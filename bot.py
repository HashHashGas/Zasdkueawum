import asyncio
import os

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

# =========================================================
# 1) НАСТРОЙКИ
# =========================================================
# Railway / сервер: токен берём из переменных окружения
# В Railway → Variables должен быть BOT_TOKEN = <твой токен>
BOT_TOKEN = os.getenv("BOT_TOKEN")


# =========================================================
# 2) ТЕКСТЫ (МЕНЯЙ ТУТ)
# =========================================================
START_TEXT = (
    "✅ ТВОЙ СТАРТОВЫЙ ТЕКСТ\n"
    "Тут можешь поставить смайлики как хочешь 😄"
)

ODESA_TEXT = (
    "🏴‍☠️ ТЕКСТ ПРИ НАЖАТИИ «ОДЕССА»\n"
    "Тут будет другой текст, который ты хочешь."
)

MAIN_TEXT = (
    "🔘 ТЕКСТ ВКЛАДКИ «ГЛАВНАЯ»\n"
    "Сюда можешь поставить то же, что и START_TEXT — или другой."
)

PROFILE_TEXT = (
    "👤 ТЕКСТ ВКЛАДКИ «ПРОФИЛЬ»\n"
    "Например: баланс, статус, и т.д."
)

HELP_TEXT = (
    "💬 ТЕКСТ ВКЛАДКИ «ПОМОЩЬ»\n"
    "Тут твои правила, FAQ, контакты."
)

WORK_TEXT = (
    "💸 ТЕКСТ ВКЛАДКИ «РАБОТА»\n"
    "Тут твой текст."
)


# =========================================================
# 3) КНОПКИ (НИЖНЯЯ ПАНЕЛЬ)
# =========================================================
BTN_MAIN = "ГЛАВНАЯ 🔘"
BTN_PROFILE = "ПРОФИЛЬ 👤"
BTN_HELP = "ПОМОЩЬ 💬"
BTN_WORK = "РАБОТА 💸"

def main_reply_keyboard() -> ReplyKeyboardMarkup:
    # Нижняя панель (ReplyKeyboard)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MAIN), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_WORK)],
        ],
        resize_keyboard=True,
    )


# =========================================================
# 4) INLINE КНОПКИ
# =========================================================
def start_inline_keyboard() -> InlineKeyboardMarkup:
    # Кнопка под старт-текстом
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Одесса", callback_data="city:odesa")]
        ]
    )

def odesa_inline_keyboard() -> InlineKeyboardMarkup:
    # Кнопки, которые появляются после нажатия "Одесса"
    # =========================================================
    # МЕНЯЙ ТУТ количество / названия
    # Просто добавляй/удаляй строки InlineKeyboardButton(...)
    # =========================================================
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Панелька 1", callback_data="odesa:panel1")],
            [InlineKeyboardButton(text="Панелька 2", callback_data="odesa:panel2")],
            [InlineKeyboardButton(text="Панелька 3", callback_data="odesa:panel3")],
        ]
    )

def profile_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="История Покупок", callback_data="profile:history")],
        ]
    )


# =========================================================
# 5) ЛОГИКА ЭКРАНОВ
# =========================================================
async def show_main(message: Message):
    # Главная = /start по функциям
    await message.answer(
        MAIN_TEXT,
        reply_markup=main_reply_keyboard(),
    )
    await message.answer(
        START_TEXT,
        reply_markup=start_inline_keyboard(),
    )

async def start_cmd(message: Message):
    await message.answer(
        START_TEXT,
        reply_markup=main_reply_keyboard(),
    )
    await message.answer(
        "⬇️ Выбери город:",
        reply_markup=start_inline_keyboard(),
    )

async def main_btn(message: Message):
    await show_main(message)

async def profile_btn(message: Message):
    await message.answer(PROFILE_TEXT, reply_markup=main_reply_keyboard())
    await message.answer("Выбери действие 👇", reply_markup=profile_inline_keyboard())

async def help_btn(message: Message):
    await message.answer(HELP_TEXT, reply_markup=main_reply_keyboard())

async def work_btn(message: Message):
    await message.answer(WORK_TEXT, reply_markup=main_reply_keyboard())


# =========================================================
# 6) CALLBACK (НАЖАТИЯ INLINE)
# =========================================================
async def on_city_odesa(call: CallbackQuery):
    await call.answer()  # убирает "часики" на кнопке
    await call.message.answer(ODESA_TEXT, reply_markup=odesa_inline_keyboard())

# Заглушки для оdesa панелек
async def on_odesa_panel(call: CallbackQuery):
    await call.answer()
    # =========================================================
    # МЕНЯЙ ТУТ ответы для каждой панельки
    # =========================================================
    await call.message.answer(f"✅ Нажато: {call.data}")

# Заглушки для профиля
async def on_profile_action(call: CallbackQuery):
    await call.answer()
    if call.data == "profile:topup":
        await call.message.answer("💳 Пополнение баланса — пока в разработке.")
    elif call.data == "profile:promo":
        await call.message.answer("🏷 Активировать промокод — пока в разработке.")
    elif call.data == "profile:history":
        await call.message.answer("🧾 История покупок — пока в разработке.")
    else:
        await call.message.answer("Пункт пока не настроен.")


# =========================================================
# 7) MAIN
# =========================================================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Добавь BOT_TOKEN в Railway → Variables.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # /start
    dp.message.register(start_cmd, CommandStart())

    # Нижняя панель
    dp.message.register(main_btn, F.text == BTN_MAIN)
    dp.message.register(profile_btn, F.text == BTN_PROFILE)
    dp.message.register(help_btn, F.text == BTN_HELP)
    dp.message.register(work_btn, F.text == BTN_WORK)

    # Inline callbacks
    dp.callback_query.register(on_city_odesa, F.data == "city:odesa")
    dp.callback_query.register(on_odesa_panel, F.data.startswith("odesa:"))
    dp.callback_query.register(on_profile_action, F.data.startswith("profile:"))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
