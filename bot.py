import os
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
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


# =======================
# ENV
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_IDS = os.getenv("ADMIN_IDS", "").strip()  # пример: "123,456"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing (set BOT_TOKEN in Railway Variables)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (add Postgres + set DATABASE_URL in Railway Variables)")

ADMIN_SET = set()
if ADMIN_IDS:
    for x in ADMIN_IDS.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_SET.add(int(x))


# =======================
# UI TEXTS
# =======================
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

🏦Баланс : <b>{balance}</b>
🛍️Количество заказов : <b>{orders}</b>
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту:
@gskalye"""

WORK_TEXT = "A"

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦 Баланс: <b>{balance}</b>
🛍️ Количество заказов: <b>{orders}</b>

⬇️ Действия профиля:"""


# =======================
# BUTTON LABELS (нижняя панель)
# =======================
BTN_MAIN = "ГЛАВНАЯ ⚪"
BTN_PROFILE = "ПРОФИЛЬ 👤"
BTN_HELP = "ПОМОЩЬ 💬"
BTN_WORK = "РАБОТА 🛠️"


def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MAIN), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_WORK)],
        ],
        resize_keyboard=True
    )


# =======================
# INLINE KEYBOARDS
# =======================
def kb_main_with_city() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одесса ⚓", callback_data="city:odessa")]
    ])


def kb_profile_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promocode")],
        [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
    ])


def kb_city_odessa_products() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1) saint", callback_data="product:odessa:saint")],
        [InlineKeyboardButton(text="2) big bob", callback_data="product:odessa:big_bob")],
        [InlineKeyboardButton(text="3) shenen", callback_data="product:odessa:shenen")],
    ])


def kb_product_buy(product_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Купить", callback_data=f"buy:{product_code}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="city:odessa")],
    ])


def kb_back_to_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile:open")]
    ])


# =======================
# FSM
# =======================
class PromoStates(StatesGroup):
    waiting_code = State()


# =======================
# DB
# =======================
pool: asyncpg.Pool | None = None
bot_ref: Bot | None = None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        # Базовые таблицы
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            description TEXT NOT NULL,
            link TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_code TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            link TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            amount NUMERIC(12,2) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            uses_left INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS promo_activations (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            code TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, code)
        );
        """)

        # ДОБАВЛЯЕМ колонки для синхронизации сообщений (не ломает существующую БД)
        await con.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS main_chat_id BIGINT,
            ADD COLUMN IF NOT EXISTS main_message_id BIGINT,
            ADD COLUMN IF NOT EXISTS profile_chat_id BIGINT,
            ADD COLUMN IF NOT EXISTS profile_message_id BIGINT;
        """)

        # Если товаров нет — добавим дефолт 3
        cnt = await con.fetchval("SELECT COUNT(*) FROM products;")
        if int(cnt) == 0:
            defaults = [
                ("odessa", "saint", "saint", Decimal("100.00"), "Описание товара saint (поменяешь сам)", "https://example.com/saint"),
                ("odessa", "big_bob", "big bob", Decimal("150.00"), "Описание товара big bob (поменяешь сам)", "https://example.com/big_bob"),
                ("odessa", "shenen", "shenen", Decimal("200.00"), "Описание товара shenen (поменяешь сам)", "https://example.com/shenen"),
            ]
            for city, code, name, price, desc, link in defaults:
                await con.execute(
                    """
                    INSERT INTO products (city, code, name, price, description, link)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    city, code, name, price, desc, link
                )


async def ensure_user(user_id: int) -> None:
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )


async def get_user_profile(user_id: int) -> dict:
    await ensure_user(user_id)
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id
        )
    bal = Decimal(row["balance"]) if row else Decimal("0.00")
    ords = int(row["orders_count"]) if row else 0
    return {"balance": f"{bal:.2f}", "orders": ords}


async def render_main_text(user_id: int) -> str:
    prof = await get_user_profile(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=prof["balance"], orders=prof["orders"])


async def save_message_refs(user_id: int, kind: str, chat_id: int, message_id: int) -> None:
    # kind: "main" or "profile"
    col_chat = "main_chat_id" if kind == "main" else "profile_chat_id"
    col_msg = "main_message_id" if kind == "main" else "profile_message_id"
    async with pool.acquire() as con:
        await con.execute(
            f"UPDATE users SET {col_chat}=$1, {col_msg}=$2 WHERE user_id=$3",
            chat_id, message_id, user_id
        )


async def update_main_message(user_id: int) -> None:
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT main_chat_id, main_message_id FROM users WHERE user_id=$1",
            user_id
        )
    if not row or not row["main_chat_id"] or not row["main_message_id"]:
        return

    text = await render_main_text(user_id)
    try:
        await bot_ref.edit_message_text(
            chat_id=int(row["main_chat_id"]),
            message_id=int(row["main_message_id"]),
            text=text,
            reply_markup=kb_main_with_city(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        # если сообщение старое/удалено — просто молча
        return


async def update_profile_message(user_id: int) -> None:
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT profile_chat_id, profile_message_id FROM users WHERE user_id=$1",
            user_id
        )
    if not row or not row["profile_chat_id"] or not row["profile_message_id"]:
        return

    prof = await get_user_profile(user_id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=prof["balance"], orders=prof["orders"])
    try:
        await bot_ref.edit_message_text(
            chat_id=int(row["profile_chat_id"]),
            message_id=int(row["profile_message_id"]),
            text=text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        return


async def get_product_by_code(code: str):
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT city, code, name, price, description, link
            FROM products
            WHERE code=$1 AND is_active=TRUE
            """,
            code
        )


# =======================
# SENDERS (main/profile)
# =======================
async def send_main_message(message: Message):
    user_id = message.from_user.id
    await ensure_user(user_id)
    text = await render_main_text(user_id)
    msg = await message.answer(
        text,
        reply_markup=kb_main_with_city(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await save_message_refs(user_id, "main", msg.chat.id, msg.message_id)


async def send_profile_message(message: Message | CallbackQuery):
    user_id = message.from_user.id
    await ensure_user(user_id)
    prof = await get_user_profile(user_id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=prof["balance"], orders=prof["orders"])

    if isinstance(message, Message):
        msg = await message.answer(
            text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await save_message_refs(user_id, "profile", msg.chat.id, msg.message_id)
    else:
        msg = await message.message.answer(
            text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await save_message_refs(user_id, "profile", msg.chat.id, msg.message_id)
        await message.answer()


# =======================
# HANDLERS
# =======================
async def start_handler(message: Message):
    # /start: БЕЗ инлайн Одесса
    await ensure_user(message.from_user.id)
    text = await render_main_text(message.from_user.id)
    await message.answer(
        text,
        reply_markup=main_reply_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


async def main_handler(message: Message):
    # "ГЛАВНАЯ": сообщение + инлайн Одесса (и сохраняем id для синхры)
    await send_main_message(message)


async def profile_handler(message: Message):
    await send_profile_message(message)


async def help_handler(message: Message):
    await message.answer(
        HELP_TEXT,
        reply_markup=main_reply_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


async def work_handler(message: Message):
    await message.answer(
        WORK_TEXT,
        reply_markup=main_reply_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ---------- INLINE: CITY ----------
async def on_city(callback: CallbackQuery):
    if callback.data == "city:odessa":
        await callback.message.answer(
            "✅ Вы выбрали город <b>Одесса</b>. Выберите товар:",
            reply_markup=kb_city_odessa_products(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer()


# ---------- INLINE: PRODUCT ----------
async def on_product(callback: CallbackQuery):
    # product:odessa:saint
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    _, city, code = parts
    product = await get_product_by_code(code)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = (
        f"📦 <b>{product['name']}</b>\n"
        f"🏙 Город: <b>{city.capitalize()}</b>\n"
        f"💳 Цена: <b>{Decimal(product['price']):.2f}</b>\n\n"
        f"{product['description']}\n\n"
        f"🔗 Ссылка/инфо: {product['link']}"
    )

    await callback.message.answer(
        text,
        reply_markup=kb_product_buy(product_code=code),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()


# ---------- INLINE: BUY ----------
async def on_buy(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 2:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    _, code = parts
    user_id = callback.from_user.id

    product = await get_product_by_code(code)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    await ensure_user(user_id)

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                "SELECT balance FROM users WHERE user_id=$1 FOR UPDATE",
                user_id
            )
            balance = Decimal(row["balance"])
            price = Decimal(product["price"])

            if balance < price:
                await callback.answer("Недостаточно средств 😔", show_alert=True)
                return

            await con.execute(
                """
                UPDATE users
                SET balance = balance - $1,
                    orders_count = orders_count + 1
                WHERE user_id=$2
                """,
                price, user_id
            )

            await con.execute(
                """
                INSERT INTO purchases (user_id, product_code, product_name, price, link)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id, product["code"], product["name"], price, product["link"]
            )

    # Сообщение о покупке
    await callback.message.answer(
        f"✅ Покупка успешна!\n\n📦 <b>{product['name']}</b>\n💳 Списано: <b>{Decimal(product['price']):.2f}</b>\n🔗 {product['link']}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # Синхронизируем главную/профиль
    await update_main_message(user_id)
    await update_profile_message(user_id)

    await callback.answer()


# ---------- PROFILE INLINE ----------
async def profile_open(callback: CallbackQuery):
    await send_profile_message(callback)


async def profile_topup(callback: CallbackQuery):
    await callback.message.answer(
        "💳 Пополнение баланса временно в разработке.",
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()


async def profile_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    await ensure_user(user_id)

    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT product_name, link, price, created_at
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 50
            """,
            user_id
        )

    if not rows:
        await callback.message.answer(
            "🧾 История покупок пуста.",
            reply_markup=kb_back_to_profile(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer()
        return

    lines = ["🧾 <b>История покупок:</b>\n"]
    for r in rows:
        lines.append(f"• <b>{r['product_name']}</b> — {Decimal(r['price']):.2f}\n{r['link']}\n")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()


async def profile_promocode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_code)
    await callback.message.answer(
        "🎟 Введите промокод одним сообщением (как в базе).",
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()


async def promo_input(message: Message, state: FSMContext):
    code_in = (message.text or "").strip()
    code_in = " ".join(code_in.split())
    if not code_in:
        await message.answer("❌ Пустой промокод. Введите ещё раз.")
        return

    user_id = message.from_user.id
    await ensure_user(user_id)

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                """
                SELECT code, amount, uses_left
                FROM promo_codes
                WHERE lower(code) = lower($1)
                  AND is_active = TRUE
                  AND uses_left > 0
                FOR UPDATE
                """,
                code_in
            )
            if not row:
                await message.answer("❌ Промокод недействителен или уже использован.")
                return

            already = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                user_id, row["code"]
            )
            if already:
                await message.answer("❌ Вы уже активировали этот промокод.")
                return

            amount = Decimal(row["amount"])

            await con.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id=$2",
                amount, user_id
            )
            await con.execute(
                "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
                row["code"]
            )
            await con.execute(
                "INSERT INTO promo_activations (user_id, code, amount) VALUES ($1, $2, $3)",
                user_id, row["code"], amount
            )

    await state.clear()

    # ЯВНОЕ подтверждение (это ты просил)
    await message.answer(
        f"✅ Промокод <b>{row['code']}</b> активирован!\n➕ Начислено: <b>{amount:.2f}</b>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # Синхронизируем главную/профиль
    await update_main_message(user_id)
    await update_profile_message(user_id)

    # И отдельно покажем профиль (как раньше) — но уже синхронный
    await send_profile_message(message)


# =======================
# ADMIN (товары)
# =======================
def is_admin(user_id: int) -> bool:
    return (not ADMIN_SET) or (user_id in ADMIN_SET)  # если ADMIN_IDS не задан — считаем тебя админом


async def admin_addproduct(message: Message):
    if not is_admin(message.from_user.id):
        return

    # Формат:
    # /addproduct odessa code "Название" 123.45 https://link Описание...
    # Название можно без кавычек, но лучше с кавычками если пробелы.
    text = message.text or ""
    parts = text.split(maxsplit=5)
    if len(parts) < 6:
        await message.answer('❌ Формат:\n/addproduct odessa code "Название" 123.45 https://link Описание...')
        return

    _, city, code, name, price_str, rest = parts
    # rest = "https://link Описание..."
    rest_parts = rest.split(maxsplit=1)
    if len(rest_parts) < 2:
        await message.answer('❌ Формат:\n/addproduct odessa code "Название" 123.45 https://link Описание...')
        return
    link = rest_parts[0].strip()
    desc = rest_parts[1].strip()

    name = name.strip().strip('"').strip("'")
    try:
        price = Decimal(price_str)
    except Exception:
        await message.answer("❌ Цена должна быть числом типа 100 или 100.00")
        return

    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO products (city, code, name, price, description, link, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE)
            ON CONFLICT (code) DO UPDATE
            SET city=EXCLUDED.city,
                name=EXCLUDED.name,
                price=EXCLUDED.price,
                description=EXCLUDED.description,
                link=EXCLUDED.link,
                is_active=TRUE
            """,
            city.lower(), code, name, price, desc, link
        )

    await message.answer(f"✅ Товар добавлен/обновлён: <b>{code}</b>", parse_mode="HTML")


async def admin_listproducts(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    city = parts[1].strip().lower() if len(parts) > 1 else "odessa"

    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT code, name, price, is_active
            FROM products
            WHERE city=$1
            ORDER BY id ASC
            """,
            city
        )

    if not rows:
        await message.answer("Пусто.")
        return

    lines = [f"📦 <b>Товары ({city}):</b>\n"]
    for r in rows:
        status = "✅" if r["is_active"] else "❌"
        lines.append(f"{status} <b>{r['code']}</b> — {r['name']} — {Decimal(r['price']):.2f}")
    await message.answer("\n".join(lines), parse_mode="HTML")


async def admin_delproduct(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Формат: /delproduct code")
        return
    code = parts[1].strip()

    async with pool.acquire() as con:
        await con.execute(
            "UPDATE products SET is_active=FALSE WHERE code=$1",
            code
        )

    await message.answer(f"✅ Товар отключён: <b>{code}</b>", parse_mode="HTML")


# =======================
# MAIN
# =======================
async def main():
    global bot_ref
    await db_init()

    bot = Bot(token=BOT_TOKEN)
    bot_ref = bot

    dp = Dispatcher(storage=MemoryStorage())

    # commands / texts
    dp.message.register(start_handler, CommandStart())
    dp.message.register(main_handler, F.text == BTN_MAIN)
    dp.message.register(profile_handler, F.text == BTN_PROFILE)
    dp.message.register(help_handler, F.text == BTN_HELP)
    dp.message.register(work_handler, F.text == BTN_WORK)

    # admin commands
    dp.message.register(admin_addproduct, Command("addproduct"))
    dp.message.register(admin_listproducts, Command("listproducts"))
    dp.message.register(admin_delproduct, Command("delproduct"))

    # FSM promo input
    dp.message.register(promo_input, PromoStates.waiting_code)

    # callbacks
    dp.callback_query.register(on_city, F.data.startswith("city:"))
    dp.callback_query.register(on_product, F.data.startswith("product:"))
    dp.callback_query.register(on_buy, F.data.startswith("buy:"))

    dp.callback_query.register(profile_open, F.data == "profile:open")
    dp.callback_query.register(profile_topup, F.data == "profile:topup")
    dp.callback_query.register(profile_history, F.data == "profile:history")
    dp.callback_query.register(profile_promocode, F.data == "profile:promocode")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
