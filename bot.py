import os
import asyncio
import decimal
import asyncpg
import aiohttp
import uuid
from urllib.parse import quote

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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ================== ENV ==================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
ADMIN_ID_RAW = (os.getenv("ADMIN_ID") or "").strip()

PAYSYNC_APIKEY = (os.getenv("PAYSYNC_APIKEY") or "").strip()
PAYSYNC_CLIENT_ID = (os.getenv("PAYSYNC_CLIENT_ID") or "").strip()
PAYSYNC_CURRENCY = (os.getenv("PAYSYNC_CURRENCY") or "UAH").strip().upper()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID_RAW or not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID is missing or invalid")
if not PAYSYNC_APIKEY:
    raise RuntimeError("PAYSYNC_APIKEY is missing")
if not PAYSYNC_CLIENT_ID or not PAYSYNC_CLIENT_ID.isdigit():
    raise RuntimeError("PAYSYNC_CLIENT_ID is missing or invalid (must be digits)")

ADMIN_ID = int(ADMIN_ID_RAW)
CLIENT_ID = int(PAYSYNC_CLIENT_ID)

UAH = "₴"


# ================== TEXTS ==================
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
https://t.me/+lsgvuPdI01U0M2My

🥇Отзывы :
https://t.me/+JW5Au3ZS4YM1MTli

Оператор/Сапорт : 
@potterspotter 

🏦Баланс : {balance} {uah}
🛍️Количество заказов : {orders}
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦Баланс : {balance} {uah}
🛍️Количество заказов : {orders}
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
@gskalye
"""

WORK_TEXT = "X"  # заменишь сам

ITEM_TEXT_TEMPLATE = """✅ Вы выбрали: {name}

Цена: {price} {uah}

{desc}
"""

DISTRICT_TEXT = """📍 Выберите способ оплаты:"""
TOPUP_ASK_TEXT = f"💳 Введите сумму пополнения в гривнах ({UAH}) целым числом:\nНапример: 150"


# ================== KEYBOARDS ==================
def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
    )


def inline_main_city() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Одесса", callback_data="city:odesa")]]
    )


def inline_one_button(text: str, cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=cb)]])


def inline_pay_buttons(product_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Балансом", callback_data=f"pay:bal:{product_code}")],
            [InlineKeyboardButton(text="Картой (PaySync)", callback_data=f"pay:card:{product_code}")],
        ]
    )


def inline_profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
        ]
    )


def inline_check_only(trade_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{trade_id}")]]
    )


# ================== DB ==================
pool: asyncpg.Pool | None = None


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def normalize_code(raw: str) -> str:
    return (raw or "").strip()


def parse_int_amount(text: str) -> int | None:
    try:
        s = (text or "").strip().replace(",", ".")
        d = decimal.Decimal(s)
        if d <= 0:
            return None
        d2 = d.quantize(decimal.Decimal("1"))
        if d2 != d:
            return None
        return int(d2)
    except Exception:
        return None


def price_to_int_uah(price: decimal.Decimal) -> int | None:
    p = price.quantize(decimal.Decimal("0.01"))
    if p != p.quantize(decimal.Decimal("1.00")):
        return None
    return int(p)


def safe_int_from_paysync_amount(val) -> int | None:
    """
    PaySync может вернуть amount как "5000" или 5000 или "5000.00".
    Нам нужен INT (гривны целым числом) для сообщения.
    """
    try:
        d = decimal.Decimal(str(val).replace(",", ".").strip())
        d2 = d.quantize(decimal.Decimal("1"))
        if d2 <= 0:
            return None
        return int(d2)
    except Exception:
        return None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            link TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        # MIGRATIONS purchases
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS product_code TEXT")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS item_name TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS price NUMERIC(12,2) NOT NULL DEFAULT 0")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS link TEXT NOT NULL DEFAULT ''")

        await con.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            amount NUMERIC(12,2) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            uses_left INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS promo_activations (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            code TEXT NOT NULL REFERENCES promo_codes(code),
            activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, code)
        )
        """)

        # invoices:
        # amount      = "логическая сумма" (цена товара / сумма пополнения, которую мы зачислим)
        # amount_int  = "к оплате по PaySync" (то, что PaySync реально требует отправить)
        await con.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            trade_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,                 -- 'topup' | 'product'
            amount_int INT NOT NULL DEFAULT 0,  -- PaySync: сумма к оплате (с комиссией если на плательщике)
            amount INT NOT NULL DEFAULT 0,      -- наша "логическая сумма" (зачисление/цена)
            currency TEXT NOT NULL DEFAULT 'UAH',
            product_code TEXT,
            card_number TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'wait', -- 'wait' | 'paid' | 'done'
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        # MIGRATIONS invoices
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS amount_int INT NOT NULL DEFAULT 0")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS amount INT NOT NULL DEFAULT 0")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS product_code TEXT")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS card_number TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'wait'")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'UAH'")


async def ensure_user(user_id: int) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )


async def get_user_stats(user_id: int) -> tuple[decimal.Decimal, int]:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id,
        )
    if not row:
        return decimal.Decimal("0.00"), 0
    return decimal.Decimal(row["balance"]), int(row["orders_count"])


async def render_main_text(user_id: int) -> str:
    await ensure_user(user_id)
    bal, orders = await get_user_stats(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=orders, uah=UAH)


async def get_city_products(city: str, limit: int = 20) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetch(
            """
            SELECT code, name, price
            FROM products
            WHERE city=$1 AND is_active=TRUE
            ORDER BY created_at DESC
            LIMIT $2
            """,
            city, limit
        )


def inline_city_products(rows: list[asyncpg.Record], city: str) -> InlineKeyboardMarkup:
    if not rows:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Нет товаров", callback_data="noop")]])
    kb = []
    for r in rows:
        name = str(r["name"])
        code = str(r["code"])
        price = decimal.Decimal(r["price"])
        kb.append([InlineKeyboardButton(text=f"{name} — {price:.2f} {UAH}", callback_data=f"prod:{city}:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def get_product(code: str) -> asyncpg.Record | None:
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetchrow(
            "SELECT code, city, name, price, link, description, is_active FROM products WHERE code=$1",
            code
        )


async def add_or_update_product(city: str, code: str, name: str, price: decimal.Decimal, link: str, desc: str) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO products(code, city, name, price, link, description, is_active)
            VALUES($1,$2,$3,$4,$5,$6,TRUE)
            ON CONFLICT (code) DO UPDATE SET
                city=EXCLUDED.city,
                name=EXCLUDED.name,
                price=EXCLUDED.price,
                link=EXCLUDED.link,
                description=EXCLUDED.description,
                is_active=TRUE
            """,
            code, city, name, price, link, desc
        )


async def deactivate_product(code: str) -> bool:
    assert pool is not None
    async with pool.acquire() as con:
        res = await con.execute("UPDATE products SET is_active=FALSE WHERE code=$1", code)
    return res.startswith("UPDATE")


async def get_history(user_id: int) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetch(
            """
            SELECT item_name, link, price, created_at
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 20
            """,
            user_id,
        )


async def activate_promo(user_id: int, raw_code: str) -> tuple[bool, str]:
    code = normalize_code(raw_code)
    if not code:
        return False, "❌ Введи промокод текстом."

    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow(
                """
                SELECT code, amount, is_active, uses_left
                FROM promo_codes
                WHERE upper(code) = upper($1)
                FOR UPDATE
                """,
                code,
            )

            if not promo or not promo["is_active"] or int(promo["uses_left"]) <= 0:
                return False, "❌ Промокод недействителен."

            real_code = str(promo["code"])
            amount = decimal.Decimal(promo["amount"])

            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                user_id, real_code
            )
            if used:
                return False, "❌ Ты уже активировал этот промокод."

            await con.execute(
                "INSERT INTO promo_activations(user_id, code) VALUES($1, $2)",
                user_id, real_code
            )
            await con.execute(
                "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
                real_code
            )
            await con.execute(
                "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                user_id, amount
            )

    return True, f"✅ Промокод активирован!\n🏦 Начислено: {amount:.2f} {UAH}"


# ================== BUY WITH BALANCE ==================
async def buy_with_balance(user_id: int, product_code: str) -> tuple[bool, str]:
    await ensure_user(user_id)

    product = await get_product(product_code)
    if not product or not product["is_active"]:
        return False, "❌ Товар недоступен."

    price = decimal.Decimal(product["price"])
    name = str(product["name"])
    link = str(product["link"] or "").strip()
    if not link:
        return False, "❌ Для этого товара ещё не добавлена ссылка."

    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                "SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE",
                user_id
            )
            bal = decimal.Decimal(row["balance"])
            if bal < price:
                return False, f"❌ Недостаточно средств.\nНужно: {price:.2f} {UAH}\nУ тебя: {bal:.2f} {UAH}"

            await con.execute(
                "UPDATE users SET balance = balance - $2, orders_count = orders_count + 1 WHERE user_id=$1",
                user_id, price
            )

            await con.execute(
                """
                INSERT INTO purchases(user_id, product_code, item_name, price, link)
                VALUES($1,$2,$3,$4,$5)
                """,
                user_id, product_code, name, price, link
            )

    return True, f"✅ Покупка успешна: {name}\nСписано: {price:.2f} {UAH}\n\n🔗 Твоя ссылка:\n{link}"


# ================== PaySync H2H ==================
async def paysync_h2h_create(amount_int: int, currency: str, data: str) -> dict:
    data_q = quote(data or "")
    url = f"https://paysync.bot/api/client{CLIENT_ID}/amount{amount_int}/currency{currency}?data={data_q}"
    headers = {"Content-Type": "application/json", "apikey": PAYSYNC_APIKEY}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=30) as resp:
            try:
                js = await resp.json()
            except Exception:
                txt = await resp.text()
                raise RuntimeError(f"PaySync H2H bad response: {txt[:300]}")
    return js


async def paysync_gettrans(trade_id: str) -> dict:
    url = f"https://paysync.bot/gettrans/{trade_id}"
    headers = {"Content-Type": "application/json", "apikey": PAYSYNC_APIKEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=30) as resp:
            try:
                return await resp.json()
            except Exception:
                txt = await resp.text()
                raise RuntimeError(f"PaySync gettrans bad response: {txt[:300]}")


async def invoice_create(user_id: int, kind: str, logical_amount_int: int, product_code: str | None) -> asyncpg.Record:
    """
    logical_amount_int = цена товара/пополнения (то, что мы зачислим или считаем ценой)
    PaySync может вернуть amount = сумма к оплате (с комиссией если она на плательщике)
    """
    await ensure_user(user_id)

    nonce = uuid.uuid4().hex[:10]
    data = f"{kind}:{user_id}:{product_code or '-'}:{nonce}"

    js = await paysync_h2h_create(logical_amount_int, PAYSYNC_CURRENCY, data)

    trade = js.get("trade")
    card_number = js.get("card_number") or ""
    status = (js.get("status") or "wait").lower()
    currency = js.get("currency") or PAYSYNC_CURRENCY

    if not trade:
        raise RuntimeError(f"PaySync create missing 'trade': {js}")

    # ✅ ВАЖНО: amount_to_pay берём из ответа PaySync (если пришёл), иначе fallback на logical
    amount_to_pay_int = safe_int_from_paysync_amount(js.get("amount"))
    if amount_to_pay_int is None:
        amount_to_pay_int = logical_amount_int

    trade_id = str(trade)

    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO invoices(trade_id, user_id, kind, amount_int, amount, currency, product_code, card_number, status)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (trade_id) DO UPDATE SET
              user_id=EXCLUDED.user_id,
              kind=EXCLUDED.kind,
              amount_int=EXCLUDED.amount_int,
              amount=EXCLUDED.amount,
              currency=EXCLUDED.currency,
              product_code=EXCLUDED.product_code,
              card_number=EXCLUDED.card_number,
              status=EXCLUDED.status
            """,
            trade_id, user_id, kind,
            amount_to_pay_int,                 # PaySync amount (что реально отправлять)
            logical_amount_int,                # наша логическая сумма (что зачисляем/цена)
            str(currency), product_code, str(card_number), str(status)
        )

        inv = await con.fetchrow("SELECT * FROM invoices WHERE trade_id=$1", trade_id)

    if not inv:
        raise RuntimeError("DB error: invoice not saved")
    return inv


async def invoice_apply_paid(trade_id: str) -> tuple[bool, str]:
    assert pool is not None

    js = await paysync_gettrans(trade_id)
    status = (js.get("status") or "").lower()
    if status != "paid":
        return False, "❌ Оплата ещё не подтверждена."

    async with pool.acquire() as con:
        inv = await con.fetchrow("SELECT * FROM invoices WHERE trade_id=$1", trade_id)

    if not inv:
        return False, "❌ Заявка не найдена в базе (trade_id не найден)."

    if str(inv["status"]) in ("paid", "done"):
        if inv["kind"] == "topup":
            return True, "✅ Уже подтверждено ранее. Баланс пополнен."
        return True, "✅ Уже подтверждено ранее. Товар уже выдан."

    user_id = int(inv["user_id"])
    kind = str(inv["kind"])
    logical_amount_int = int(inv["amount"])      # ✅ пополняем/цена = логическая сумма
    product_code = inv["product_code"]

    if kind == "topup":
        # ✅ начисляем именно logical_amount_int
        add_sum = decimal.Decimal(logical_amount_int).quantize(decimal.Decimal("0.01"))
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                    user_id, add_sum
                )
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)

        return True, f"✅ Оплата подтверждена.\n🏦 Баланс пополнен на {logical_amount_int} {UAH}"

    if kind == "product":
        if not product_code:
            async with pool.acquire() as con:
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)
            return True, "✅ Оплата подтверждена, но товар не привязан. Напиши оператору."

        product = await get_product(str(product_code))
        if not product or not product["is_active"]:
            async with pool.acquire() as con:
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)
            return True, "✅ Оплата подтверждена, но товар сейчас недоступен. Напиши оператору."

        name = str(product["name"])
        link = str(product["link"] or "").strip()
        price = decimal.Decimal(product["price"])

        if not link:
            async with pool.acquire() as con:
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)
            return True, "✅ Оплата подтверждена, но ссылка на товар не добавлена. Напиши оператору."

        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    "UPDATE users SET orders_count = orders_count + 1 WHERE user_id=$1",
                    user_id
                )
                await con.execute(
                    """
                    INSERT INTO purchases(user_id, product_code, item_name, price, link)
                    VALUES($1,$2,$3,$4,$5)
                    """,
                    user_id, str(product_code), name, price, link
                )
                await con.execute("UPDATE invoices SET status='done' WHERE trade_id=$1", trade_id)

        return True, f"✅ Оплата подтверждена.\n✅ Покупка успешна: {name}\n\n🔗 Твоя ссылка:\n{link}"

    return False, "❌ Неизвестный тип заявки."


def render_h2h_message(inv: asyncpg.Record) -> str:
    trade_id = str(inv["trade_id"])
    amount_to_pay_int = int(inv["amount_int"])   # ✅ то, что PaySync реально требует
    currency = str(inv["currency"])
    card = str(inv["card_number"] or "").strip()
    if not card:
        card = "— (карта не выдана API)"

    return (
        f"💳 Оплата через PaySync\n\n"
        f"🧾 Номер заявки: {trade_id}\n"
        f"💳 Реквизиты для оплаты: {card}\n"
        f"💰 Сумма к оплате: {amount_to_pay_int} {currency}\n\n"
        f"❗️Оплачивай одним платежом и точно в сумме.\n"
        f"После оплаты нажми «✅ Проверить оплату»."
    )


# ================== FSM ==================
class PromoStates(StatesGroup):
    waiting_code = State()


class TopupStates(StatesGroup):
    waiting_amount = State()


# ================== BOT ==================
dp = Dispatcher(storage=MemoryStorage())


# ================== HANDLERS ==================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=bottom_menu())


@dp.message(F.text.contains("ГЛАВНАЯ"))
async def btn_main(message: Message):
    text = await render_main_text(message.from_user.id)
    await message.answer(text, reply_markup=inline_main_city())


@dp.message(F.text.contains("ПРОФИЛЬ"))
async def btn_profile(message: Message):
    await ensure_user(message.from_user.id)
    bal, orders = await get_user_stats(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=orders, uah=UAH)
    await message.answer(text, reply_markup=inline_profile_menu())


@dp.message(F.text.contains("ПОМОЩЬ"))
async def btn_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=bottom_menu())


@dp.message(F.text.contains("РАБОТА"))
async def btn_work(message: Message):
    await message.answer(WORK_TEXT, reply_markup=bottom_menu())


@dp.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@dp.callback_query(F.data == "city:odesa")
async def cb_city_odesa(call: CallbackQuery):
    await call.answer()
    rows = await get_city_products("odesa")
    await call.message.answer(
        "✅ Вы выбрали город Одесса.\nВыберите товар:",
        reply_markup=inline_city_products(rows, "odesa")
    )


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(call: CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    if len(parts) != 3:
        return
    code = parts[2]

    product = await get_product(code)
    if not product or not product["is_active"]:
        await call.message.answer("❌ Товар недоступен.")
        return

    name = str(product["name"])
    price = decimal.Decimal(product["price"])
    desc = str(product["description"] or "").strip() or " "

    text = ITEM_TEXT_TEMPLATE.format(name=name, price=f"{price:.2f}", uah=UAH, desc=desc)
    await call.message.answer(text, reply_markup=inline_one_button("Район", f"district:{code}"))


@dp.callback_query(F.data.startswith("district:"))
async def cb_district(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":", 1)[1]
    await call.message.answer(DISTRICT_TEXT, reply_markup=inline_pay_buttons(code))


@dp.callback_query(F.data.startswith("pay:bal:"))
async def cb_pay_balance(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]
    try:
        ok, msg = await buy_with_balance(call.from_user.id, code)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка оплаты балансом: {e}")
        return
    await call.message.answer(msg)


@dp.callback_query(F.data.startswith("pay:card:"))
async def cb_pay_card(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]

    product = await get_product(code)
    if not product or not product["is_active"]:
        await call.message.answer("❌ Товар недоступен.")
        return

    price = decimal.Decimal(product["price"])
    logical_amount_int = price_to_int_uah(price)
    if logical_amount_int is None:
        await call.message.answer("❌ Для оплаты картой цена товара должна быть целым числом (например 350.00).")
        return

    try:
        inv = await invoice_create(call.from_user.id, "product", logical_amount_int, code)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    await call.message.answer(render_h2h_message(inv), reply_markup=inline_check_only(str(inv["trade_id"])))


@dp.callback_query(F.data.startswith("check:"))
async def cb_check(call: CallbackQuery):
    await call.answer()
    trade_id = call.data.split(":", 1)[1]
    try:
        ok, msg = await invoice_apply_paid(trade_id)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка проверки оплаты: {e}")
        return
    await call.message.answer(msg)


@dp.callback_query(F.data == "profile:topup")
async def cb_profile_topup(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TopupStates.waiting_amount)
    await call.message.answer(TOPUP_ASK_TEXT)


@dp.message(TopupStates.waiting_amount)
async def topup_amount_entered(message: Message, state: FSMContext):
    logical_amount_int = parse_int_amount(message.text)
    if logical_amount_int is None:
        await message.answer("❌ Введи сумму целым числом. Пример: 200")
        return

    if logical_amount_int < 10:
        await message.answer(f"❌ Минимум 10 {UAH}.")
        return

    try:
        inv = await invoice_create(message.from_user.id, "topup", logical_amount_int, None)
    except Exception as e:
        await message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    await message.answer(render_h2h_message(inv), reply_markup=inline_check_only(str(inv["trade_id"])))
    await state.clear()


@dp.callback_query(F.data == "profile:promo")
async def cb_profile_promo(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoStates.waiting_code)
    await call.message.answer("🎟 Введи промокод одним сообщением:")


@dp.message(PromoStates.waiting_code)
async def promo_entered(message: Message, state: FSMContext):
    await ensure_user(message.from_user.id)
    ok, msg = await activate_promo(message.from_user.id, message.text)
    await message.answer(msg)
    await state.clear()


@dp.callback_query(F.data == "profile:history")
async def cb_profile_history(call: CallbackQuery):
    await call.answer()
    rows = await get_history(call.from_user.id)
    if not rows:
        await call.message.answer("История пуста.")
        return

    text = "🧾 История покупок:\n\n"
    for r in rows:
        dt = r["created_at"].strftime("%Y-%m-%d %H:%M")
        price = decimal.Decimal(r["price"])
        text += f"• {r['item_name']} — {price:.2f} {UAH} ({dt})\n{r['link']}\n\n"
    await call.message.answer(text)


# ================== ADMIN COMMANDS ==================
@dp.message(F.text.startswith("/addproduct"))
async def cmd_addproduct(message: Message):
    if not is_admin(message.from_user.id):
        return

    raw = message.text.strip()
    try:
        parts = [p.strip() for p in raw[len("/addproduct"):].strip().split("|")]
        if len(parts) < 5:
            await message.answer("Формат:\n/addproduct city | code | name | price | link | desc(опц.)")
            return

        city = parts[0].lower()
        code = parts[1].strip()
        name = parts[2].strip()
        price = decimal.Decimal(parts[3].replace(",", "."))
        link = parts[4].strip()
        desc = parts[5].strip() if len(parts) >= 6 else ""

        if not code:
            await message.answer("❌ code пустой.")
            return
        if not name:
            await message.answer("❌ name пустой.")
            return
        if not link:
            await message.answer("❌ link пустой.")
            return

        await add_or_update_product(city, code, name, price, link, desc)
        await message.answer(f"✅ Товар сохранён: {code} ({name}) — {price:.2f} {UAH}")

    except Exception as e:
        await message.answer(f"❌ Ошибка формата: {e}")


@dp.message(F.text.startswith("/delproduct"))
async def cmd_delproduct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /delproduct CODE")
        return
    code = parts[1].strip()
    ok = await deactivate_product(code)
    await message.answer("✅ Отключено." if ok else "❌ Не найдено.")


@dp.message(F.text.startswith("/products"))
async def cmd_products(message: Message):
    if not is_admin(message.from_user.id):
        return
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT city, code, name, price, is_active FROM products ORDER BY created_at DESC LIMIT 50"
        )
    if not rows:
        await message.answer("Товаров нет.")
        return
    text = "Товары:\n\n"
    for r in rows:
        text += f"{r['city']} | {r['code']} | {r['name']} | {decimal.Decimal(r['price']):.2f} {UAH} | {'ON' if r['is_active'] else 'OFF'}\n"
    await message.answer(text)


async def main():
    await db_init()
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
