import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

BOT_TOKEN = os.getenv("BOT_TOKEN")

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎟 Купить товар")],
            [KeyboardButton(text="👤 Оператор"), KeyboardButton(text="ℹ️ Информация")]
        ],
        resize_keyboard=True
    )

async def start(message: Message):
    await message.answer(
        "Добро пожаловать в BlackPort 👁‍🗨",
        reply_markup=main_keyboard()
    )

async def buy_coupon(message: Message):
    await message.answer("🎟 Покупка купона временно в разработке.")

async def operator(message: Message):
    await message.answer("👤 Напишите оператору: @potterspotter")

async def info(message: Message):
    await message.answer("ℹ️ Информация о проекте появится здесь.")

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start, CommandStart())
    dp.message.register(buy_coupon, F.text == "🎟 Купить купон")
    dp.message.register(operator, F.text == "👤 Оператор")
    dp.message.register(info, F.text == "ℹ️ Информация")

    await dp.start_polling(bot)

if __name__ == "__main__":

    asyncio.run(main())

