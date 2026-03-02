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
if not PAYSYNC_CLIENT_ID:
    raise RuntimeError("PAYSYNC_CLIENT_ID is missing")

ADMIN_ID = int(ADMIN_ID_RAW)
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
https://t.me/+HvuVKZkR2-03MzBi

🥇Отзывы :
https://t.me/+HvuVKZkR2-03MzBi

Оператор/Сапорт : 
@gskalye

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

TOPUP_ASK_TEXT = f"💳 Введите сумму пополнения в гривнах ({UAH}):"


# ================== KEYBOARDS ==================
def bottom_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        is_persistent=True,
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
            [InlineKeyboardButton(text="Картой", callback_data=f"pay:card:{product_code}")],
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


def inline_pay_and_check(payment_url: str, trade_id: str, label: str = "💳 Оплатить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, url=payment_url)],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{trade_id}")],
        ]
    )


# ================== DB ==================
pool: asyncpg.Pool | None = None


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def normalize_code(raw: str) -> str:
    return (raw or "").strip()


def parse_amount_uah(text: str) -> decimal.Decimal | None:
    try:
        amt = decimal.Decimal((text or "").replace(",", ".").strip())
        if amt <= 0:
            return None
        # ограничим до 2 знаков
        return amt.quantize(decimal.Decimal("0.01"))
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
            product_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            link TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

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

        # ✅ PAYMENTS (без callback, через “проверить оплату”)
        await con.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            trade_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,                       -- 'topup' | 'product'
            amount NUMERIC(12,2) NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            product_code TEXT,
            status TEXT NOT NULL DEFAULT 'wait',       -- 'wait' | 'paid' | 'done'
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)


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
        rows = await con.fetch(
            """
            SELECT code, name, price
            FROM products
            WHERE city=$1 AND is_active=TRUE
            ORDER BY created_at DESC
            LIMIT $2
            """,
            city, limit
        )
    return rows


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
        row = await con.fetchrow(
            "SELECT code, city, name, price, link, description, is_active FROM products WHERE code=$1",
            code
        )
    return row


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
        rows = await con.fetch(
            """
            SELECT item_name, link, price, created_at
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 20
            """,
            user_id,
        )
    return rows


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


# ✅ FIX: Балансом всегда выдаёт ссылку (если link задан)
async def buy_with_balance(user_id: int, product_code: str) -> tuple[bool, str]:
    await ensure_user(user_id)
    product = await get_product(product_code)
    if not product or not product["is_active"]:
        return False, "❌ Товар недоступен."

    price = decimal.Decimal(product["price"])
    name = str(product["name"])
    link = str(product["link"] or "")
    if not link.strip():
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


# ================== PaySync (без callback) ==================
async def paysync_create_invoice(amount_uah: decimal.Decimal, data: str) -> tuple[str, str]:
    """
    Возвращает (trade_id, payment_url)
    Используем redirect create_invoice, а дальше проверяем gettrans по trade_id.
    """
    # data лучше кодировать
    data_q = quote(data)

    url = f"https://paysync.bot/create_invoice/{PAYSYNC_CLIENT_ID}/{amount_uah:.2f}/{PAYSYNC_CURRENCY}?data={data_q}"

    headers = {"apikey": PAYSYNC_APIKEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=30) as resp:
            # иногда API возвращает text, иногда json
            try:
                js = await resp.json()
            except Exception:
                txt = await resp.text()
                raise RuntimeError(f"PaySync create_invoice bad response: {txt[:300]}")

    # Попробуем угадать поля максимально устойчиво
    trade = js.get("trade") or js.get("trade_id") or js.get("id") or js.get("invoice_id")
    pay_url = js.get("url") or js.get("payment_url") or js.get("pay_url")

    if not trade or not pay_url:
        raise RuntimeError(f"PaySync create_invoice missing fields: {js}")

    return str(trade), str(pay_url)


async def paysync_gettrans(trade_id: str) -> dict:
    url = f"https://paysync.bot/gettrans/{trade_id}"
    headers = {"apikey": PAYSYNC_APIKEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=30) as resp:
            try:
                return await resp.json()
            except Exception:
                txt = await resp.text()
                raise RuntimeError(f"PaySync gettrans bad response: {txt[:300]}")


async def invoice_create(user_id: int, kind: str, amount: decimal.Decimal, product_code: str | None) -> tuple[str, str]:
    """
    Создаёт инвойс в PaySync и сохраняет в БД.
    """
    await ensure_user(user_id)
    nonce = uuid.uuid4().hex[:12]
    data = f"{kind}:{user_id}:{product_code or '-'}:{nonce}"

    trade_id, payment_url = await paysync_create_invoice(amount, data)

    assert pool is not None
    async with pool.acquire() as con:
        # если вдруг trade_id уже был — просто перезапишем
        await con.execute(
            """
            INSERT INTO invoices(trade_id, user_id, kind, amount, currency, product_code, status)
            VALUES($1,$2,$3,$4,$5,$6,'wait')
            ON CONFLICT (trade_id) DO UPDATE SET
              user_id=EXCLUDED.user_id,
              kind=EXCLUDED.kind,
              amount=EXCLUDED.amount,
              currency=EXCLUDED.currency,
              product_code=EXCLUDED.product_code,
              status='wait'
            """,
            trade_id, user_id, kind, amount, PAYSYNC_CURRENCY, product_code
        )

    return trade_id, payment_url


async def invoice_apply_paid(trade_id: str) -> tuple[bool, str]:
    """
    Проверяет PaySync, если paid — применяет:
    - topup: начислить баланс
    - product: выдать товар, списать/записать как покупку (оплата картой — баланс не трогаем)
    Делает идемпотентно (повторно не начислит/не выдаст).
    Возвращает (ok, message_to_user)
    """
    assert pool is not None

    # 1) Проверяем статус у PaySync
    js = await paysync_gettrans(trade_id)
    status = (js.get("status") or "").lower()

    if status != "paid":
        return False, "❌ Оплата ещё не подтверждена."

    # 2) Берём инвойс из БД
    async with pool.acquire() as con:
        inv = await con.fetchrow("SELECT * FROM invoices WHERE trade_id=$1", trade_id)

    if not inv:
        return False, "❌ Инвойс не найден."

    if inv["status"] in ("paid", "done"):
        # уже применено
        if inv["kind"] == "topup":
            return True, "✅ Оплата уже была подтверждена. Баланс пополнен."
        else:
            return True, "✅ Оплата уже была подтверждена. Товар уже выдан."

    user_id = int(inv["user_id"])
    kind = str(inv["kind"])
    amount = decimal.Decimal(inv["amount"])
    product_code = (inv["product_code"] if inv["product_code"] else None)

    if kind == "topup":
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    "UPDATE users SET balance = balance + $2 WHERE user_id=$1",
                    user_id, amount
                )
                await con.execute(
                    "UPDATE invoices SET status='paid' WHERE trade_id=$1",
                    trade_id
                )
        return True, f"✅ Оплата подтверждена.\n🏦 Баланс пополнен на {amount:.2f} {UAH}"

    if kind == "product":
        if not product_code:
            return False, "❌ Ошибка инвойса: нет товара."

        product = await get_product(str(product_code))
        if not product or not product["is_active"]:
            # пометим, что paid, но товар недоступен (чтобы не выдавать)
            async with pool.acquire() as con:
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)
            return True, "✅ Оплата подтверждена, но товар сейчас недоступен. Напиши оператору."

        name = str(product["name"])
        link = str(product["link"] or "")
        price = decimal.Decimal(product["price"])
        if not link.strip():
            async with pool.acquire() as con:
                await con.execute("UPDATE invoices SET status='paid' WHERE trade_id=$1", trade_id)
            return True, "✅ Оплата подтверждена, но ссылка на товар ещё не добавлена. Напиши оператору."

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
                await con.execute(
                    "UPDATE invoices SET status='done' WHERE trade_id=$1",
                    trade_id
                )

        return True, f"✅ Оплата подтверждена.\n✅ Покупка успешна: {name}\n\n🔗 Твоя ссылка:\n{link}"

    return False, "❌ Неизвестный тип инвойса."


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
    # prod:{city}:{code}
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


# ✅ Балансом — фикс: выдаёт ссылку
@dp.callback_query(F.data.startswith("pay:bal:"))
async def cb_pay_balance(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]
    ok, msg = await buy_with_balance(call.from_user.id, code)
    await call.message.answer(msg)


# ✅ Картой — создаём инвойс PaySync на сумму товара + “Проверить оплату”
@dp.callback_query(F.data.startswith("pay:card:"))
async def cb_pay_card(call: CallbackQuery):
    await call.answer()
    code = call.data.split(":")[-1]

    product = await get_product(code)
    if not product or not product["is_active"]:
        await call.message.answer("❌ Товар недоступен.")
        return

    price = decimal.Decimal(product["price"])
    name = str(product["name"])

    try:
        trade_id, payment_url = await invoice_create(call.from_user.id, "product", price, code)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    kb = inline_pay_and_check(payment_url, trade_id, label="💳 Оплатить картой")
    await call.message.answer(
        f"💳 Оплата товара: {name}\nСумма: {price:.2f} {UAH}\n\nПосле оплаты нажми «✅ Проверить оплату».",
        reply_markup=kb
    )


# ✅ Проверить оплату (и топап, и покупку товара)
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


# ================== PROFILE: TOPUP ==================
@dp.callback_query(F.data == "profile:topup")
async def cb_profile_topup(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TopupStates.waiting_amount)
    await call.message.answer(TOPUP_ASK_TEXT)


@dp.message(TopupStates.waiting_amount)
async def topup_amount_entered(message: Message, state: FSMContext):
    amt = parse_amount_uah(message.text)
    if amt is None:
        await message.answer("❌ Введи сумму числом. Пример: 200")
        return

    # минималка (чтобы не спамили 1 грн)
    if amt < decimal.Decimal("10.00"):
        await message.answer(f"❌ Минимум 10 {UAH}.")
        return

    try:
        trade_id, payment_url = await invoice_create(message.from_user.id, "topup", amt, None)
    except Exception as e:
        await message.answer(f"❌ Ошибка создания оплаты: {e}")
        return

    kb = inline_pay_and_check(payment_url, trade_id, label="💳 Оплатить пополнение")
    await message.answer(
        f"💳 Пополнение баланса на {amt:.2f} {UAH}\n\nПосле оплаты нажми «✅ Проверить оплату».",
        reply_markup=kb
    )
    await state.clear()


# ================== PROFILE: PROMO / HISTORY ==================
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
        # Пример:
        # /addproduct odesa | saint | Saint | 300 | https://t.me/... | описание
        parts = [p.strip() for p in raw[len("/addproduct"):].strip().split("|")]
        if len(parts) < 5:
            await message.answer(
                "Формат:\n/addproduct city | code | name | price | link | desc(опц.)"
            )
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
