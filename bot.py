import os
import re
import asyncio
from decimal import Decimal

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (Railway Variables -> DATABASE_URL)")

pool: asyncpg.Pool | None = None


# === EDIT PRODUCTS HERE (easy) ===
PRODUCTS = [
    {"key": "saint",  "title": "saint",  "price": Decimal("50.00"),  "link": "https://t.me/your_link_1"},
    {"key": "bigbob", "title": "big bob","price": Decimal("75.00"),  "link": "https://t.me/your_link_2"},
    {"key": "shenen", "title": "shenen", "price": Decimal("90.00"),  "link": "https://t.me/your_link_3"},
]


class PromoState(StatesGroup):
    waiting_code = State()


def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ"), KeyboardButton(text="ПРОФИЛЬ")],
            [KeyboardButton(text="ПОМОЩЬ"), KeyboardButton(text="РАБОТА")],
        ],
        resize_keyboard=True
    )


def ikb_city() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одесса ⚓️", callback_data="city:odesa")]
    ])


def ikb_profile_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
        [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
    ])


def ikb_products() -> InlineKeyboardMarkup:
    rows = []
    for p in PRODUCTS:
        rows.append([InlineKeyboardButton(
            text=f"{p['title']} — {p['price']}",
            callback_data=f"buy:{p['key']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def db_execute(sql: str, *args):
    assert pool is not None
    async with pool.acquire() as con:
        return await con.execute(sql, *args)


async def db_fetchrow(sql: str, *args):
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetchrow(sql, *args)


async def db_fetch(sql: str, *args):
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetch(sql, *args)


async def ensure_schema():
    # create base tables
    await db_execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        balance NUMERIC(12,2) NOT NULL DEFAULT 0,
        orders_count INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    await db_execute("""
    CREATE TABLE IF NOT EXISTS products (
        id BIGSERIAL PRIMARY KEY,
        key TEXT UNIQUE,
        title TEXT,
        price NUMERIC(12,2) NOT NULL DEFAULT 0,
        link TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    await db_execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        product_key TEXT,
        title TEXT NOT NULL,
        price NUMERIC(12,2) NOT NULL,
        link TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    await db_execute("""
    CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        amount NUMERIC(12,2) NOT NULL,
        uses_left INT NOT NULL DEFAULT 1,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    await db_execute("""
    CREATE TABLE IF NOT EXISTS promo_activations (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        code TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
        amount NUMERIC(12,2) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(user_id, code)
    );
    """)

    # ---- auto-fix old "products" schemas ----
    # If your old products table exists but misses columns, add them.
    async def col_exists(table: str, col: str) -> bool:
        r = await db_fetchrow("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_name=$1 AND column_name=$2
        """, table, col)
        return r is not None

    # rename product_name -> title (if needed)
    if await col_exists("products", "product_name") and not await col_exists("products", "title"):
        try:
            await db_execute("""ALTER TABLE products RENAME COLUMN product_name TO title;""")
        except Exception:
            pass

    # ensure title exists
    if not await col_exists("products", "title"):
        await db_execute("""ALTER TABLE products ADD COLUMN title TEXT;""")

    # ensure key exists (we use key for callbacks)
    if not await col_exists("products", "key"):
        await db_execute("""ALTER TABLE products ADD COLUMN key TEXT;""")
        try:
            await db_execute("""CREATE UNIQUE INDEX IF NOT EXISTS products_key_uq ON products(key);""")
        except Exception:
            pass

    # ensure price/link/is_active exist
    if not await col_exists("products", "price"):
        await db_execute("""ALTER TABLE products ADD COLUMN price NUMERIC(12,2) NOT NULL DEFAULT 0;""")
    if not await col_exists("products", "link"):
        await db_execute("""ALTER TABLE products ADD COLUMN link TEXT NOT NULL DEFAULT '';""")
    if not await col_exists("products", "is_active"):
        await db_execute("""ALTER TABLE products ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;""")

    # seed / sync products list
    # keep it simple: upsert by key
    for p in PRODUCTS:
        await db_execute("""
        INSERT INTO products(key, title, price, link, is_active)
        VALUES($1,$2,$3,$4,TRUE)
        ON CONFLICT (key) DO UPDATE
        SET title=EXCLUDED.title, price=EXCLUDED.price, link=EXCLUDED.link, is_active=TRUE
        """, p["key"], p["title"], p["price"], p["link"])


async def ensure_user(user_id: int):
    await db_execute("""
    INSERT INTO users(user_id) VALUES($1)
    ON CONFLICT (user_id) DO NOTHING
    """, user_id)


async def get_user(user_id: int):
    await ensure_user(user_id)
    return await db_fetchrow("SELECT user_id, balance, orders_count FROM users WHERE user_id=$1", user_id)


async def add_balance(user_id: int, amount: Decimal):
    await ensure_user(user_id)
    await db_execute("""
    UPDATE users SET balance = balance + $2
    WHERE user_id=$1
    """, user_id, amount)


async def try_buy(user_id: int, product_key: str) -> tuple[bool, str]:
    await ensure_user(user_id)

    p = await db_fetchrow("""
    SELECT key, title, price, link
    FROM products
    WHERE key=$1 AND is_active=TRUE
    """, product_key)
    if not p:
        return False, "Товар недоступен."

    async with pool.acquire() as con:
        async with con.transaction():
            u = await con.fetchrow("SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE", user_id)
            bal = Decimal(str(u["balance"]))
            price = Decimal(str(p["price"]))
            if bal < price:
                return False, f"Недостаточно средств. Баланс: {bal}, нужно: {price}"

            await con.execute("""
            UPDATE users
            SET balance = balance - $2,
                orders_count = orders_count + 1
            WHERE user_id=$1
            """, user_id, price)

            await con.execute("""
            INSERT INTO purchases(user_id, product_key, title, price, link)
            VALUES($1,$2,$3,$4,$5)
            """, user_id, p["key"], p["title"], price, p["link"])

    return True, f"✅ Куплено: {p['title']}\nСсылка: {p['link']}"


async def apply_promo(user_id: int, code: str) -> tuple[bool, str]:
    code = code.strip()
    if not code:
        return False, "Промокод пустой."

    await ensure_user(user_id)

    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow("""
            SELECT code, amount, uses_left, is_active
            FROM promo_codes
            WHERE code=$1
            FOR UPDATE
            """, code)

            if not promo or not promo["is_active"] or promo["uses_left"] <= 0:
                return False, "❌ Промокод недействителен или уже использован."

            used = await con.fetchrow("""
            SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2
            """, user_id, code)
            if used:
                return False, "❌ Этот промокод уже был активирован тобой."

            amount = Decimal(str(promo["amount"]))

            await con.execute("""
            INSERT INTO promo_activations(user_id, code, amount)
            VALUES($1,$2,$3)
            """, user_id, code, amount)

            await con.execute("""
            UPDATE promo_codes
            SET uses_left = uses_left - 1,
                is_active = CASE WHEN uses_left - 1 <= 0 THEN FALSE ELSE TRUE END
            WHERE code=$1
            """, code)

            await con.execute("""
            UPDATE users SET balance = balance + $2 WHERE user_id=$1
            """, user_id, amount)

    return True, f"✅ Промокод активирован: +{amount}"


# ========= HANDLERS =========

async def cmd_start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer("Меню", reply_markup=kb_main())


async def show_main(message: Message):
    u = await get_user(message.from_user.id)
    bal = Decimal(str(u["balance"]))
    orders = int(u["orders_count"])
    text = (
        f"Баланс: {bal}\n"
        f"Количество заказов: {orders}\n\n"
        f"Выберите город:"
    )
    await message.answer(text, reply_markup=kb_main())
    await message.answer("Города:", reply_markup=ikb_city())


async def show_profile(message: Message):
    u = await get_user(message.from_user.id)
    bal = Decimal(str(u["balance"]))
    orders = int(u["orders_count"])
    text = (
        "👤 Профиль\n\n"
        f"💰 Баланс: {bal}\n"
        f"🛍 Количество заказов: {orders}"
    )
    await message.answer(text, reply_markup=ikb_profile_actions())


async def help_msg(message: Message):
    await message.answer("Поддержка: @gskalye")


async def work_msg(message: Message):
    await message.answer("РАБОТА: скоро")


async def on_city(call: CallbackQuery):
    await call.answer()
    if call.data != "city:odesa":
        return
    await call.message.answer(
        "Вы выбрали город Одесса. Выберите товар:",
        reply_markup=ikb_products()
    )


async def on_buy(call: CallbackQuery):
    await call.answer()
    m = re.match(r"^buy:(.+)$", call.data or "")
    if not m:
        return
    key = m.group(1)
    ok, txt = await try_buy(call.from_user.id, key)
    await call.message.answer(txt)


async def on_profile_button(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if call.data == "profile:topup":
        await call.message.answer("Пополнение: скоро")
        return

    if call.data == "profile:history":
        rows = await db_fetch("""
        SELECT title, link, created_at
        FROM purchases
        WHERE user_id=$1
        ORDER BY created_at DESC
        LIMIT 50
        """, call.from_user.id)
        if not rows:
            await call.message.answer("История пуста.")
            return
        text = "🧾 История покупок:\n\n"
        for r in rows:
            text += f"{r['title']}\n{r['link']}\n\n"
        await call.message.answer(text.strip())
        return

    if call.data == "profile:promo":
        await state.set_state(PromoState.waiting_code)
        await call.message.answer("Введите промокод одним сообщением (например: TEST300)")
        return


async def promo_text_input(message: Message, state: FSMContext):
    code = message.text.strip()
    ok, txt = await apply_promo(message.from_user.id, code)
    await message.answer(txt)
    await state.clear()


async def cmd_promo(message: Message):
    # /promo CODE
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /promo ВАШ_ПРОМОКОД")
        return
    ok, txt = await apply_promo(message.from_user.id, parts[1])
    await message.answer(txt)


async def cmd_addpromo(message: Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return

    # /addpromo CODE AMOUNT USES
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Формат: /addpromo CODE AMOUNT USES")
        return

    code = parts[1].strip()
    try:
        amount = Decimal(parts[2])
        uses = int(parts[3])
        if uses <= 0:
            raise ValueError
    except Exception:
        await message.answer("Неверные данные. Пример: /addpromo TEST300 300 1")
        return

    await db_execute("""
    INSERT INTO promo_codes(code, amount, uses_left, is_active)
    VALUES($1,$2,$3,TRUE)
    ON CONFLICT (code) DO UPDATE
    SET amount=EXCLUDED.amount,
        uses_left=EXCLUDED.uses_left,
        is_active=TRUE
    """, code, amount, uses)

    await message.answer(f"✅ Промокод создан: {code} (+{amount}, uses={uses})")


async def main():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await ensure_schema()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_promo, Command("promo"))
    dp.message.register(cmd_addpromo, Command("addpromo"))

    dp.message.register(show_main, F.text == "ГЛАВНАЯ")
    dp.message.register(show_profile, F.text == "ПРОФИЛЬ")
    dp.message.register(help_msg, F.text == "ПОМОЩЬ")
    dp.message.register(work_msg, F.text == "РАБОТА")

    dp.callback_query.register(on_city, F.data.startswith("city:"))
    dp.callback_query.register(on_buy, F.data.startswith("buy:"))
    dp.callback_query.register(on_profile_button, F.data.startswith("profile:"))

    dp.message.register(promo_text_input, PromoState.waiting_code)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
