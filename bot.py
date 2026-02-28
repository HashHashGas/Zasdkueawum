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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


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


# inline под "ГЛАВНАЯ"
def inline_main_city() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Одесса", callback_data="city:odesa")]]
    )


# 3 кнопки внутри Одессы — МЕНЯЕШЬ ЛЕГКО ТУТ
ODESA_BUTTONS = [
    ("🛍 Каталог", "odesa:catalog"),
    ("💬 Оператор", "odesa:operator"),
    ("⬅️ Назад", "odesa:back"),
]

def inline_odesa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=cb)] for t, cb in ODESA_BUTTONS]
    )


# inline в профиле
def inline_profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
        ]
    )


# ================== DB ==================
pool: asyncpg.Pool | None = None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        # ВАЖНО: одним execute можно гнать несколько CREATE TABLE
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            item_name TEXT NOT NULL,
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
            code TEXT NOT NULL REFERENCES promo_codes(code),
            activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, code)
        );
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


async def activate_promo(user_id: int, code: str) -> tuple[bool, str]:
    """Промокоды — если решишь использовать. Сейчас просто логика."""
    code = code.strip().upper()
    assert pool is not None

    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow(
                "SELECT code, amount, is_active, uses_left FROM promo_codes WHERE code=$1 FOR UPDATE",
                code,
            )
            if not promo or not promo["is_active"] or promo["uses_left"] <= 0:
                return False, "❌ Промокод недействителен."

            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                user_id, code
            )
            if used:
                return False, "❌ Ты уже активировал этот промокод."

            amount = decimal.Decimal(promo["amount"])

            await con.execute(
                "INSERT INTO promo_activations(user_id, code) VALUES($1, $2)",
                user_id, code
            )
            await con.execute(
                "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
                code
            )
            await con.execute(
                "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                user_id, amount
            )

    return True, f"✅ Промокод активирован!\n🏦 Начислено: {amount:.2f}"


async def get_history(user_id: int) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT item_name, link, created_at FROM purchases WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20",
            user_id,
        )
    return rows


# ================== FSM ==================
class PromoStates(StatesGroup):
    waiting_code = State()


# ================== BOT ==================
dp = Dispatcher(storage=MemoryStorage())


async def render_main_text(user_id: int) -> str:
    await ensure_user(user_id)
    bal, orders = await get_user_stats(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=orders)


# /start — БЕЗ Одессы
@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=bottom_menu())


# ГЛАВНАЯ — С Одессой прикреплённой к этому сообщению
@dp.message(F.text.contains("ГЛАВНАЯ"))
async def btn_main(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=inline_main_city())


# ПРОФИЛЬ — текст + 3 inline кнопки
@dp.message(F.text.contains("ПРОФИЛЬ"))
async def btn_profile(message: Message):
    await ensure_user(message.from_user.id)
    bal, orders = await get_user_stats(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=orders)
    await message.answer(text, reply_markup=inline_profile_menu())


@dp.message(F.text.contains("ПОМОЩЬ"))
async def btn_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=bottom_menu())


@dp.message(F.text.contains("РАБОТА"))
async def btn_work(message: Message):
    await message.answer(WORK_TEXT, reply_markup=bottom_menu())


# ========= CALLBACKS =========

# Нажал Одесса — НИКАКОГО ТЕКСТА. Просто меняем кнопки под этим же сообщением
@dp.callback_query(F.data == "city:odesa")
async def cb_city_odesa(call: CallbackQuery):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=inline_odesa_menu())


# 3 кнопки Одессы — пока заглушки (можешь менять как хочешь)
@dp.callback_query(F.data == "odesa:catalog")
async def cb_odesa_catalog(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Каталог: скоро добавим.")

@dp.callback_query(F.data == "odesa:operator")
async def cb_odesa_operator(call: CallbackQuery):
    await call.answer()
    await call.message.answer("@gskalye")

@dp.callback_query(F.data == "odesa:back")
async def cb_odesa_back(call: CallbackQuery):
    await call.answer()
    # возвращаем обратно одну кнопку “Одесса”
    await call.message.edit_reply_markup(reply_markup=inline_main_city())


# профиль-кнопки
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


async def main():
    await db_init()
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
