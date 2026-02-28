import os
import asyncio
from decimal import Decimal
from typing import Optional, Dict, Any, List

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing (set BOT_TOKEN in Railway Variables)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (add Postgres + set DATABASE_URL in Railway Variables)")


# =======================
# UI TEXTS (НЕ ТРОГАЮ КАК ТЫ ПИСАЛ, только ссылки/ник)
# =======================
MAIN_TEXT = """✋🏻 Здравствуй! Кавалер 🎩
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

🏦Баланс :
🛍️Количество заказов :
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту:
@gskalye"""

WORK_TEXT = "A"  # ты просил одну букву, потом заменишь сам

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
    # ВАЖНО: /start без этой кнопки. Она только в "ГЛАВНАЯ".
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
    # 3 кнопки, легко менять — просто поменяешь список ниже в PRODUCTS_DEFAULT
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
pool: Optional[asyncpg.Pool] = None


PRODUCTS_DEFAULT = [
    # city, code, name, price, description, link
    ("odessa", "saint", "saint", Decimal("100.00"), "Описание товара saint (поменяешь сам)", "https://example.com/saint"),
    ("odessa", "big_bob", "big bob", Decimal("150.00"), "Описание товара big bob (поменяешь сам)", "https://example.com/big_bob"),
    ("odessa", "shenen", "shenen", Decimal("200.00"), "Описание товара shenen (поменяешь сам)", "https://example.com/shenen"),
]


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

        # закидываем дефолт товары только если таблица пустая
        cnt = await con.fetchval("SELECT COUNT(*) FROM products;")
        if int(cnt) == 0:
            for city, code, name, price, desc, link in PRODUCTS_DEFAULT:
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
            """
            INSERT INTO users (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id
        )


async def get_user_profile(user_id: int) -> Dict[str, Any]:
    await ensure_user(user_id)
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id
        )
    balance = row["balance"] if row else Decimal("0.00")
    orders = row["orders_count"] if row else 0
    return {"balance": f"{Decimal(balance):.2f}", "orders": int(orders)}


async def get_product_by_code(product_code: str) -> Optional[asyncpg.Record]:
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT code, name, price, description, link
            FROM products
            WHERE code=$1 AND is_active=TRUE
            """,
            product_code
        )


# =======================
# BOT LOGIC
# =======================
async def send_profile(message_or_query, user_id: int) -> None:
    prof = await get_user_profile(user_id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=prof["balance"], orders=prof["orders"])

    if isinstance(message_or_query, Message):
        await message_or_query.answer(
            text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        # CallbackQuery
        await message_or_query.message.answer(
            text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await message_or_query.answer()


# =======================
# HANDLERS
# =======================
async def start_handler(message: Message):
    # /start: БЕЗ инлайн Одесса
    await ensure_user(message.from_user.id)
    await message.answer(
        MAIN_TEXT,
        reply_markup=main_reply_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


async def main_handler(message: Message):
    # "ГЛАВНАЯ": ТО ЖЕ сообщение + инлайн Одесса
    await ensure_user(message.from_user.id)
    await message.answer(
        MAIN_TEXT,
        reply_markup=kb_main_with_city(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


async def profile_handler(message: Message):
    await send_profile(message, message.from_user.id)


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
    data = callback.data or ""
    if data == "city:odessa":
        # ВАЖНО: При нажатии Одесса — отдельное сообщение + 3 кнопки. Старые кнопки не трогаем.
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
    # buy:saint
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
        # блокируем юзера на время списания (чтобы не купить два раза одновременно)
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE",
            user_id
        )
        balance = Decimal(row["balance"])
        price = Decimal(product["price"])

        if balance < price:
            await callback.answer("Недостаточно средств 😔", show_alert=True)
            return

        # списываем баланс + увеличиваем orders_count
        await con.execute(
            """
            UPDATE users
            SET balance = balance - $1,
                orders_count = orders_count + 1
            WHERE user_id=$2
            """,
            price, user_id
        )

        # пишем историю покупок
        await con.execute(
            """
            INSERT INTO purchases (user_id, product_code, product_name, price, link)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, product["code"], product["name"], price, product["link"]
        )

    await callback.message.answer(
        f"✅ Покупка успешна!\n\n📦 <b>{product['name']}</b>\n💳 Списано: <b>{price:.2f}</b>\n🔗 {product['link']}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()


# ---------- PROFILE INLINE ----------
async def profile_open(callback: CallbackQuery):
    await send_profile(callback, callback.from_user.id)


async def profile_topup(callback: CallbackQuery):
    # заглушка (потом подключишь оплату)
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
            LIMIT 20
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
    code = (message.text or "").strip()
    code = " ".join(code.split())  # убираем лишние пробелы

    if not code:
        await message.answer("❌ Пустой промокод. Введите ещё раз.")
        return

    user_id = message.from_user.id
    await ensure_user(user_id)

    async with pool.acquire() as con:
        # ищем промо без зависимости от регистра
        row = await con.fetchrow(
            """
            SELECT code, amount, uses_left
            FROM promo_codes
            WHERE lower(code) = lower($1)
              AND is_active = TRUE
              AND uses_left > 0
            """,
            code
        )

        if not row:
            await message.answer("❌ Промокод недействителен или уже использован.")
            return

        # защита: один и тот же промо нельзя активировать одному юзеру 2 раза
        already = await con.fetchval(
            "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
            user_id, row["code"]
        )
        if already:
            await message.answer("❌ Вы уже активировали этот промокод.")
            return

        amount = Decimal(row["amount"])

        # начисляем баланс
        await con.execute(
            "UPDATE users SET balance = balance + $1 WHERE user_id=$2",
            amount, user_id
        )

        # списываем использование промо
        await con.execute(
            "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
            row["code"]
        )

        # лог активации
        await con.execute(
            """
            INSERT INTO promo_activations (user_id, code, amount)
            VALUES ($1, $2, $3)
            """,
            user_id, row["code"], amount
        )

    await state.clear()
    await message.answer(f"✅ Промокод активирован! Начислено: <b>{amount:.2f}</b>", parse_mode="HTML")
    # сразу покажем профиль
    await send_profile(message, user_id)


# =======================
# MAIN
# =======================
async def main():
    await db_init()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # commands / texts
    dp.message.register(start_handler, CommandStart())
    dp.message.register(main_handler, F.text == BTN_MAIN)
    dp.message.register(profile_handler, F.text == BTN_PROFILE)
    dp.message.register(help_handler, F.text == BTN_HELP)
    dp.message.register(work_handler, F.text == BTN_WORK)

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
