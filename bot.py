import os
import asyncio
from decimal import Decimal

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")

BTN_MAIN = "ГЛАВНАЯ ⚪"
BTN_PROFILE = "ПРОФИЛЬ 👤"
BTN_HELP = "ПОМОЩЬ 💬"
BTN_WORK = "РАБОТА 🛠️"

OPERATOR = "@gskalye"

pool: asyncpg.Pool | None = None
bot_ref: Bot | None = None

dp = Dispatcher()


# ---------- UI ----------
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

def kb_profile():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Top up", callback_data="profile:topup")],
        [InlineKeyboardButton(text="🎟 Promo", callback_data="profile:promo")],
        [InlineKeyboardButton(text="🧾 History", callback_data="profile:history")],
    ])

def kb_odessa_items():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1) Item 1", callback_data="item:i1")],
        [InlineKeyboardButton(text="2) Item 2", callback_data="item:i2")],
        [InlineKeyboardButton(text="3) Item 3", callback_data="item:i3")],
    ])

def kb_buy(code: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Buy", callback_data=f"buy:{code}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="city:odessa")],
    ])


# ---------- DB ----------
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

        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            info TEXT NOT NULL,
            link TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
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

        # default products if empty
        c = await con.fetchval("SELECT COUNT(*) FROM products")
        if int(c) == 0:
            defaults = [
                ("i1", "Item 1", Decimal("100.00"), "Info for Item 1", "https://example.com/i1"),
                ("i2", "Item 2", Decimal("150.00"), "Info for Item 2", "https://example.com/i2"),
                ("i3", "Item 3", Decimal("200.00"), "Info for Item 3", "https://example.com/i3"),
            ]
            for code, name, price, info, link in defaults:
                await con.execute(
                    "INSERT INTO products(code,name,price,info,link,is_active) VALUES($1,$2,$3,$4,$5,TRUE)",
                    code, name, price, info, link
                )

async def ensure_user(uid: int):
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            uid
        )

async def get_stats(uid: int):
    await ensure_user(uid)
    async with pool.acquire() as con:
        r = await con.fetchrow("SELECT balance, orders_count FROM users WHERE user_id=$1", uid)
    bal = Decimal(r["balance"])
    ords = int(r["orders_count"])
    return bal, ords

async def set_refs(uid: int, kind: str, chat_id: int, msg_id: int):
    colc = "main_chat_id" if kind == "main" else "profile_chat_id"
    colm = "main_message_id" if kind == "main" else "profile_message_id"
    async with pool.acquire() as con:
        await con.execute(f"UPDATE users SET {colc}=$1, {colm}=$2 WHERE user_id=$3", chat_id, msg_id, uid)

async def render_main(uid: int):
    bal, ords = await get_stats(uid)
    return (
        "✋🏻 Hello, Cavalier 🎩\n"
        "👑 Cavalier Shop 👑\n\n"
        f"🏦 Balance: <b>{bal:.2f}</b>\n"
        f"🛍️ Orders: <b>{ords}</b>\n\n"
        f"Support: {OPERATOR}"
    )

async def render_profile(uid: int):
    bal, ords = await get_stats(uid)
    return (
        "👤 Profile\n\n"
        f"🏦 Balance: <b>{bal:.2f}</b>\n"
        f"🛍️ Orders: <b>{ords}</b>"
    )

async def refresh_saved(uid: int, which: str):
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        r = await con.fetchrow(
            "SELECT main_chat_id,main_message_id,profile_chat_id,profile_message_id FROM users WHERE user_id=$1",
            uid
        )
    if not r:
        return

    if which == "main" and r["main_chat_id"] and r["main_message_id"]:
        try:
            await bot_ref.edit_message_text(
                chat_id=int(r["main_chat_id"]),
                message_id=int(r["main_message_id"]),
                text=await render_main(uid),
                reply_markup=kb_main_city(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            pass

    if which == "profile" and r["profile_chat_id"] and r["profile_message_id"]:
        try:
            await bot_ref.edit_message_text(
                chat_id=int(r["profile_chat_id"]),
                message_id=int(r["profile_message_id"]),
                text=await render_profile(uid),
                reply_markup=kb_profile(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            pass


# ---------- Promo (NO FSM) ----------
async def promo_begin(uid: int):
    await ensure_user(uid)
    async with pool.acquire() as con:
        await con.execute("UPDATE users SET awaiting_promo=TRUE WHERE user_id=$1", uid)

async def promo_apply(uid: int, code_in: str):
    code_in = " ".join((code_in or "").strip().split())
    if not code_in:
        return False, "Empty promo"

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
                return False, "Invalid promo"

            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                uid, row["code"]
            )
            if used:
                return False, "Already used"

            amount = Decimal(row["amount"])

            await con.execute("UPDATE users SET balance=balance+$1, awaiting_promo=FALSE WHERE user_id=$2", amount, uid)
            await con.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=$1", row["code"])
            await con.execute(
                "INSERT INTO promo_activations(user_id,code,amount) VALUES($1,$2,$3)",
                uid, row["code"], amount
            )
    return True, f"Promo OK +{amount:.2f}"


# ---------- Handlers ----------
@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer(
        await render_main(message.from_user.id),
        reply_markup=reply_menu(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@dp.message(F.text == BTN_MAIN)
async def on_main(message: Message):
    msg = await message.answer(
        await render_main(message.from_user.id),
        reply_markup=kb_main_city(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await set_refs(message.from_user.id, "main", msg.chat.id, msg.message_id)

@dp.message(F.text == BTN_PROFILE)
async def on_profile(message: Message):
    msg = await message.answer(
        await render_profile(message.from_user.id),
        reply_markup=kb_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await set_refs(message.from_user.id, "profile", msg.chat.id, msg.message_id)

@dp.message(F.text == BTN_HELP)
async def on_help(message: Message):
    await message.answer(f"Support: {OPERATOR}", reply_markup=reply_menu())

@dp.message(F.text == BTN_WORK)
async def on_work(message: Message):
    await message.answer("A", reply_markup=reply_menu())

# promo input catcher (after user pressed Promo)
@dp.message()
async def catch_text(message: Message):
    uid = message.from_user.id
    await ensure_user(uid)

    async with pool.acquire() as con:
        awaiting = await con.fetchval("SELECT awaiting_promo FROM users WHERE user_id=$1", uid)

    if not awaiting:
        return  # ignore other random texts

    ok, txt = await promo_apply(uid, message.text)
    if ok:
        await message.answer(f"✅ {txt}")
        await refresh_saved(uid, "main")
        await refresh_saved(uid, "profile")
    else:
        # keep awaiting_promo TRUE if invalid, so user can try again
        await message.answer(f"❌ {txt}")

@dp.callback_query(F.data == "profile:promo")
async def cb_profile_promo(call: CallbackQuery):
    await call.answer()
    await promo_begin(call.from_user.id)
    await call.message.answer("Send promo code:")

@dp.callback_query(F.data == "profile:history")
async def cb_history(call: CallbackQuery):
    await call.answer()
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT product_name, price, link
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 20
            """,
            call.from_user.id
        )
    if not rows:
        await call.message.answer("History empty")
        return
    out = ["History:"]
    for r in rows:
        out.append(f"- {r['product_name']} ({Decimal(r['price']):.2f}) {r['link']}")
    await call.message.answer("\n".join(out))

@dp.callback_query(F.data == "profile:topup")
async def cb_topup(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Top up: soon")

@dp.callback_query(F.data == "city:odessa")
async def cb_city(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Odessa selected. Choose item:", reply_markup=kb_odessa_items())

@dp.callback_query(F.data.startswith("item:"))
async def cb_item(call: CallbackQuery):
    await call.answer()
    code = (call.data or "").split(":", 1)[1]
    async with pool.acquire() as con:
        p = await con.fetchrow(
            "SELECT code,name,price,info,link FROM products WHERE code=$1 AND is_active=TRUE",
            code
        )
    if not p:
        await call.message.answer("Item not found")
        return
    text = f"<b>{p['name']}</b>\nPrice: <b>{Decimal(p['price']):.2f}</b>\n\n{p['info']}\n{p['link']}"
    await call.message.answer(text, reply_markup=kb_buy(p["code"]), parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    code = (call.data or "").split(":", 1)[1]
    await ensure_user(uid)

    async with pool.acquire() as con:
        async with con.transaction():
            p = await con.fetchrow(
                "SELECT code,name,price,link FROM products WHERE code=$1 AND is_active=TRUE FOR UPDATE",
                code
            )
            if not p:
                await call.message.answer("Item not found")
                return

            u = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", uid)
            bal = Decimal(u["balance"])
            price = Decimal(p["price"])
            if bal < price:
                await call.message.answer("Not enough balance")
                return

            await con.execute(
                "UPDATE users SET balance=balance-$1, orders_count=orders_count+1 WHERE user_id=$2",
                price, uid
            )
            await con.execute(
                """
                INSERT INTO purchases(user_id, product_code, product_name, price, link)
                VALUES($1,$2,$3,$4,$5)
                """,
                uid, p["code"], p["name"], price, p["link"]
            )

    await call.message.answer(f"✅ Bought {p['name']} (-{price:.2f})")
    await refresh_saved(uid, "main")
    await refresh_saved(uid, "profile")


async def main():
    global bot_ref
    await db_init()
    bot = Bot(token=BOT_TOKEN)
    bot_ref = bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
