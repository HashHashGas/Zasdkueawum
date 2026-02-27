import asyncio
import os
from typing import Optional, Tuple

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")


# ---------- ТЕКСТЫ (тут меняешь под себя) ----------
HOME_TEXT_TEMPLATE = """✋🏻 Здравствуй! Кавалер 🎩
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
https://t.me/mcdonald_support

🏦Баланс : {balance}
🛍️Количество заказов : {orders}
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦 Баланс: {balance}
🛍️ Количество заказов: {orders}
"""

HELP_TEXT = """💬 Помощь

Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
https://t.me/mcdonald_support
"""

WORK_TEXT = "X"  # <- тут потом заменишь на свой текст


# ---------- КНОПКИ ----------
def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбирай кнопку ниже 👇",
    )


def city_inline_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Одесса ⚓", callback_data="city:odessa")
    # Потом добавишь так же:
    # kb.button(text="Киев 🏛", callback_data="city:kyiv")
    kb.adjust(1)
    return kb.as_markup()


def profile_actions_inline_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пополнить баланс", callback_data="profile:topup")
    kb.button(text="Активировать промокод", callback_data="profile:promo")
    kb.button(text="История Покупок", callback_data="profile:history")
    kb.adjust(1)
    return kb.as_markup()


# ---------- БАЗА (Postgres) ----------
pool: Optional[asyncpg.Pool] = None


async def db_init() -> None:
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in environment variables")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id BIGINT PRIMARY KEY,
                balance NUMERIC(12,2) NOT NULL DEFAULT 0,
                orders_count INT NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)


async def get_or_create_user(tg_id: int) -> Tuple[str, int]:
    # Возвращаем (balance_as_text, orders_count)
    assert pool is not None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT balance, orders_count FROM users WHERE tg_id=$1",
            tg_id
        )
        if row is None:
            await conn.execute(
                "INSERT INTO users (tg_id) VALUES ($1)",
                tg_id
            )
            return "0.00", 0

        balance = row["balance"]
        orders = row["orders_count"]
        # Приводим красиво к строке
        return f"{float(balance):.2f}", int(orders)


# На будущее (когда начнёшь менять баланс/заказы)
async def add_balance(tg_id: int, amount: float) -> None:
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE tg_id=$2",
            amount, tg_id
        )


async def inc_orders(tg_id: int) -> None:
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET orders_count = orders_count + 1 WHERE tg_id=$1",
            tg_id
        )


# ---------- ХЭНДЛЕРЫ ----------
async def send_home(message: Message) -> None:
    balance, orders = await get_or_create_user(message.from_user.id)

    # ВАЖНО: чтобы не “засорять” — можно отправлять новый экран,
    # а старый пусть уходит вверх. Telegram “одним и тем же” сообщением
    # без inline callback редактирования на /start не сделать.
    text = HOME_TEXT_TEMPLATE.format(balance=balance, orders=orders)

    await message.answer(
        text,
        reply_markup=main_menu_kb()
    )
    # Кнопка города — ПРИКРЕПЛЕНА К ОТДЕЛЬНОМУ СООБЩЕНИЮ?
    # Ты просил прямо под основным текстом: это возможно только если
    # inline-кнопки будут у этого же сообщения.
    # Поэтому шлём одним сообщением:
    await message.answer(
        "⬇️",
        reply_markup=city_inline_kb()
    )


async def profile(message: Message) -> None:
    balance, orders = await get_or_create_user(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=balance, orders=orders)

    # Одним сообщением: текст + кнопки
    await message.answer(text, reply_markup=profile_actions_inline_kb())


async def help_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT)


async def work_cmd(message: Message) -> None:
    await message.answer(WORK_TEXT)


# Inline callbacks (пока заглушки)
async def on_city_callback(callback: Message):  # placeholder (aiogram uses CallbackQuery normally)
    pass


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables")

    await db_init()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(send_home, CommandStart())
    dp.message.register(send_home, F.text == "ГЛАВНАЯ 🔘")
    dp.message.register(profile, F.text == "ПРОФИЛЬ 👤")
    dp.message.register(help_cmd, F.text == "ПОМОЩЬ 💬")
    dp.message.register(work_cmd, F.text == "РАБОТА 💸")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
