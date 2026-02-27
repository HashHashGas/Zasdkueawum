import asyncio
import os
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")


# =========================
# ТЕКСТЫ
# =========================

HOME_TEXT = """✋🏻 Здравствуй! Кавалер 🎩
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

🏦Баланс :
🛍️Количество заказов :
"""

PROFILE_TEXT = """👤 Профиль

🏦 Баланс:

🛍️ Количество заказов:
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
https://t.me/mcdonald_support
"""

WORK_TEXT = "A"  # заменишь потом


# =========================
# КЛАВИАТУРЫ
# =========================

def bottom_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел"
    )


def home_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚓ Одесса ⚓", callback_data="city:odessa")
    kb.adjust(1)
    return kb


def profile_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Пополнить баланс", callback_data="profile:topup")
    kb.button(text="🎟 Активировать промокод", callback_data="profile:promo")
    kb.button(text="🧾 История покупок", callback_data="profile:history")
    kb.adjust(1)
    return kb


def odessa_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Каталог", callback_data="odessa:catalog")
    kb.button(text="ℹ️ Информация", callback_data="odessa:info")
    kb.adjust(1)
    return kb


# =========================
# ФУНКЦИИ
# =========================

async def send_home(message: Message):
    await message.answer(
        HOME_TEXT,
        reply_markup=home_inline().as_markup(),
        disable_web_page_preview=True
    )


async def send_profile(message: Message):
    await message.answer(
        PROFILE_TEXT,
        reply_markup=profile_inline().as_markup()
    )


# =========================
# ХЕНДЛЕРЫ
# =========================

async def cmd_start(message: Message):
    # 1. Ставим нижнюю панель
    await message.answer(
        HOME_TEXT,
        reply_markup=bottom_menu()
    )

    # 2. Отдельно отправляем inline Одесса (под текстом)
    await message.answer(
        " ",
        reply_markup=home_inline().as_markup()
    )


async def btn_home(message: Message):
    await send_home(message)


async def btn_profile(message: Message):
    await send_profile(message)


async def btn_help(message: Message):
    await message.answer(HELP_TEXT)


async def btn_work(message: Message):
    await message.answer(WORK_TEXT)


# ===== INLINE CALLBACKS =====

async def city_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "⚓ Одесса",
        reply_markup=odessa_inline().as_markup()
    )


async def profile_actions(callback: CallbackQuery):
    await callback.answer("Скоро добавим 🤝")


async def odessa_actions(callback: CallbackQuery):
    await callback.answer("Скоро добавим 🤝")


# =========================
# MAIN
# =========================

async def main():
    if not BOT_TOKEN:
        raise ValueError("Добавь BOT_TOKEN в Railway -> Variables")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(btn_home, F.text == "ГЛАВНАЯ 🔘")
    dp.message.register(btn_profile, F.text == "ПРОФИЛЬ 👤")
    dp.message.register(btn_help, F.text == "ПОМОЩЬ 💬")
    dp.message.register(btn_work, F.text == "РАБОТА 💸")

    dp.callback_query.register(city_handler, F.data.startswith("city:"))
    dp.callback_query.register(profile_actions, F.data.startswith("profile:"))
    dp.callback_query.register(odessa_actions, F.data.startswith("odessa:"))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
