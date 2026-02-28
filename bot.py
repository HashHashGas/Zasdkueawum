import os
import asyncio
from decimal import Decimal

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_IDS = os.getenv("ADMIN_IDS", "").strip()  # можно пусто

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")

ADMIN_SET = set()
if ADMIN_IDS:
    for x in ADMIN_IDS.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_SET.add(int(x))

def is_admin(uid: int) -> bool:
    return (not ADMIN_SET) or (uid in ADMIN_SET)

# ---------- ТВОИ ТЕКСТЫ ----------
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

🏦Баланс : <b>{balance}</b>
🛍️Количество заказов : <b>{orders}</b>
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦 Баланс : <b>{balance}</b>
🛍️ Количество заказов : <b>{orders}</b>
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
@gskalye
"""

WORK_TEXT = "A"

# ---------- КНОПКИ ----------
BTN_MAIN = "ГЛАВНАЯ ⚪"
BTN_PROFILE = "ПРОФИЛЬ 👤"
BTN_HELP = "ПОМОЩЬ 💬"
BTN_WORK = "РАБОТА 🛠️"

def reply_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MAIN), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_WORK)],
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def kb_main_city():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одесса ⚓", callback_data="city:odessa")]
    ])

def kb_profile_actions():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
        [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
    ])

def kb_odessa_products():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 saint", callback_data="product:saint")],
        [InlineKeyboardButton(text="2 big bob", callback_data="product:big_bob")],
        [InlineKeyboardButton(text="3 shenen", callback_data="product:shenen")],
    ])

def kb_buy(code: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Купить", callback_data=f"buy:{code}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="city:odessa")],
    ])

def kb_back_profile():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:open")]
    ])

# ---------- ЛОКАЛЬНЫЙ КАТАЛОГ (НЕ ЗАВИСИТ ОТ БАЗЫ, поэтому кнопки 100% работают) ----------
CATALOG = {
    "saint":  {"name": "saint",   "price": Decimal("100.00"), "info": "Описание saint",   "link": "https://example.com/saint"},
    "big_bob":{"name": "big bob", "price": Decimal("150.00"), "info": "Описание big bob", "link": "https://example.com/big_bob"},
    "shenen": {"name": "shenen",  "price": Decimal("200.00"), "info": "Описание shenen",  "link": "https://example.com/shenen"},
}

# ---------- DB ----------
pool: asyncpg.Pool | None = None
bot_ref: Bot | None = None
dp = Dispatcher()

async def db_init():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            awaiting_promo BOOLEAN NOT NULL DEFAULT FALSE,
            main_chat_id BIGINT,
            main_message_id BIGINT,
            profile_chat_id BIGINT,
            profile_message_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_code TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
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
            code TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, code)
        );
        """)

async def ensure_user(uid: int):
    async with pool.acquire() as con:
        await con.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING", uid)

async def get_stats(uid: int):
    await ensure_user(uid)
    async with pool.acquire() as con:
        r = await con.fetchrow("SELECT balance, orders_count FROM users WHERE user_id=$1", uid)
    return Decimal(r["balance"]), int(r["orders_count"])

async def render_main(uid: int):
    bal, ords = await get_stats(uid)
    return MAIN_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=ords)

async def render_profile(uid: int):
    bal, ords = await get_stats(uid)
    return PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=ords)

async def set_refs(uid: int, kind: str, chat_id: int, msg_id: int):
    c1 = "main_chat_id" if kind == "main" else "profile_chat_id"
    c2 = "main_message_id" if kind == "main" else "profile_message_id"
    async with pool.acquire() as con:
        await con.execute(f"UPDATE users SET {c1}=$1, {c2}=$2 WHERE user_id=$3", chat_id, msg_id, uid)

async def refresh_saved(uid: int, kind: str):
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        r = await con.fetchrow("SELECT main_chat_id,main_message_id,profile_chat_id,profile_message_id FROM users WHERE user_id=$1", uid)
    if not r:
        return
    try:
        if kind == "main" and r["main_chat_id"] and r["main_message_id"]:
            await bot_ref.edit_message_text(
                chat_id=int(r["main_chat_id"]),
                message_id=int(r["main_message_id"]),
                text=await render_main(uid),
                reply_markup=kb_main_city(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        if kind == "profile" and r["profile_chat_id"] and r["profile_message_id"]:
            await bot_ref.edit_message_text(
                chat_id=int(r["profile_chat_id"]),
                message_id=int(r["profile_message_id"]),
                text=await render_profile(uid),
                reply_markup=kb_profile_actions(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    except Exception:
        pass

# ---------- PROMO ----------
async def promo_begin(uid: int):
    await ensure_user(uid)
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET awaiting_promo=TRUE WHERE user_id=$1", uid)

async def promo_apply(uid: int, code_in: str):
    code_in = " ".join((code_in or "").strip().split())
    if not code_in:
        return False, "❌ Пустой промокод."

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                """
                SELECT code, amount, uses_left
                FROM promo_codes
                WHERE lower(code)=lower($1) AND is_active=TRUE AND uses_left>0
                FOR UPDATE
                """,
                code_in
            )
            if not row:
                return False, "❌ Промокод недействителен или уже использован."

            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                uid, row["code"]
            )
            if used:
                return False, "❌ Вы уже активировали этот промокод."

            amount = Decimal(row["amount"])
            await con.execute("UPDATE users SET balance=balance+$1, awaiting_promo=FALSE WHERE user_id=$2", amount, uid)
            await con.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=$1", row["code"])
            await con.execute("INSERT INTO promo_activations(user_id,code,amount) VALUES($1,$2,$3)", uid, row["code"], amount)

    return True, f"✅ Промокод <b>{row['code']}</b> активирован!\n➕ Начислено: <b>{amount:.2f}</b>"

# ---------- HANDLERS ----------
@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer(await render_main(message.from_user.id), reply_markup=reply_menu(), parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == BTN_MAIN)
async def on_main(message: Message):
    msg = await message.answer(await render_main(message.from_user.id), reply_markup=kb_main_city(), parse_mode="HTML", disable_web_page_preview=True)
    await set_refs(message.from_user.id, "main", msg.chat.id, msg.message_id)

@dp.message(F.text == BTN_PROFILE)
async def on_profile(message: Message):
    msg = await message.answer(await render_profile(message.from_user.id), reply_markup=kb_profile_actions(), parse_mode="HTML", disable_web_page_preview=True)
    await set_refs(message.from_user.id, "profile", msg.chat.id, msg.message_id)

@dp.message(F.text == BTN_HELP)
async def on_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=reply_menu(), disable_web_page_preview=True)

@dp.message(F.text == BTN_WORK)
async def on_work(message: Message):
    await message.answer(WORK_TEXT, reply_markup=reply_menu())

# ловим ввод промо только когда включен режим
@dp.message()
async def catch_text(message: Message):
    uid = message.from_user.id
    await ensure_user(uid)
    async with pool.acquire() as con:
        awaiting = await con.fetchval("SELECT awaiting_promo FROM users WHERE user_id=$1", uid)

    if not awaiting:
        return

    ok, txt = await promo_apply(uid, message.text)
    await message.answer(txt, parse_mode="HTML", disable_web_page_preview=True)
    if ok:
        await refresh_saved(uid, "main")
        await refresh_saved(uid, "profile")

# callbacks
@dp.callback_query(F.data == "profile:open")
async def cb_profile_open(call: CallbackQuery):
    await call.answer()
    msg = await call.message.answer(await render_profile(call.from_user.id), reply_markup=kb_profile_actions(), parse_mode="HTML", disable_web_page_preview=True)
    await set_refs(call.from_user.id, "profile", msg.chat.id, msg.message_id)

@dp.callback_query(F.data == "profile:topup")
async def cb_topup(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Пополнение скоро будет.", reply_markup=kb_back_profile())

@dp.callback_query(F.data == "profile:promo")
async def cb_promo(call: CallbackQuery):
    await call.answer()
    await promo_begin(call.from_user.id)
    await call.message.answer("🎟 Введите промокод одним сообщением:")

@dp.callback_query(F.data == "profile:history")
async def cb_history(call: CallbackQuery):
    await call.answer()
    async with pool.acquire() as con:
        rows = await con.fetch("SELECT product_name, price, link FROM purchases WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20", call.from_user.id)
    if not rows:
        await call.message.answer("🧾 История покупок пуста.", reply_markup=kb_back_profile())
        return
    out = ["🧾 <b>История покупок:</b>\n"]
    for r in rows:
        out.append(f"• <b>{r['product_name']}</b> — {Decimal(r['price']):.2f}\n{r['link']}\n")
    await call.message.answer("\n".join(out), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb_back_profile())

@dp.callback_query(F.data == "city:odessa")
async def cb_city(call: CallbackQuery):
    await call.answer()
    await call.message.answer("✅ Вы выбрали город Одесса. Выберите товар:", reply_markup=kb_odessa_products())

@dp.callback_query(F.data.startswith("product:"))
async def cb_product(call: CallbackQuery):
    await call.answer()
    code = (call.data or "").split(":", 1)[1]
    item = CATALOG.get(code)
    if not item:
        await call.message.answer("Товар не найден.")
        return
    text = f"📦 <b>{item['name']}</b>\n💳 Цена: <b>{item['price']:.2f}</b>\n\n{item['info']}\n\n{item['link']}"
    await call.message.answer(text, reply_markup=kb_buy(code), parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    code = (call.data or "").split(":", 1)[1]
    item = CATALOG.get(code)
    if not item:
        await call.message.answer("Товар не найден.")
        return

    await ensure_user(uid)
    price = item["price"]

    async with pool.acquire() as con:
        async with con.transaction():
            u = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", uid)
            bal = Decimal(u["balance"])
            if bal < price:
                await call.message.answer("Недостаточно средств 😔")
                return

            await con.execute("UPDATE users SET balance=balance-$1, orders_count=orders_count+1 WHERE user_id=$2", price, uid)
            await con.execute(
                "INSERT INTO purchases(user_id, product_code, product_name, price, link) VALUES($1,$2,$3,$4,$5)",
                uid, code, item["name"], price, item["link"]
            )

    await call.message.answer(f"✅ Покупка успешна: <b>{item['name']}</b>\n💳 Списано: <b>{price:.2f}</b>", parse_mode="HTML")
    await refresh_saved(uid, "main")
    await refresh_saved(uid, "profile")

@dp.callback_query()
async def cb_unknown(call: CallbackQuery):
    await call.answer("Кнопка устарела. Нажми ГЛАВНАЯ ⚪ и пробуй снова.", show_alert=True)

# админ: создать промокод
@dp.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: /addpromo CODE AMOUNT [USES]")
        return
    code = parts[1].strip()
    amount = Decimal(parts[2])
    uses = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO promo_codes(code,amount,is_active,uses_left)
            VALUES($1,$2,TRUE,$3)
            ON CONFLICT (code) DO UPDATE
            SET amount=EXCLUDED.amount, is_active=TRUE, uses_left=EXCLUDED.uses_left
            """,
            code, amount, uses
        )
    await message.answer(f"✅ Промокод создан: {code} (+{amount:.2f}, uses={uses})")

async def main():
    global bot_ref
    await db_init()
    bot_ref = Bot(token=BOT_TOKEN)
    await bot_ref.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot_ref)

if __name__ == "__main__":
    asyncio.run(main())
