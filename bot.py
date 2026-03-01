import os
import asyncio
from decimal import Decimal
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit())

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("ENV missing")

dp = Dispatcher()
pool = None

# ---------- DB ----------

async def db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) DEFAULT 0 NOT NULL,
            orders_count INT DEFAULT 0 NOT NULL,
            awaiting_promo BOOLEAN DEFAULT FALSE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products(
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            link TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS purchases(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            product_code TEXT,
            product_title TEXT,
            price NUMERIC(12,2),
            link TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS promo_codes(
            code TEXT PRIMARY KEY,
            amount NUMERIC(12,2) NOT NULL,
            uses_left INT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS promo_activations(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            code TEXT REFERENCES promo_codes(code),
            amount NUMERIC(12,2) NOT NULL,
            UNIQUE(user_id, code)
        );
        """)

        # демо товары
        await con.execute("""
        INSERT INTO products(code,title,price,link)
        VALUES
        ('saint','saint',10,'https://example.com/saint'),
        ('big_bob','big bob',15,'https://example.com/big'),
        ('shenen','shenen',20,'https://example.com/shenen')
        ON CONFLICT DO NOTHING;
        """)

async def ensure(uid):
    async with pool.acquire() as con:
        await con.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)

async def get_user(uid):
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT balance,orders_count,awaiting_promo FROM users WHERE user_id=$1", uid)

# ---------- UI ----------

def reply_kb():
    b = ReplyKeyboardBuilder()
    b.button(text="ГЛАВНАЯ ⚪")
    b.button(text="ПРОФИЛЬ 👤")
    b.button(text="ПОМОЩЬ 💬")
    b.button(text="РАБОТА 🛠️")
    b.adjust(2,2)
    return b.as_markup(resize_keyboard=True)

def main_inline():
    b = InlineKeyboardBuilder()
    b.button(text="Одесса ⚓", callback_data="city")
    return b.as_markup()

def profile_inline():
    b = InlineKeyboardBuilder()
    b.button(text="💳 Пополнить баланс", callback_data="topup")
    b.button(text="🎟 Активировать промокод", callback_data="promo_btn")
    b.button(text="🧾 История покупок", callback_data="history")
    b.adjust(1,1,1)
    return b.as_markup()

def odessa_inline():
    b = InlineKeyboardBuilder()
    b.button(text="1 saint", callback_data="buy:saint")
    b.button(text="2 big bob", callback_data="buy:big_bob")
    b.button(text="3 shenen", callback_data="buy:shenen")
    b.adjust(1,1,1)
    return b.as_markup()

# ---------- TEXT ----------

async def main_text(uid):
    u = await get_user(uid)
    bal = Decimal(str(u["balance"]))
    orders = u["orders_count"]
    return f"""✋🏻 Здравствуй! Кавалер 🎩
👑Вы находитесь в Cavalier Shop👑

🏦Баланс : {bal:.2f}
🛍️Количество заказов : {orders}
"""

async def profile_text(uid):
    u = await get_user(uid)
    bal = Decimal(str(u["balance"]))
    orders = u["orders_count"]
    return f"""👤 Профиль

🏦 Баланс : {bal:.2f}
🛍️ Количество заказов : {orders}
"""

# ---------- HANDLERS ----------

@dp.message(CommandStart())
async def start(m: Message):
    await ensure(m.from_user.id)
    await m.answer(await main_text(m.from_user.id), reply_markup=reply_kb())

@dp.message(F.text == "ГЛАВНАЯ ⚪")
async def main_menu(m: Message):
    await ensure(m.from_user.id)
    await m.answer(await main_text(m.from_user.id), reply_markup=main_inline())

@dp.message(F.text == "ПРОФИЛЬ 👤")
async def profile_menu(m: Message):
    await ensure(m.from_user.id)
    await m.answer(await profile_text(m.from_user.id), reply_markup=profile_inline())

@dp.message(F.text == "ПОМОЩЬ 💬")
async def help_menu(m: Message):
    await m.answer("Поддержка: @gskalye", reply_markup=reply_kb())

@dp.message(F.text == "РАБОТА 🛠️")
async def work_menu(m: Message):
    await m.answer("A", reply_markup=reply_kb())

@dp.callback_query(F.data == "city")
async def city(call: CallbackQuery):
    await call.message.answer("Вы выбрали Одессу. Выберите товар:", reply_markup=odessa_inline())
    await call.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy(call: CallbackQuery):
    uid = call.from_user.id
    code = call.data.split(":")[1]
    async with pool.acquire() as con:
        async with con.transaction():
            user = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", uid)
            product = await con.fetchrow("SELECT * FROM products WHERE code=$1 AND is_active=TRUE", code)
            if not product:
                await call.answer("Нет товара")
                return
            if Decimal(str(user["balance"])) < Decimal(str(product["price"])):
                await call.answer("Недостаточно средств")
                return
            await con.execute("UPDATE users SET balance=balance-$1,orders_count=orders_count+1 WHERE user_id=$2", product["price"], uid)
            await con.execute("INSERT INTO purchases(user_id,product_code,product_title,price,link) VALUES($1,$2,$3,$4,$5)",
                              uid, product["code"], product["title"], product["price"], product["link"])
    await call.message.answer(f"✅ Покупка успешна\n{product['link']}")
    await call.answer()

@dp.callback_query(F.data == "promo_btn")
async def promo_btn(call: CallbackQuery):
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET awaiting_promo=TRUE WHERE user_id=$1", call.from_user.id)
    await call.message.answer("Введите промокод одним сообщением:")
    await call.answer()

@dp.message()
async def promo_handler(m: Message):
    u = await get_user(m.from_user.id)
    if not u or not u["awaiting_promo"]:
        return

    code = m.text.strip()
    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow("SELECT * FROM promo_codes WHERE code=$1 AND is_active=TRUE FOR UPDATE", code)
            if not promo or promo["uses_left"] <= 0:
                await con.execute("UPDATE users SET awaiting_promo=FALSE WHERE user_id=$1", m.from_user.id)
                await m.answer("❌ Промокод недействителен")
                return
            used = await con.fetchrow("SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                                      m.from_user.id, code)
            if used:
                await con.execute("UPDATE users SET awaiting_promo=FALSE WHERE user_id=$1", m.from_user.id)
                await m.answer("❌ Уже активирован")
                return
            await con.execute("INSERT INTO promo_activations(user_id,code,amount) VALUES($1,$2,$3)",
                              m.from_user.id, code, promo["amount"])
            await con.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=$1", code)
            await con.execute("UPDATE users SET balance=balance+$1,awaiting_promo=FALSE WHERE user_id=$2",
                              promo["amount"], m.from_user.id)

    await m.answer(f"✅ Промокод активирован +{promo['amount']}")

@dp.message(Command("addpromo"))
async def addpromo(m: Message):
    if ADMIN_IDS and m.from_user.id not in ADMIN_IDS:
        await m.answer("Нет доступа")
        return
    parts = m.text.split()
    if len(parts) != 4:
        await m.answer("Формат: /addpromo CODE AMOUNT USES")
        return
    code, amount, uses = parts[1], Decimal(parts[2]), int(parts[3])
    async with pool.acquire() as con:
        await con.execute("""
        INSERT INTO promo_codes(code,amount,uses_left,is_active)
        VALUES($1,$2,$3,TRUE)
        ON CONFLICT (code) DO UPDATE
        SET amount=$2,uses_left=$3,is_active=TRUE
        """, code, amount, uses)
    await m.answer("✅ Промокод создан")

@dp.callback_query(F.data == "history")
async def history(call: CallbackQuery):
    async with pool.acquire() as con:
        rows = await con.fetch("SELECT product_title,link FROM purchases WHERE user_id=$1 ORDER BY id DESC LIMIT 20",
                               call.from_user.id)
    if not rows:
        await call.message.answer("История пуста")
    else:
        text = "\n".join([f"{r['product_title']} - {r['link']}" for r in rows])
        await call.message.answer(text)
    await call.answer()

# ---------- RUN ----------

async def main():
    await db()
    bot = Bot(BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
