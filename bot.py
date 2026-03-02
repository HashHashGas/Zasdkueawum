import os
import asyncio
import decimal
import asyncpg

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ================== ENV ==================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
ADMIN_ID_RAW = (os.getenv("ADMIN_ID") or "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

UAH = "₴"


def fmt_uah(amount: decimal.Decimal) -> str:
    return f"{amount:.2f} {UAH}"


def normalize_code(raw: str) -> str:
    return (raw or "").strip()


def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


# ================== TEXTS ==================
MAIN_TEXT_TEMPLATE = """✋🏻 Здравствуй! Кавалер 🎩
👑Вы находитесь в Cavalier Shop👑

✍🏻Кратко о нашем сервисе

°Готовые позиции
°Горячие позиции
°Превосходное качество товара
°ОПТ
°Разновидные способы оплаты 
°Отправки NovaPost 🇺🇦 
°Оператор/Сапорт в сети 24/7 

Актуальные ссылки 

Бот : 
@CavalierShopBot

💬Чат : 
https://t.me/+HvuVKZkR2-03MzBi

🥇Отзывы :
https://t.me/+HvuVKZkR2-03MzBi

Оператор/Сапорт : 
@gskalye

🏦Баланс : {balance}
🛍️Количество заказов : {orders}
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦Баланс : {balance}
🛍️Количество заказов : {orders}
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
@gskalye
"""

WORK_TEXT = "X"  # заменишь сам

ODESA_TEXT = "✅ Вы выбрали город Одесса.\nВыберите товар:"
ITEM_TEXT = "📦 Вы выбрали товар: {title}\n\n{desc}\n\nНажмите «Район» для продолжения."
PAY_TEXT = "📍 Выберите способ оплаты:"


# ================== KEYBOARDS ==================
def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def inline_main_city() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Одесса", callback_data="city:odesa")]]
    )


def inline_profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
        ]
    )


def inline_district_button(prod_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Район", callback_data=f"odesa:district:{prod_key}")]]
    )


def inline_pay_methods(prod_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Балансом", callback_data=f"pay:balance:{prod_key}")],
            [InlineKeyboardButton(text="Картой", callback_data=f"pay:card:{prod_key}")],
        ]
    )


def inline_products_menu(products: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        price = decimal.Decimal(p["price"])
        title = str(p["title"])
        rows.append([InlineKeyboardButton(text=f"{title} — {fmt_uah(price)}", callback_data=f"odesa:item:{p['key']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================== DB ==================
pool: asyncpg.Pool | None = None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            item_name TEXT NOT NULL,
            link TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            amount NUMERIC(12,2) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            uses_left INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS promo_activations (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            code TEXT NOT NULL REFERENCES promo_codes(code),
            activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, code)
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            key TEXT PRIMARY KEY,
            city TEXT NOT NULL DEFAULT 'odesa',
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            link TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        # дефолтные 3 товара (чтобы сразу были кнопки)
        await con.execute("""
        INSERT INTO products(key, city, title, description, price, link, is_active)
        VALUES
          ('saint',  'odesa', 'saint',  'Описание товара saint',  0, '', TRUE),
          ('bigbob', 'odesa', 'big bob','Описание товара big bob', 0, '', TRUE),
          ('shenen', 'odesa', 'shenen', 'Описание товара shenen', 0, '', TRUE)
        ON CONFLICT (key) DO NOTHING
        """)


async def ensure_user(user_id: int) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )


async def get_user_stats(user_id: int) -> tuple[decimal.Decimal, int]:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id,
        )
    if not row:
        return decimal.Decimal("0.00"), 0
    return decimal.Decimal(row["balance"]), int(row["orders_count"])


async def set_user_balance(user_id: int, amount: decimal.Decimal) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET balance=$2 WHERE user_id=$1", user_id, amount)


async def activate_promo(user_id: int, raw_code: str) -> tuple[bool, str]:
    code = normalize_code(raw_code)
    if not code:
        return False, "❌ Введи промокод текстом."

    assert pool is not None

    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow(
                """
                SELECT code, amount, is_active, uses_left
                FROM promo_codes
                WHERE upper(code) = upper($1)
                FOR UPDATE
                """,
                code,
            )
            if not promo or not promo["is_active"] or int(promo["uses_left"]) <= 0:
                return False, "❌ Промокод недействителен."

            real_code = str(promo["code"])
            amount = decimal.Decimal(promo["amount"])

            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                user_id, real_code
            )
            if used:
                return False, "❌ Ты уже активировал этот промокод."

            await con.execute(
                "INSERT INTO promo_activations(user_id, code) VALUES($1, $2)",
                user_id, real_code
            )
            await con.execute(
                "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
                real_code
            )
            await con.execute(
                "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                user_id, amount
            )

    return True, f"✅ Промокод активирован!\n🏦 Начислено: {fmt_uah(amount)}"


async def get_history(user_id: int) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT item_name, link, created_at FROM purchases WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20",
            user_id,
        )
    return rows


async def get_city_products(city: str) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT key, title, description, price, link
            FROM products
            WHERE city=$1 AND is_active=TRUE
            ORDER BY created_at ASC
            LIMIT 30
            """,
            city,
        )
    return rows


async def get_product(prod_key: str) -> asyncpg.Record | None:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """
            SELECT key, title, description, price, link, city, is_active
            FROM products
            WHERE key=$1
            """,
            prod_key,
        )
    return row


async def buy_with_balance(user_id: int, prod_key: str) -> tuple[bool, str]:
    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            p = await con.fetchrow(
                """
                SELECT key, title, price, link, is_active
                FROM products
                WHERE key=$1
                FOR UPDATE
                """,
                prod_key,
            )
            if not p or not p["is_active"]:
                return False, "❌ Товар недоступен."

            price = decimal.Decimal(p["price"])
            title = str(p["title"])
            link = str(p["link"] or "").strip()

            u = await con.fetchrow(
                "SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE",
                user_id
            )
            if not u:
                return False, "❌ Профиль не найден."

            bal = decimal.Decimal(u["balance"])
            if bal < price:
                return False, f"❌ Недостаточно средств.\nНужно: {fmt_uah(price)}\nУ тебя: {fmt_uah(bal)}"

            new_bal = bal - price

            await con.execute("UPDATE users SET balance=$2, orders_count=orders_count+1 WHERE user_id=$1", user_id, new_bal)
            await con.execute(
                "INSERT INTO purchases(user_id, item_name, link) VALUES($1, $2, $3)",
                user_id, title, link if link else "—"
            )

    if not link:
        return True, f"✅ Оплачено балансом: {title}\n⚠️ Но ссылка не задана админом.\nНапиши администратору."
    return True, f"✅ Оплачено балансом: {title}\n🔗 Твоя ссылка:\n{link}"


# ================== FSM ==================
class PromoStates(StatesGroup):
    waiting_code = State()


# ================== BOT ==================
dp = Dispatcher(storage=MemoryStorage())


async def render_main_text(user_id: int) -> str:
    await ensure_user(user_id)
    bal, orders = await get_user_stats(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=fmt_uah(bal), orders=orders)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=bottom_menu())


@dp.message(F.text.contains("ГЛАВНАЯ"))
async def btn_main(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=inline_main_city())


@dp.message(F.text.contains("ПРОФИЛЬ"))
async def btn_profile(message: Message):
    await ensure_user(message.from_user.id)
    bal, orders = await get_user_stats(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=fmt_uah(bal), orders=orders)
    await message.answer(text, reply_markup=inline_profile_menu())


@dp.message(F.text.contains("ПОМОЩЬ"))
async def btn_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=bottom_menu())


@dp.message(F.text.contains("РАБОТА"))
async def btn_work(message: Message):
    await message.answer(WORK_TEXT, reply_markup=bottom_menu())


# ========= CALLBACKS =========
@dp.callback_query(F.data == "city:odesa")
async def cb_city_odesa(call: CallbackQuery):
    await call.answer()
    prods = await get_city_products("odesa")
    if not prods:
        await call.message.answer("Пока нет товаров для Одессы.")
        return
    await call.message.answer(ODESA_TEXT, reply_markup=inline_products_menu(prods))


@dp.callback_query(F.data.startswith("odesa:item:"))
async def cb_odesa_item(call: CallbackQuery):
    await call.answer()
    prod_key = call.data.split(":")[-1]
    p = await get_product(prod_key)
    if not p or not p["is_active"]:
        await call.message.answer("Товар не найден.")
        return
    title = str(p["title"])
    desc = str(p["description"] or "")
    await call.message.answer(ITEM_TEXT.format(title=title, desc=desc), reply_markup=inline_district_button(prod_key))


@dp.callback_query(F.data.startswith("odesa:district:"))
async def cb_odesa_district(call: CallbackQuery):
    await call.answer()
    prod_key = call.data.split(":")[-1]
    await call.message.answer(PAY_TEXT, reply_markup=inline_pay_methods(prod_key))


@dp.callback_query(F.data.startswith("pay:balance:"))
async def cb_pay_balance(call: CallbackQuery):
    await call.answer()
    await ensure_user(call.from_user.id)
    prod_key = call.data.split(":")[-1]
    ok, txt = await buy_with_balance(call.from_user.id, prod_key)
    await call.message.answer(txt)
    # чтобы сразу было видно обновление
    text = await render_main_text(call.from_user.id)
    await call.message.answer(text, reply_markup=inline_main_city())


@dp.callback_query(F.data.startswith("pay:card:"))
async def cb_pay_card(call: CallbackQuery):
    await call.answer()
    prod_key = call.data.split(":")[-1]
    # тут потом будет интеграция платежки через API (одна точка)
    await call.message.answer(f"💳 Оплата картой скоро будет подключена.\nТовар: {prod_key}")


@dp.callback_query(F.data == "profile:topup")
async def cb_profile_topup(call: CallbackQuery):
    await call.answer()
    await call.message.answer("💳 Пополнение скоро подключим.")


@dp.callback_query(F.data == "profile:promo")
async def cb_profile_promo(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoStates.waiting_code)
    await call.message.answer("🎟 Введи промокод одним сообщением:")


@dp.message(PromoStates.waiting_code)
async def promo_entered(message: Message, state: FSMContext):
    await ensure_user(message.from_user.id)
    ok, msg = await activate_promo(message.from_user.id, message.text)
    await message.answer(msg)
    await state.clear()
    # обновим профиль/главную инфу после промо
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=inline_main_city())


@dp.message(F.text.startswith("/promo"))
async def cmd_promo(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /promo ВАШ_ПРОМОКОД")
        return
    await ensure_user(message.from_user.id)
    ok, msg = await activate_promo(message.from_user.id, parts[1])
    await message.answer(msg)
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=inline_main_city())


@dp.callback_query(F.data == "profile:history")
async def cb_profile_history(call: CallbackQuery):
    await call.answer()
    rows = await get_history(call.from_user.id)
    if not rows:
        await call.message.answer("История пуста.")
        return

    text = "🧾 История покупок:\n\n"
    for r in rows:
        dt = r["created_at"].strftime("%Y-%m-%d %H:%M")
        text += f"• {r['item_name']} ({dt})\n{r['link']}\n\n"
    await call.message.answer(text)


# ========= ADMIN (товары) =========
@dp.message(F.text.startswith("/addproduct"))
async def admin_addproduct(message: Message):
    if not is_admin(message.from_user.id):
        return
    # Формат:
    # /addproduct key;title;price;description;link
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer("Формат: /addproduct key;title;price;description;link")
        return
    parts = [p.strip() for p in raw[1].split(";", 4)]
    if len(parts) < 5:
        await message.answer("Формат: /addproduct key;title;price;description;link")
        return

    key, title, price_s, desc, link = parts
    try:
        price = decimal.Decimal(price_s.replace(",", "."))
    except Exception:
        await message.answer("Цена должна быть числом, например 300 или 300.50")
        return

    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO products(key, city, title, description, price, link, is_active)
            VALUES($1, 'odesa', $2, $3, $4, $5, TRUE)
            ON CONFLICT (key) DO UPDATE SET
              title=EXCLUDED.title,
              description=EXCLUDED.description,
              price=EXCLUDED.price,
              link=EXCLUDED.link,
              is_active=TRUE
            """,
            key, title, desc, price, link
        )
    await message.answer(f"✅ Товар сохранён: {key} — {title} — {fmt_uah(price)}")


@dp.message(F.text.startswith("/setprice"))
async def admin_setprice(message: Message):
    if not is_admin(message.from_user.id):
        return
    # /setprice key 300
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /setprice key 300.00")
        return
    key = parts[1].strip()
    try:
        price = decimal.Decimal(parts[2].replace(",", "."))
    except Exception:
        await message.answer("Цена должна быть числом.")
        return

    assert pool is not None
    async with pool.acquire() as con:
        res = await con.execute("UPDATE products SET price=$2 WHERE key=$1", key, price)
    await message.answer(f"✅ Цена обновлена: {key} — {fmt_uah(price)}")


@dp.message(F.text.startswith("/setlink"))
async def admin_setlink(message: Message):
    if not is_admin(message.from_user.id):
        return
    # /setlink key https://...
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /setlink key https://link")
        return
    key = parts[1].strip()
    link = parts[2].strip()

    assert pool is not None
    async with pool.acquire() as con:
        await con.execute("UPDATE products SET link=$2 WHERE key=$1", key, link)
    await message.answer(f"✅ Ссылка обновлена: {key}")


@dp.message(F.text.startswith("/setdesc"))
async def admin_setdesc(message: Message):
    if not is_admin(message.from_user.id):
        return
    # /setdesc key текст...
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /setdesc key описание")
        return
    key = parts[1].strip()
    desc = parts[2].strip()

    assert pool is not None
    async with pool.acquire() as con:
        await con.execute("UPDATE products SET description=$2 WHERE key=$1", key, desc)
    await message.answer(f"✅ Описание обновлено: {key}")


@dp.message(F.text.startswith("/delproduct"))
async def admin_delproduct(message: Message):
    if not is_admin(message.from_user.id):
        return
    # /delproduct key
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /delproduct key")
        return
    key = parts[1].strip()

    assert pool is not None
    async with pool.acquire() as con:
        await con.execute("UPDATE products SET is_active=FALSE WHERE key=$1", key)
    await message.answer(f"✅ Товар выключен: {key}")


@dp.message(F.text.startswith("/products"))
async def admin_products(message: Message):
    if not is_admin(message.from_user.id):
        return
    prods = await get_city_products("odesa")
    if not prods:
        await message.answer("Товаров нет.")
        return
    txt = "📦 Товары (odesa):\n\n"
    for p in prods:
        txt += f"- {p['key']}: {p['title']} — {fmt_uah(decimal.Decimal(p['price']))}\n"
    await message.answer(txt)


# ================== RUN ==================
async def main():
    await db_init()
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
