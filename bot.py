import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart


# =========================
# НАСТРОЙКИ (менять тут)
# =========================

SUPPORT_LINK = "https://t.me/mcdonald_support"  # <-- оператор/сапорт
CHAT_LINK = "https://t.me/+HvuVKZkR2-03MzBi"    # <-- чат
REVIEWS_LINK = "https://t.me/+HvuVKZkR2-03MzBi" # <-- отзывы (пока как ты дал)
BOT_USERNAME = "@CavalierShopBot"               # <-- ник бота

# Слово-замена вместо "Клад" (как просил)
WORD_DROP_REPLACEMENT = "Вклады"  # можешь поменять на "Заказы" / "Пункты" / "Выдачи" и т.д.


def get_start_text(balance: str = "—", orders: str = "—") -> str:
    # Текст для /start и ГЛАВНАЯ 🔘 (одинаковый)
    return (
        "✋🏻 Здравствуй! Кавалер 🎩\n"
        "👑Вы находитесь в Cavalier Shop👑\n\n"
        "✍🏻Кратко о нашем сервисе\n\n"
        f"°Готовые {WORD_DROP_REPLACEMENT}\n"
        f"°Горячие {WORD_DROP_REPLACEMENT}\n"
        "°Превосходное качество товара\n"
        "°ОПТ\n"
        "°Разновидные способы оплаты\n"
        "°Отправки NovaPost 🇺🇦\n"
        "°Оператор/Сапорт в сети 24/7\n\n"
        "Актуальные ссылки\n\n"
        "Бот :\n"
        f"{BOT_USERNAME}\n\n"
        "💬Чат :\n"
        f"{CHAT_LINK}\n\n"
        "🥇Отзывы :\n"
        f"{REVIEWS_LINK}\n\n"
        "Оператор/Сапорт :\n"
        f"{SUPPORT_LINK}\n\n"
        "🏦Баланс :\n"
        f"{balance}\n"
        "🛍️Количество заказов :\n"
        f"{orders}"
    )


PROFILE_TEXT = (
    "👤 Профиль\n\n"
    "🏦 Баланс:\n"
    "—\n\n"
    "🛍️ Количество заказов:\n"
    "—"
)

HELP_TEXT = (
    "💬 Помощь\n\n"
    "Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :\n"
    f"{SUPPORT_LINK}"
)

WORK_TEXT = "A"  # <-- ты заменишь на свой текст


# =========================
# КНОПКИ
# =========================

def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
    )


def main_inline() -> InlineKeyboardMarkup:
    # Кнопка под главным сообщением
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Одесса ⚓", callback_data="city:odessa")]
        ]
    )


def odessa_inline() -> InlineKeyboardMarkup:
    # Кнопки внутри "Одесса" (легко расширять)
    buttons = [
        [InlineKeyboardButton(text="📦 Каталог", callback_data="odessa:catalog")],
        [InlineKeyboardButton(text="ℹ️ Инфо", callback_data="odessa:info")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def profile_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="🧾 История Покупок", callback_data="profile:history")],
        ]
    )


# =========================
# ЛОГИКА
# =========================

def get_env_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не найден. Добавь переменную окружения BOT_TOKEN в Railway.")
    return token


async def show_home(message: Message):
    # Чтобы не засорять чат: если это /start — просто отправляем.
    # Если это кнопка "ГЛАВНАЯ" — тоже отправим новым (Telegram не даёт редактировать чужие старые сообщения всегда).
    text = get_start_text()
    await message.answer(text, reply_markup=bottom_menu())
    await message.answer("⬇️ Выбери город:", reply_markup=main_inline())


@Dispatcher().message  # заглушка чтобы линтер не ругался (ни на что не влияет)
async def _noop(_: Message):
    pass


async def main():
    bot = Bot(token=get_env_token())
    dp = Dispatcher()

    # /start
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        # Одно основное + одна строка с inline (без «простыней» из разных сообщений)
        text = get_start_text()
        await message.answer(text, reply_markup=bottom_menu())
        await message.answer("⬇️ Выбери город:", reply_markup=main_inline())

    # ГЛАВНАЯ (как /start)
    @dp.message(F.text == "ГЛАВНАЯ 🔘")
    async def btn_home(message: Message):
        text = get_start_text()
        await message.answer(text, reply_markup=bottom_menu())
        await message.answer("⬇️ Выбери город:", reply_markup=main_inline())

    # ПРОФИЛЬ
    @dp.message(F.text == "ПРОФИЛЬ 👤")
    async def btn_profile(message: Message):
        await message.answer(PROFILE_TEXT, reply_markup=bottom_menu())
        await message.answer("⬇️ Действия профиля:", reply_markup=profile_inline())

    # ПОМОЩЬ
    @dp.message(F.text == "ПОМОЩЬ 💬")
    async def btn_help(message: Message):
        await message.answer(HELP_TEXT, reply_markup=bottom_menu())

    # РАБОТА
    @dp.message(F.text == "РАБОТА 💸")
    async def btn_work(message: Message):
        await message.answer(WORK_TEXT, reply_markup=bottom_menu())

    # Inline: Одесса
    @dp.callback_query(F.data == "city:odessa")
    async def cb_odessa(call: CallbackQuery):
        await call.message.edit_text(
            "⚓ Одесса\n\nВыбери раздел ниже:",
            reply_markup=odessa_inline()
        )
        await call.answer()

    # Inline: Назад на главную (возвращаем то же самое главное сообщение)
    @dp.callback_query(F.data == "back:home")
    async def cb_back_home(call: CallbackQuery):
        await call.message.edit_text(
            get_start_text(),
            reply_markup=main_inline()
        )
        await call.answer()

    # Odessa placeholders
    @dp.callback_query(F.data == "odessa:catalog")
    async def cb_odessa_catalog(call: CallbackQuery):
        await call.answer("Каталог скоро будет 👀", show_alert=True)

    @dp.callback_query(F.data == "odessa:info")
    async def cb_odessa_info(call: CallbackQuery):
        await call.answer("Инфо скоро добавим 🤝", show_alert=True)

    # Profile placeholders
    @dp.callback_query(F.data.startswith("profile:"))
    async def cb_profile_any(call: CallbackQuery):
        await call.answer("Скоро подключим 🧠", show_alert=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
