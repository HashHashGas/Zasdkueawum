import asyncio
import os
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")  # Railway -> Variables -> BOT_TOKEN = "xxx"


# =========================
# ТЕКСТЫ (МЕНЯЙ ТУТ)
# =========================
HOME_TEXT = (
    "✋🏻 Здравствуй! Кавалер 🎩\n"
    "👑Вы находитесь в Cavalier Shop👑\n\n"
    "✍🏻Кратко о нашем сервисе\n\n"
    "°Готовые позиции\n"
    "°Горячие позиции\n"
    "°Превосходное качество товара\n"
    "°ОПТ\n"
    "°Разновидные способы оплаты\n"
    "°Отправки NovaPost 🇺🇦\n"
    "°Оператор/Сапорт в сети 24/7\n\n"
    "Актуальные ссылки\n\n"
    "Бот :\n"
    "@CavalierShopBot\n\n"
    "💬Чат :\n"
    "https://t.me/+HvuVKZkR2-03MzBi\n\n"
    "🥇Отзывы :\n"
    "https://t.me/+HvuVKZkR2-03MzBi\n\n"
    "Оператор/Сапорт :\n"
    "https://t.me/mcdonald_support\n\n"
    "🏦Баланс :\n"
    "🛍️Количество заказов :\n"
)

PROFILE_TEXT = (
    "👤 Профиль\n\n"
    "🏦 Баланс:\n"
    "—\n\n"
    "🛍️ Количество заказов:\n"
    "—\n"
)

HELP_TEXT = (
    "Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :\n"
    "https://t.me/mcdonald_support"
)

WORK_TEXT = "A"  # тут одна буква, как просил — потом заменишь


# =========================
# КЛАВИАТУРЫ
# =========================
def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True
    )


def home_inline() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Одесса ⚓️", callback_data="city:odessa")
    # если потом захочешь добавить города — добавишь тут:
    # kb.button(text="Киев 🏛", callback_data="city:kyiv")
    kb.adjust(1)
    return kb


def profile_inline() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Пополнить баланс", callback_data="profile:topup")
    kb.button(text="🎟 Активировать промокод", callback_data="profile:promo")
    kb.button(text="🧾 История покупок", callback_data="profile:history")
    kb.adjust(1)
    return kb


def odessa_inline() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    # Заготовка под будущие товары/каталог/категории:
    kb.button(text="📦 Каталог", callback_data="odessa:catalog")
    kb.button(text="ℹ️ Информация", callback_data="odessa:info")
    kb.adjust(1)
    return kb


# =========================
# ХЕЛПЕРЫ ОТПРАВКИ (чтобы не плодить сообщения)
# =========================
async def send_home(message: Message) -> None:
    await message.answer(
        HOME_TEXT,
        reply_markup=home_inline().as_markup(),
        disable_web_page_preview=True
    )


async def send_profile(message: Message) -> None:
    await message.answer(
        PROFILE_TEXT,
        reply_markup=profile_inline().as_markup(),
        disable_web_page_preview=True
    )


# =========================
# ХЕНДЛЕРЫ
# =========================
async def cmd_start(message: Message) -> None:
    # 1 сообщение: текст + inline "Одесса"
    await message.answer(" ", reply_markup=bottom_menu())  # просто выставляем нижнюю панель (без текста)
    await send_home(message)


async def on_home_button(message: Message) -> None:
    await send_home(message)


async def on_profile_button(message: Message) -> None:
    await send_profile(message)


async def on_help_button(message: Message) -> None:
    await message.answer(HELP_TEXT, disable_web_page_preview=True)


async def on_work_button(message: Message) -> None:
    await message.answer(WORK_TEXT)


# ===== INLINE callbacks =====
async def on_city(callback: CallbackQuery) -> None:
    await callback.answer()
    city = callback.data.split(":", 1)[1]

    if city == "odessa":
        text = "Одесса ⚓️\n\nВыбери действие:"  # это НЕ отдельное сообщение “выбери город”, это ответ на нажатие
        await callback.message.answer(text, reply_markup=odessa_inline().as_markup())
    else:
        await callback.message.answer("Город пока не настроен.")


async def on_profile_actions(callback: CallbackQuery) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]

    if action == "topup":
        await callback.message.answer("💳 Пополнение баланса — скоро добавим.")
    elif action == "promo":
        await callback.message.answer("🎟 Промокоды — скоро добавим.")
    elif action == "history":
        await callback.message.answer("🧾 История покупок — скоро добавим.")
    else:
        await callback.message.answer("Неизвестное действие.")


async def on_odessa_actions(callback: CallbackQuery) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]

    if action == "catalog":
        await callback.message.answer("📦 Каталог — позже добавим категории/товары.")
    elif action == "info":
        await callback.message.answer("ℹ️ Информация — позже добавим текст.")
    else:
        await callback.message.answer("Неизвестное действие.")


def ensure_token() -> str:
    if not BOT_TOKEN or not isinstance(BOT_TOKEN, str) or len(BOT_TOKEN) < 10:
        raise RuntimeError("BOT_TOKEN не найден. Railway -> Settings -> Shared Variables -> BOT_TOKEN")
    return BOT_TOKEN


async def main() -> None:
    token = ensure_token()
    bot = Bot(token=token)
    dp = Dispatcher()

    # /start
    dp.message.register(cmd_start, CommandStart())

    # нижняя панель
    dp.message.register(on_home_button, F.text == "ГЛАВНАЯ 🔘")
    dp.message.register(on_profile_button, F.text == "ПРОФИЛЬ 👤")
    dp.message.register(on_help_button, F.text == "ПОМОЩЬ 💬")
    dp.message.register(on_work_button, F.text == "РАБОТА 💸")

    # inline callbacks
    dp.callback_query.register(on_city, F.data.startswith("city:"))
    dp.callback_query.register(on_profile_actions, F.data.startswith("profile:"))
    dp.callback_query.register(on_odessa_actions, F.data.startswith("odessa:"))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
