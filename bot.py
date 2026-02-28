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

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
# DATABASE_URL появится после того как ты добавишь Postgres — пока можно оставить, но бот упадёт без него


# ---------- Кнопки снизу ----------
def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------- Inline кнопка (только для “ГЛАВНАЯ”) ----------
def odessa_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Одесса", callback_data="city:odessa")]]
    )


# ---------- Тексты ----------
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

PROFILE_TEXT_TEMPLATE = """Баланс : {balance}

Количество заказов : {orders}
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
@gskalye
"""

WORK_TEXT = "X"  # ты заменишь на свой текст


# ---------- База данных ----------
pool: asyncpg.Pool | None = None


async def db_init() -> None:
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing (add Postgres + set DATABASE_URL in Railway Variables)")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)


async def ensure_user(user_id: int) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )


async def get_user(user_id: int) -> tuple[str, int]:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id
        )
        if not row:
            return "0.00", 0

        bal = decimal.Decimal(row["balance"])
        return f"{bal:.2f}", int(row["orders_count"])


# ---------- Bot ----------
dp = Dispatcher()


async def render_main(user_id: int) -> str:
    await ensure_user(user_id)
    balance, orders = await get_user(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=balance, orders=orders)


# /start — БЕЗ “Одесса”
@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = await render_main(message.from_user.id)
    await message.answer(text, reply_markup=bottom_menu())


# ГЛАВНАЯ — С “Одесса” прикреплённой к этому же сообщению
# (чтобы не ломалось из-за эмодзи/пробелов — ловим по слову)
@dp.message(F.text.contains("ГЛАВНАЯ"))
async def btn_main(message: Message):
    text = await render_main(message.from_user.id)
    await message.answer(text, reply_markup=odessa_inline())


@dp.message(F.text.contains("ПРОФИЛЬ"))
async def btn_profile(message: Message):
    await ensure_user(message.from_user.id)
    balance, orders = await get_user(message.from_user.id)
    await message.answer(PROFILE_TEXT_TEMPLATE.format(balance=balance, orders=orders), reply_markup=bottom_menu())


@dp.message(F.text.contains("ПОМОЩЬ"))
async def btn_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=bottom_menu())


@dp.message(F.text.contains("РАБОТА"))
async def btn_work(message: Message):
    await message.answer(WORK_TEXT, reply_markup=bottom_menu())


@dp.callback_query(F.data == "city:odessa")
async def cb_odessa(call: CallbackQuery):
    await call.answer()
    # Заглушка: потом добавим кнопки/каталог/цены
    await call.message.answer("Одесса выбрана ✅\n(дальше добавим разделы/позиции)")


async def main():
    await db_init()
    bot = Bot(token=BOT_TOKEN)

    # на всякий случай, чтобы polling работал всегда
    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
