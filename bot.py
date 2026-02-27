import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

pool = None


# ================= БАЗА =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) DEFAULT 0,
            orders_count INTEGER DEFAULT 0
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id SERIAL PRIMARY KEY,
            name TEXT,
            price NUMERIC(12,2),
            link TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS purchases(
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            product_name TEXT,
            link TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS promocodes(
            code TEXT PRIMARY KEY,
            amount NUMERIC(12,2),
            is_active BOOLEAN DEFAULT TRUE
        );
        """)


async def ensure_user(user_id):
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users(user_id)
        VALUES($1)
        ON CONFLICT DO NOTHING;
        """, user_id)


async def get_user(user_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)


# ================= КЛАВИАТУРЫ =================

def start_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True
    )


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Одесса")],
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True
    )


def profile_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Пополнить баланс")],
            [KeyboardButton(text="Активировать промокод")],
            [KeyboardButton(text="История покупок")],
            [KeyboardButton(text="ГЛАВНАЯ 🔘")]
        ],
        resize_keyboard=True
    )


# ================= ТЕКСТЫ =================

async def start_message(message: Message):
    await ensure_user(message.from_user.id)

    text = (
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
        "Оператор: @gskalye"
    )

    await message.answer(text, reply_markup=start_keyboard())


async def main_message(message: Message):
    user = await get_user(message.from_user.id)

    text = (
        f"🏦Баланс: {user['balance']}\n"
        f"🛍️Количество заказов: {user['orders_count']}"
    )

    await message.answer(text, reply_markup=main_keyboard())


async def profile_message(message: Message):
    user = await get_user(message.from_user.id)

    text = (
        f"👤 Профиль\n\n"
        f"🏦Баланс: {user['balance']}\n"
        f"🛍️Количество заказов: {user['orders_count']}"
    )

    await message.answer(text, reply_markup=profile_keyboard())


# ================= ПОКУПКА =================

async def show_products(message: Message):
    async with pool.acquire() as conn:
        products = await conn.fetch("SELECT * FROM products")

    if not products:
        await message.answer("Пока нет товаров.")
        return

    text = "📦 Доступные позиции:\n\n"
    for p in products:
        text += f"{p['id']}. {p['name']} — {p['price']}\n"

    text += "\nНапиши номер товара для покупки."

    await message.answer(text)


async def buy_product(message: Message):
    if not message.text.isdigit():
        return

    product_id = int(message.text)

    async with pool.acquire() as conn:
        product = await conn.fetchrow("SELECT * FROM products WHERE id=$1", product_id)

    if not product:
        return

    user = await get_user(message.from_user.id)

    if user["balance"] < product["price"]:
        await message.answer("❌ Недостаточно средств.")
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance=balance-$1, orders_count=orders_count+1 WHERE user_id=$2",
            product["price"], message.from_user.id
        )

        await conn.execute(
            "INSERT INTO purchases(user_id, product_name, link) VALUES($1,$2,$3)",
            message.from_user.id, product["name"], product["link"]
        )

    await message.answer(f"✅ Покупка успешна!\nВот твоя ссылка:\n{product['link']}")


# ================= ПРОМОКОД =================

async def activate_promocode(message: Message):
    code = message.text.strip()

    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promocodes WHERE code=$1 AND is_active=TRUE", code)

    if not promo:
        await message.answer("❌ Промокод не найден.")
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance=balance+$1 WHERE user_id=$2",
            promo["amount"], message.from_user.id
        )
        await conn.execute(
            "UPDATE promocodes SET is_active=FALSE WHERE code=$1",
            code
        )

    await message.answer(f"✅ Баланс пополнен на {promo['amount']}")


# ================= ИСТОРИЯ =================

async def show_history(message: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM purchases WHERE user_id=$1 ORDER BY created_at DESC",
            message.from_user.id
        )

    if not rows:
        await message.answer("История пуста.")
        return

    text = "🧾 История покупок:\n\n"
    for r in rows:
        text += f"{r['product_name']}\n{r['link']}\n\n"

    await message.answer(text)


# ================= ЗАПУСК =================

async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_message, CommandStart())
    dp.message.register(main_message, F.text == "ГЛАВНАЯ 🔘")
    dp.message.register(profile_message, F.text == "ПРОФИЛЬ 👤")
    dp.message.register(show_products, F.text == "Одесса")
    dp.message.register(show_history, F.text == "История покупок")
    dp.message.register(activate_promocode, F.text.startswith("PROMO_"))
    dp.message.register(buy_product)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
