import os
import asyncio
import decimal
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp
import asyncpg

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# =========================
# ENV
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
ADMIN_ID_RAW = (os.getenv("ADMIN_ID") or "").strip()

PAYSYNC_APIKEY = (os.getenv("PAYSYNC_APIKEY") or "").strip()
PAYSYNC_CLIENT_ID = (os.getenv("PAYSYNC_CLIENT_ID") or "").strip()
PAYSYNC_CURRENCY = (os.getenv("PAYSYNC_CURRENCY") or "UAH").strip().upper()

# Для Crypto Pay нужен API token приложения Crypto Pay, а НЕ токен Telegram-бота
CRYPTO_PAY_API_TOKEN = (os.getenv("CRYPTO_PAY_API_TOKEN") or "").strip()
CRYPTO_PAY_BASE_URL = (os.getenv("CRYPTO_PAY_BASE_URL") or "https://pay.crypt.bot/api").strip().rstrip("/")
CRYPTO_PAY_FIAT = (os.getenv("CRYPTO_PAY_FIAT") or "UAH").strip().upper()
CRYPTO_PAY_ACCEPTED_ASSETS = (os.getenv("CRYPTO_PAY_ACCEPTED_ASSETS") or "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC").strip()

RESERVATION_MINUTES = int((os.getenv("RESERVATION_MINUTES") or "15").strip())
PAYMENT_TIMEOUT_MINUTES = int((os.getenv("PAYMENT_TIMEOUT_MINUTES") or "15").strip())

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID is missing or invalid")
if not PAYSYNC_APIKEY:
    raise RuntimeError("PAYSYNC_APIKEY is missing")
if not PAYSYNC_CLIENT_ID.isdigit():
    raise RuntimeError("PAYSYNC_CLIENT_ID is missing or invalid")

ADMIN_ID = int(ADMIN_ID_RAW)
CLIENT_ID = int(PAYSYNC_CLIENT_ID)
UAH = "₴"

# =========================
# TEXTS
# =========================
MAIN_TEXT_TEMPLATE = """Приветствуем Кавалер 🫡

✍🏻О СЕРВИСЕ

°Готовые Товары 💪🏻
°ОПТ ⭕️
°Шустрые смены сортов 💨
°Разновидные способы оплаты 🌐
°Отправки NovaPost 🇺🇦
°Оператор/Сапорт в сети 24/7 🟢

Актуальные ссылки

Бот :
@CavalierShopBot

Оператор/Сапорт :
@Cavalerskiy_supp

🏦Баланс : {balance} {uah}
🛍️Количество заказов : {orders}
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦Баланс : {balance} {uah}
🛍️Количество заказов : {orders}
"""

HELP_TEXT = """По Случаю НеНахода/Имеющихся вопросов, писать :
@Cavalerskiy_supp
"""

WORK_TEXT = "Ищем ответственных сотрудников магазина (Зп 150-200грн/1), подробности @Cavalerskiy_supp"

ITEM_TEXT_TEMPLATE = """✅ Вы выбрали: {name}

Цена: {price} {uah}

{desc}
"""

DISTRICT_TEXT = "📍 Выберите способ оплаты:"
TOPUP_ASK_TEXT = f"💳 Введите сумму пополнения в гривнах ({UAH}) целым числом:\nНапример: 150"

# =========================
# GLOBALS
# =========================
pool: asyncpg.Pool | None = None
dp = Dispatcher(storage=MemoryStorage())

# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def normalize_code(raw: str) -> str:
    return (raw or "").strip()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


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
    try:
        d = decimal.Decimal(str(val).replace(",", ".").strip())
        d2 = d.quantize(decimal.Decimal("1"))
        if d2 <= 0:
            return None
        return int(d2)
    except Exception:
        return None


def decimal_to_str_2(d: decimal.Decimal) -> str:
    return f"{d.quantize(decimal.Decimal('0.01'))}"


def make_nonce() -> str:
    return uuid.uuid4().hex[:16]


# =========================
# KEYBOARDS
# =========================
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
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=cb)]]
    )


def inline_profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
        ]
    )


def inline_pay_buttons(product_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Балансом", callback_data=f"pay:bal:{product_code}")],
            [InlineKeyboardButton(text="Картой (PaySync)", callback_data=f"pay:card:{product_code}")],
            [InlineKeyboardButton(text="Crypto", callback_data=f"pay:crypto:{product_code}")],
        ]
    )


def inline_check_invoice(invoice_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{invoice_id}")]]
    )


# =========================
# DB INIT
# =========================
async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8)

    assert pool is not None
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
            reserved_by BIGINT,
            reserved_until TIMESTAMPTZ,
            sold_to BIGINT,
            sold_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        # Миграции products
        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS reserved_by BIGINT")
        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS reserved_until TIMESTAMPTZ")
        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS sold_to BIGINT")
        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS sold_at TIMESTAMPTZ")

        await con.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            product_code TEXT,
            item_name TEXT NOT NULL DEFAULT '',
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            link TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT 'balance',
            external_payment_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS product_code TEXT")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS item_name TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS price NUMERIC(12,2) NOT NULL DEFAULT 0")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS link TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'balance'")
        await con.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS external_payment_id TEXT NOT NULL DEFAULT ''")

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

        await con.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            external_id TEXT NOT NULL DEFAULT '',
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            kind TEXT NOT NULL,                       -- product | topup
            product_code TEXT,
            logical_amount INT NOT NULL DEFAULT 0,   -- цена товара / сумма пополнения
            amount_to_pay TEXT NOT NULL DEFAULT '',  -- что реально платить
            currency TEXT NOT NULL DEFAULT 'UAH',
            pay_url TEXT NOT NULL DEFAULT '',
            card_number TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'wait',     -- wait | paid | done | expired | cancelled
            expires_at TIMESTAMPTZ,
            payload TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paid_at TIMESTAMPTZ
        )
        """)

        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS external_id TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'paysync'")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'product'")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS product_code TEXT")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS logical_amount INT NOT NULL DEFAULT 0")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS amount_to_pay TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'UAH'")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS pay_url TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS card_number TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'wait'")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payload TEXT NOT NULL DEFAULT ''")
        await con.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ")

        await con.execute("CREATE INDEX IF NOT EXISTS idx_products_city_active ON products(city, is_active)")
        await con.execute("CREATE INDEX IF NOT EXISTS idx_products_reserved_until ON products(reserved_until)")
        await con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user_status ON invoices(user_id, status)")
        await con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_product_code ON invoices(product_code)")


# =========================
# DB HELPERS
# =========================
async def ensure_user(user_id: int) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )


async def get_user_stats(user_id: int) -> tuple[decimal.Decimal, int]:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id
        )
    if not row:
        return decimal.Decimal("0.00"), 0
    return decimal.Decimal(row["balance"]), int(row["orders_count"])


async def render_main_text(user_id: int) -> str:
    await ensure_user(user_id)
    bal, orders = await get_user_stats(user_id)
    return MAIN_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=orders, uah=UAH)


async def cleanup_expired_holds() -> None:
    """
    Снимаем бронь у товаров, у которых истёк срок,
    и помечаем зависшие invoices как expired.
    """
    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            await con.execute("""
                UPDATE products
                SET reserved_by = NULL,
                    reserved_until = NULL
                WHERE reserved_until IS NOT NULL
                  AND reserved_until < NOW()
                  AND sold_at IS NULL
            """)

            await con.execute("""
                UPDATE invoices
                SET status = 'expired'
                WHERE status = 'wait'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
            """)


async def background_cleanup_loop() -> None:
    while True:
        try:
            await cleanup_expired_holds()
        except Exception as e:
            print(f"[cleanup] error: {e}")
        await asyncio.sleep(30)


async def get_city_products(city: str, limit: int = 30) -> list[asyncpg.Record]:
    """
    Показываем только активные, непроданные и не забронированные товары.
    """
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT code, name, price
            FROM products
            WHERE city=$1
              AND is_active=TRUE
              AND sold_at IS NULL
              AND (
                    reserved_until IS NULL
                    OR reserved_until < NOW()
                  )
            ORDER BY created_at DESC
            LIMIT $2
            """,
            city, limit
        )
    return rows


def inline_city_products(rows: list[asyncpg.Record], city: str) -> InlineKeyboardMarkup:
    if not rows:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Нет товаров", callback_data="noop")]]
        )

    kb: list[list[InlineKeyboardButton]] = []
    for r in rows:
        name = str(r["name"])
        code = str(r["code"])
        price = decimal.Decimal(r["price"])
        kb.append([
            InlineKeyboardButton(
                text=f"{name} — {price:.2f} {UAH}",
                callback_data=f"prod:{city}:{code}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def get_product(code: str) -> asyncpg.Record | None:
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT code, city, name, price, link, description, is_active,
                   reserved_by, reserved_until, sold_to, sold_at, created_at
            FROM products
            WHERE code=$1
            """,
            code
        )


async def add_or_update_product(
    city: str,
    code: str,
    name: str,
    price: decimal.Decimal,
    link: str,
    desc: str
) -> None:
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
            SELECT item_name, link, price, provider, created_at
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 20
            """,
            user_id
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
                WHERE upper(code)=upper($1)
                FOR UPDATE
                """,
                code
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


# =========================
# PRODUCT RESERVATION
# =========================
async def reserve_product(user_id: int, product_code: str) -> tuple[bool, str, asyncpg.Record | None]:
    """
    Ставит бронь на товар на PAYMENT_TIMEOUT_MINUTES.
    Если уже забронирован этим же пользователем и бронь жива — ок.
    """
    assert pool is not None
    expires_at = now_utc() + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)

    async with pool.acquire() as con:
        async with con.transaction():
            product = await con.fetchrow(
                """
                SELECT *
                FROM products
                WHERE code=$1
                FOR UPDATE
                """,
                product_code
            )

            if not product:
                return False, "❌ Товар не найден.", None

            if not product["is_active"]:
                return False, "❌ Товар отключён.", None

            if product["sold_at"] is not None:
                return False, "❌ Товар уже продан.", None

            reserved_by = product["reserved_by"]
            reserved_until = product["reserved_until"]

            if reserved_until is not None and reserved_until < now_utc():
                await con.execute(
                    """
                    UPDATE products
                    SET reserved_by=NULL, reserved_until=NULL
                    WHERE code=$1
                    """,
                    product_code
                )
                reserved_by = None
                reserved_until = None

            if reserved_by is not None and reserved_until is not None and reserved_until > now_utc():
                if int(reserved_by) != user_id:
                    return False, "❌ Этот товар сейчас забронирован другим покупателем. Попробуй позже.", None

            await con.execute(
                """
                UPDATE products
                SET reserved_by=$2, reserved_until=$3
                WHERE code=$1
                """,
                product_code, user_id, expires_at
            )

            updated = await con.fetchrow("SELECT * FROM products WHERE code=$1", product_code)
            return True, f"✅ Товар забронирован за тобой до {format_dt(expires_at)}", updated


async def release_product_reservation(product_code: str, only_user_id: int | None = None) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        if only_user_id is None:
            await con.execute(
                """
                UPDATE products
                SET reserved_by=NULL, reserved_until=NULL
                WHERE code=$1 AND sold_at IS NULL
                """,
                product_code
            )
        else:
            await con.execute(
                """
                UPDATE products
                SET reserved_by=NULL, reserved_until=NULL
                WHERE code=$1
                  AND sold_at IS NULL
                  AND reserved_by=$2
                """,
                product_code, only_user_id
            )


async def product_is_available_for_user(user_id: int, product_code: str) -> tuple[bool, str, asyncpg.Record | None]:
    """
    Проверяем, что товар:
    - активен
    - не продан
    - либо свободен
    - либо забронирован этим же user_id
    """
    product = await get_product(product_code)
    if not product:
        return False, "❌ Товар не найден.", None

    if not product["is_active"]:
        return False, "❌ Товар недоступен.", None

    if product["sold_at"] is not None:
        return False, "❌ Товар уже продан.", None

    reserved_by = product["reserved_by"]
    reserved_until = product["reserved_until"]

    if reserved_until is not None and reserved_until < now_utc():
        await release_product_reservation(product_code)
        product = await get_product(product_code)
        return True, "ok", product

    if reserved_by is not None and reserved_until is not None and reserved_until > now_utc():
        if int(reserved_by) != user_id:
            return False, "❌ Товар сейчас забронирован другим покупателем.", None

    return True, "ok", product


# =========================
# BALANCE BUY
# =========================
async def buy_with_balance(user_id: int, product_code: str) -> tuple[bool, str]:
    await ensure_user(user_id)
    assert pool is not None

    async with pool.acquire() as con:
        async with con.transaction():
            product = await con.fetchrow(
                """
                SELECT *
                FROM products
                WHERE code=$1
                FOR UPDATE
                """,
                product_code
            )

            if not product:
                return False, "❌ Товар не найден."

            if not product["is_active"]:
                return False, "❌ Товар недоступен."

            if product["sold_at"] is not None:
                return False, "❌ Товар уже продан."

            reserved_by = product["reserved_by"]
            reserved_until = product["reserved_until"]

            if reserved_until is not None and reserved_until < now_utc():
                await con.execute(
                    "UPDATE products SET reserved_by=NULL, reserved_until=NULL WHERE code=$1",
                    product_code
                )
                reserved_by = None
                reserved_until = None

            if reserved_by is not None and reserved_until is not None and reserved_until > now_utc():
                if int(reserved_by) != user_id:
                    return False, "❌ Товар забронирован другим покупателем."

            name = str(product["name"])
            link = str(product["link"] or "").strip()
            price = decimal.Decimal(product["price"])

            if not link:
                return False, "❌ Для товара не добавлена ссылка."

            user_row = await con.fetchrow(
                "SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE",
                user_id
            )
            bal = decimal.Decimal(user_row["balance"])

            if bal < price:
                return False, f"❌ Недостаточно средств.\nНужно: {price:.2f} {UAH}\nУ тебя: {bal:.2f} {UAH}"

            await con.execute(
                "UPDATE users SET balance = balance - $2, orders_count = orders_count + 1 WHERE user_id=$1",
                user_id, price
            )

            await con.execute(
                """
                INSERT INTO purchases(user_id, product_code, item_name, price, link, provider, external_payment_id)
                VALUES($1,$2,$3,$4,$5,$6,$7)
                """,
                user_id, product_code, name, price, link, "balance", ""
            )

            await con.execute(
                """
                UPDATE products
                SET sold_to=$2,
                    sold_at=NOW(),
                    reserved_by=NULL,
                    reserved_until=NULL,
                    is_active=FALSE
                WHERE code=$1
                """,
                product_code, user_id
            )

            await con.execute(
                """
                UPDATE invoices
                SET status='cancelled'
                WHERE product_code=$1
                  AND status='wait'
                """,
                product_code
            )

    return True, f"✅ Покупка успешна: {name}\nСписано: {price:.2f} {UAH}\n\n🔗 Твоя ссылка:\n{link}"


# =========================
# PAYSYNC
# =========================
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
                raise RuntimeError(f"PaySync bad response: {txt[:300]}")
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


# =========================
# CRYPTO PAY
# =========================
async def crypto_pay_request(method: str, payload: dict | None = None) -> dict:
    if not CRYPTO_PAY_API_TOKEN:
        raise RuntimeError("CRYPTO_PAY_API_TOKEN is missing")

    url = f"{CRYPTO_PAY_BASE_URL}/{method}"
    headers = {
        "Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=(payload or {}), timeout=30) as resp:
            try:
                js = await resp.json()
            except Exception:
                txt = await resp.text()
                raise RuntimeError(f"Crypto Pay bad response: {txt[:300]}")

    if not js.get("ok"):
        raise RuntimeError(f"Crypto Pay error: {js.get('error', 'unknown error')}")
    return js


async def crypto_create_invoice(logical_amount_int: int, payload_text: str, description: str) -> dict:
    """
    Создаём invoice в фиате UAH, чтобы пользователю было удобно платить.
    """
    body = {
        "currency_type": "fiat",
        "fiat": CRYPTO_PAY_FIAT,
        "accepted_assets": CRYPTO_PAY_ACCEPTED_ASSETS,
        "amount": f"{logical_amount_int:.2f}",
        "description": description,
        "payload": payload_text,
        "expires_in": PAYMENT_TIMEOUT_MINUTES * 60,
        "allow_comments": False,
        "allow_anonymous": True,
    }
    js = await crypto_pay_request("createInvoice", body)
    return js["result"]


async def crypto_get_invoice(external_id: str) -> dict | None:
    body = {"invoice_ids": external_id}
    js = await crypto_pay_request("getInvoices", body)
    arr = js.get("result") or {}
    items = arr.get("items") if isinstance(arr, dict) else arr
    if not items:
        return None
    return items[0]


# =========================
# INVOICES
# =========================
async def save_invoice(
    invoice_id: str,
    external_id: str,
    user_id: int,
    provider: str,
    kind: str,
    logical_amount: int,
    amount_to_pay: str,
    currency: str,
    pay_url: str,
    card_number: str,
    product_code: str | None,
    payload_text: str,
    expires_at: datetime | None,
    status: str = "wait",
) -> asyncpg.Record:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO invoices(
                invoice_id, external_id, user_id, provider, kind, product_code,
                logical_amount, amount_to_pay, currency, pay_url, card_number,
                status, expires_at, payload
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (invoice_id) DO UPDATE SET
                external_id=EXCLUDED.external_id,
                user_id=EXCLUDED.user_id,
                provider=EXCLUDED.provider,
                kind=EXCLUDED.kind,
                product_code=EXCLUDED.product_code,
                logical_amount=EXCLUDED.logical_amount,
                amount_to_pay=EXCLUDED.amount_to_pay,
                currency=EXCLUDED.currency,
                pay_url=EXCLUDED.pay_url,
                card_number=EXCLUDED.card_number,
                status=EXCLUDED.status,
                expires_at=EXCLUDED.expires_at,
                payload=EXCLUDED.payload
            """,
            invoice_id, external_id, user_id, provider, kind, product_code,
            logical_amount, amount_to_pay, currency, pay_url, card_number,
            status, expires_at, payload_text
        )
        return await con.fetchrow("SELECT * FROM invoices WHERE invoice_id=$1", invoice_id)


async def get_invoice(invoice_id: str) -> asyncpg.Record | None:
    assert pool is not None
    async with pool.acquire() as con:
        return await con.fetchrow("SELECT * FROM invoices WHERE invoice_id=$1", invoice_id)


async def cancel_old_wait_invoices_for_product(product_code: str, keep_invoice_id: str | None = None) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        if keep_invoice_id:
            await con.execute(
                """
                UPDATE invoices
                SET status='cancelled'
                WHERE product_code=$1
                  AND status='wait'
                  AND invoice_id <> $2
                """,
                product_code, keep_invoice_id
            )
        else:
            await con.execute(
                """
                UPDATE invoices
                SET status='cancelled'
                WHERE product_code=$1
                  AND status='wait'
                """,
                product_code
            )


async def create_product_invoice_paysync(user_id: int, product_code: str) -> asyncpg.Record:
    await ensure_user(user_id)

    ok, msg, product = await reserve_product(user_id, product_code)
    if not ok or not product:
        raise RuntimeError(msg)

    price = decimal.Decimal(product["price"])
    logical_amount = price_to_int_uah(price)
    if logical_amount is None:
        raise RuntimeError("Для оплаты картой цена товара должна быть целым числом, например 350.00")

    payload_text = f"product|{user_id}|{product_code}|{make_nonce()}"
    js = await paysync_h2h_create(logical_amount, PAYSYNC_CURRENCY, payload_text)

    external_id = str(js.get("trade") or "").strip()
    if not external_id:
        raise RuntimeError(f"PaySync create missing trade: {js}")

    amount_to_pay_int = safe_int_from_paysync_amount(js.get("amount"))
    if amount_to_pay_int is None:
        amount_to_pay_int = logical_amount

    card_number = str(js.get("card_number") or "").strip()
    currency = str(js.get("currency") or PAYSYNC_CURRENCY).strip()
    status = str(js.get("status") or "wait").strip().lower()
    expires_at = now_utc() + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    invoice_id = f"ps_{external_id}"

    inv = await save_invoice(
        invoice_id=invoice_id,
        external_id=external_id,
        user_id=user_id,
        provider="paysync",
        kind="product",
        logical_amount=logical_amount,
        amount_to_pay=str(amount_to_pay_int),
        currency=currency,
        pay_url="",
        card_number=card_number,
        product_code=product_code,
        payload_text=payload_text,
        expires_at=expires_at,
        status=status,
    )

    await cancel_old_wait_invoices_for_product(product_code, keep_invoice_id=invoice_id)
    return inv


async def create_product_invoice_crypto(user_id: int, product_code: str) -> asyncpg.Record:
    await ensure_user(user_id)

    ok, msg, product = await reserve_product(user_id, product_code)
    if not ok or not product:
        raise RuntimeError(msg)

    price = decimal.Decimal(product["price"])
    logical_amount = price_to_int_uah(price)
    if logical_amount is None:
        raise RuntimeError("Для Crypto цена товара должна быть целым числом, например 350.00")

    payload_text = f"product|{user_id}|{product_code}|{make_nonce()}"
    description = f"{product['name']} | {logical_amount} UAH"
    res = await crypto_create_invoice(logical_amount, payload_text, description)

    external_id = str(res.get("invoice_id") or "").strip()
    pay_url = str(res.get("bot_invoice_url") or "").strip()
    status = str(res.get("status") or "active").strip().lower()
    amount_to_pay = str(res.get("amount") or logical_amount)
    currency = str(res.get("fiat") or res.get("asset") or CRYPTO_PAY_FIAT).strip()
    expiration_date = res.get("expiration_date")

    expires_at: datetime | None = None
    if expiration_date:
        try:
            expires_at = datetime.fromisoformat(str(expiration_date).replace("Z", "+00:00"))
        except Exception:
            expires_at = now_utc() + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    else:
        expires_at = now_utc() + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)

    if not external_id:
        raise RuntimeError("Crypto Pay не вернул invoice_id")
    if not pay_url:
        raise RuntimeError("Crypto Pay не вернул bot_invoice_url")

    invoice_id = f"cp_{external_id}"
    inv = await save_invoice(
        invoice_id=invoice_id,
        external_id=external_id,
        user_id=user_id,
        provider="crypto",
        kind="product",
        logical_amount=logical_amount,
        amount_to_pay=amount_to_pay,
        currency=currency,
        pay_url=pay_url,
        card_number="",
        product_code=product_code,
        payload_text=payload_text,
        expires_at=expires_at,
        status="wait" if status == "active" else status,
    )

    await cancel_old_wait_invoices_for_product(product_code, keep_invoice_id=invoice_id)
    return inv


async def create_topup_invoice_paysync(user_id: int, logical_amount: int) -> asyncpg.Record:
    await ensure_user(user_id)

    payload_text = f"topup|{user_id}|-|{make_nonce()}"
    js = await paysync_h2h_create(logical_amount, PAYSYNC_CURRENCY, payload_text)

    external_id = str(js.get("trade") or "").strip()
    if not external_id:
        raise RuntimeError(f"PaySync create missing trade: {js}")

    amount_to_pay_int = safe_int_from_paysync_amount(js.get("amount"))
    if amount_to_pay_int is None:
        amount_to_pay_int = logical_amount

    card_number = str(js.get("card_number") or "").strip()
    currency = str(js.get("currency") or PAYSYNC_CURRENCY).strip()
    status = str(js.get("status") or "wait").strip().lower()
    expires_at = now_utc() + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    invoice_id = f"ps_{external_id}"

    inv = await save_invoice(
        invoice_id=invoice_id,
        external_id=external_id,
        user_id=user_id,
        provider="paysync",
        kind="topup",
        logical_amount=logical_amount,
        amount_to_pay=str(amount_to_pay_int),
        currency=currency,
        pay_url="",
        card_number=card_number,
        product_code=None,
        payload_text=payload_text,
        expires_at=expires_at,
        status=status,
    )
    return inv


# =========================
# PAYMENT APPLY
# =========================
async def mark_invoice_expired(invoice_id: str) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "UPDATE invoices SET status='expired' WHERE invoice_id=$1 AND status='wait'",
            invoice_id
        )


async def apply_paid_invoice(invoice_id: str) -> tuple[bool, str]:
    """
    Проверяем провайдера, подтверждаем оплату, затем:
    - topup -> пополняем баланс
    - product -> выдаём товар, записываем покупку, товар выключаем
    """
    assert pool is not None

    inv = await get_invoice(invoice_id)
    if not inv:
        return False, "❌ Платёж не найден."

    if inv["status"] in ("done", "paid"):
        if inv["kind"] == "topup":
            return True, "✅ Уже подтверждено ранее. Баланс пополнен."
        return True, "✅ Уже подтверждено ранее. Товар уже выдан."

    if inv["status"] in ("expired", "cancelled"):
        if inv["product_code"]:
            await release_product_reservation(str(inv["product_code"]), only_user_id=int(inv["user_id"]))
        return False, "❌ Счёт уже истёк или отменён."

    expires_at = inv["expires_at"]
    if expires_at is not None and expires_at < now_utc():
        await mark_invoice_expired(invoice_id)
        if inv["product_code"]:
            await release_product_reservation(str(inv["product_code"]), only_user_id=int(inv["user_id"]))
        return False, "❌ Время оплаты истекло. Бронь снята."

    provider = str(inv["provider"])
    external_id = str(inv["external_id"])
    user_id = int(inv["user_id"])
    kind = str(inv["kind"])
    product_code = inv["product_code"]
    logical_amount = int(inv["logical_amount"])

    is_paid = False

    if provider == "paysync":
        js = await paysync_gettrans(external_id)
        status = str(js.get("status") or "").lower()
        if status == "paid":
            is_paid = True

    elif provider == "crypto":
        invoice = await crypto_get_invoice(external_id)
        if not invoice:
            return False, "❌ Не удалось получить данные счёта Crypto."
        status = str(invoice.get("status") or "").lower()
        if status == "paid":
            is_paid = True
        elif status == "expired":
            await mark_invoice_expired(invoice_id)
            if product_code:
                await release_product_reservation(str(product_code), only_user_id=user_id)
            return False, "❌ Crypto-счёт истёк. Бронь снята."

    else:
        return False, "❌ Неизвестный провайдер оплаты."

    if not is_paid:
        return False, "❌ Оплата ещё не подтверждена."

    if kind == "topup":
        amount_dec = decimal.Decimal(logical_amount).quantize(decimal.Decimal("0.01"))
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT status FROM invoices WHERE invoice_id=$1 FOR UPDATE",
                    invoice_id
                )
                if not row:
                    return False, "❌ Счёт не найден."
                if row["status"] in ("done", "paid"):
                    return True, "✅ Уже подтверждено ранее. Баланс пополнен."

                await con.execute(
                    "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                    user_id, amount_dec
                )
                await con.execute(
                    "UPDATE invoices SET status='paid', paid_at=NOW() WHERE invoice_id=$1",
                    invoice_id
                )

        return True, f"✅ Оплата подтверждена.\n🏦 Баланс пополнен на {logical_amount} {UAH}"

    if kind == "product":
        if not product_code:
            return False, "❌ У счёта нет привязки к товару."

        async with pool.acquire() as con:
            async with con.transaction():
                current_inv = await con.fetchrow(
                    "SELECT * FROM invoices WHERE invoice_id=$1 FOR UPDATE",
                    invoice_id
                )
                if not current_inv:
                    return False, "❌ Счёт не найден."
                if current_inv["status"] == "done":
                    return True, "✅ Уже подтверждено ранее. Товар уже выдан."
                if current_inv["status"] in ("expired", "cancelled"):
                    return False, "❌ Счёт уже истёк или отменён."

                product = await con.fetchrow(
                    "SELECT * FROM products WHERE code=$1 FOR UPDATE",
                    str(product_code)
                )
                if not product:
                    await con.execute(
                        "UPDATE invoices SET status='paid', paid_at=NOW() WHERE invoice_id=$1",
                        invoice_id
                    )
                    return True, "✅ Оплата подтверждена, но товар не найден. Напиши оператору."

                if product["sold_at"] is not None:
                    if product["sold_to"] == user_id:
                        await con.execute(
                            "UPDATE invoices SET status='done', paid_at=NOW() WHERE invoice_id=$1",
                            invoice_id
                        )
                        return True, "✅ Уже подтверждено ранее. Товар уже выдан."

                    await con.execute(
                        "UPDATE invoices SET status='paid', paid_at=NOW() WHERE invoice_id=$1",
                        invoice_id
                    )
                    return True, "✅ Оплата подтверждена, но товар уже числится проданным. Напиши оператору."

                reserved_by = product["reserved_by"]
                reserved_until = product["reserved_until"]

                if reserved_until is not None and reserved_until < now_utc():
                    await con.execute(
                        "UPDATE products SET reserved_by=NULL, reserved_until=NULL WHERE code=$1",
                        str(product_code)
                    )
                    reserved_by = None
                    reserved_until = None

                if reserved_by is not None and reserved_until is not None and reserved_until > now_utc():
                    if int(reserved_by) != user_id:
                        await con.execute(
                            "UPDATE invoices SET status='paid', paid_at=NOW() WHERE invoice_id=$1",
                            invoice_id
                        )
                        return True, "✅ Оплата подтверждена, но бронь товара принадлежит другому пользователю. Напиши оператору."

                name = str(product["name"])
                link = str(product["link"] or "").strip()
                price = decimal.Decimal(product["price"])

                if not link:
                    await con.execute(
                        "UPDATE invoices SET status='paid', paid_at=NOW() WHERE invoice_id=$1",
                        invoice_id
                    )
                    return True, "✅ Оплата подтверждена, но ссылка на товар не добавлена. Напиши оператору."

                await con.execute(
                    "UPDATE users SET orders_count = orders_count + 1 WHERE user_id=$1",
                    user_id
                )

                await con.execute(
                    """
                    INSERT INTO purchases(user_id, product_code, item_name, price, link, provider, external_payment_id)
                    VALUES($1,$2,$3,$4,$5,$6,$7)
                    """,
                    user_id, str(product_code), name, price, link, provider, external_id
                )

                await con.execute(
                    """
                    UPDATE products
                    SET sold_to=$2,
                        sold_at=NOW(),
                        reserved_by=NULL,
                        reserved_until=NULL,
                        is_active=FALSE
                    WHERE code=$1
                    """,
                    str(product_code), user_id
                )

                await con.execute(
                    """
                    UPDATE invoices
                    SET status='done', paid_at=NOW()
                    WHERE invoice_id=$1
                    """,
                    invoice_id
                )

                await con.execute(
                    """
                    UPDATE invoices
                    SET status='cancelled'
                    WHERE product_code=$1
                      AND invoice_id <> $2
                      AND status='wait'
                    """,
                    str(product_code), invoice_id
                )

        return True, f"✅ Оплата подтверждена.\n✅ Покупка успешна: {name}\n\n🔗 Твоя ссылка:\n{link}"

    return False, "❌ Неизвестный тип счёта."


# =========================
# RENDER PAYMENT MSG
# =========================
def render_paysync_message(inv: asyncpg.Record) -> str:
    trade = str(inv["external_id"])
    amount_to_pay = str(inv["amount_to_pay"])
    currency = str(inv["currency"])
    card = str(inv["card_number"] or "").strip() or "—"
    exp = format_dt(inv["expires_at"])

    return (
        f"💳 Оплата через PaySync\n\n"
        f"🧾 Номер заявки: {trade}\n"
        f"💳 Реквизиты для оплаты: {card}\n"
        f"💰 Сумма к оплате: {amount_to_pay} {currency}\n"
        f"⏳ Оплатить до: {exp}\n\n"
        f"❗️Оплачивай одним платежом и точно в указанной сумме.\n"
        f"После оплаты нажми «✅ Проверить оплату»."
    )


def render_crypto_message(inv: asyncpg.Record) -> str:
    pay_url = str(inv["pay_url"] or "").strip()
    amount_to_pay = str(inv["amount_to_pay"])
    currency = str(inv["currency"])
    exp = format_dt(inv["expires_at"])

    text = (
        f"🪙 Оплата через Crypto\n\n"
        f"🧾 Счёт: {inv['external_id']}\n"
        f"💰 Сумма: {amount_to_pay} {currency}\n"
        f"⏳ Оплатить до: {exp}\n\n"
        f"Перейди по ссылке для оплаты:\n{pay_url}\n\n"
        f"После оплаты нажми «✅ Проверить оплату»."
    )
    return text


# =========================
# FSM
# =========================
class PromoStates(StatesGroup):
    waiting_code = State()


class TopupStates(StatesGroup):
    waiting_amount = State()


# =========================
# HANDLERS
# =========================
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
    if not product:
        await call.message.answer("❌ Товар не найден.")
        return

    if not product["is_active"] or product["sold_at"] is not None:
        await call.message.answer("❌ Товар недоступен.")
        return

    reserved_by = product["reserved_by"]
    reserved_until = product["reserved_until"]
    if reserved_until is not None and reserved_until > now_utc() and reserved_by is not None:
        if int(reserved_by) != call.from_user.id:
            await call.message.answer("❌ Этот товар сейчас временно забронирован другим покупателем.")
            return

    name = str(product["name"])
    price = decimal.Decimal(product["price"])
    desc = str(product["description"] or "").strip() or " "

    text = ITEM_TEXT_TEMPLATE.format(
        name=name,
        price=f"{price:.2f}",
        uah=UAH,
        desc=desc
    )
    await call.message.answer(text, reply_markup=inline_one_button("Район", f"district:{code}"))


@dp.callback_query(F.data.startswith("district:"))
async def cb_district(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":", 1)[1]

    ok, msg, _ = await product_is_available_for_user(call.from_user.id, code)
    if not ok:
        await call.message.answer(msg)
        return

    await call.message.answer(DISTRICT_TEXT, reply_markup=inline_pay_buttons(code))


@dp.callback_query(F.data.startswith("pay:bal:"))
async def cb_pay_balance(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]

    try:
        ok, msg, _ = await reserve_product(call.from_user.id, code)
        if not ok:
            await call.message.answer(msg)
            return
        ok2, msg2 = await buy_with_balance(call.from_user.id, code)
        await call.message.answer(msg2)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка оплаты балансом: {e}")


@dp.callback_query(F.data.startswith("pay:card:"))
async def cb_pay_card(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]

    try:
        inv = await create_product_invoice_paysync(call.from_user.id, code)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    await call.message.answer(
        render_paysync_message(inv),
        reply_markup=inline_check_invoice(str(inv["invoice_id"]))
    )


@dp.callback_query(F.data.startswith("pay:crypto:"))
async def cb_pay_crypto(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]

    try:
        inv = await create_product_invoice_crypto(call.from_user.id, code)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка создания Crypto-оплаты: {e}")
        return

    await call.message.answer(
        render_crypto_message(inv),
        reply_markup=inline_check_invoice(str(inv["invoice_id"]))
    )


@dp.callback_query(F.data.startswith("check:"))
async def cb_check(call: CallbackQuery):
    await call.answer()
    invoice_id = call.data.split(":", 1)[1]

    try:
        ok, msg = await apply_paid_invoice(invoice_id)
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
    logical_amount = parse_int_amount(message.text)
    if logical_amount is None:
        await message.answer("❌ Введи сумму целым числом. Пример: 200")
        return

    if logical_amount < 10:
        await message.answer(f"❌ Минимум 10 {UAH}.")
        return

    try:
        inv = await create_topup_invoice_paysync(message.from_user.id, logical_amount)
    except Exception as e:
        await message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    await message.answer(
        render_paysync_message(inv),
        reply_markup=inline_check_invoice(str(inv["invoice_id"]))
    )
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
        provider = str(r["provider"])
        text += f"• {r['item_name']} — {price:.2f} {UAH} [{provider}] ({dt})\n{r['link']}\n\n"
    await call.message.answer(text)


# =========================
# ADMIN
# =========================
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
            """
            SELECT city, code, name, price, is_active, reserved_by, reserved_until, sold_at
            FROM products
            ORDER BY created_at DESC
            LIMIT 50
            """
        )

    if not rows:
        await message.answer("Товаров нет.")
        return

    text = "Товары:\n\n"
    for r in rows:
        reserved = "FREE"
        if r["sold_at"] is not None:
            reserved = "SOLD"
        elif r["reserved_until"] is not None and r["reserved_until"] > now_utc():
            reserved = f"RESERVED until {format_dt(r['reserved_until'])}"

        text += (
            f"{r['city']} | {r['code']} | {r['name']} | "
            f"{decimal.Decimal(r['price']):.2f} {UAH} | "
            f"{'ON' if r['is_active'] else 'OFF'} | {reserved}\n"
        )

    await message.answer(text)


@dp.message(F.text.startswith("/invoice"))
async def cmd_invoice(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /invoice INVOICE_ID")
        return

    invoice_id = parts[1].strip()
    inv = await get_invoice(invoice_id)
    if not inv:
        await message.answer("❌ Не найдено.")
        return

    text = (
        f"invoice_id: {inv['invoice_id']}\n"
        f"external_id: {inv['external_id']}\n"
        f"user_id: {inv['user_id']}\n"
        f"provider: {inv['provider']}\n"
        f"kind: {inv['kind']}\n"
        f"product_code: {inv['product_code']}\n"
        f"logical_amount: {inv['logical_amount']}\n"
        f"amount_to_pay: {inv['amount_to_pay']}\n"
        f"currency: {inv['currency']}\n"
        f"status: {inv['status']}\n"
        f"expires_at: {format_dt(inv['expires_at'])}\n"
        f"paid_at: {format_dt(inv['paid_at'])}\n"
        f"pay_url: {inv['pay_url']}\n"
        f"card_number: {inv['card_number']}\n"
    )
    await message.answer(text)


# =========================
# MAIN
# =========================
async def main():
    await db_init()

    bot = Bot(token=BOT_TOKEN)
    cleanup_task = asyncio.create_task(background_cleanup_loop())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(Exception):
            await cleanup_task
        if pool is not None:
            await pool.close()


if __name__ == "__main__":
    import contextlib
    asyncio.run(main())
