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
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


# =======================
# ENV
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_IDS = os.getenv("ADMIN_IDS", "").strip()  # пример: "123456789,987654321"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing (set BOT_TOKEN in Variables)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (add Postgres and set DATABASE_URL)")

ADMIN_SET = set()
if ADMIN_IDS:
    for x in ADMIN_IDS.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_SET.add(int(x))


def is_admin(user_id: int) -> bool:
    # Если ADMIN_IDS не задан — считаем владельца админом (удобно на старте)
    return (not ADMIN_SET) or (user_id in ADMIN_SET)


# =======================
# UI TEXTS
# =======================
BTN_MAIN = "ГЛАВНАЯ ⚪"
BTN_PROFILE = "ПРОФИЛЬ 👤"
BTN_HELP = "ПОМОЩЬ 💬"
BTN_WORK = "РАБОТА 🛠️"

OPERATOR_TAG = "@gskalye"
CHAT_LINK = "https://t.me/+HvuVKZkR2-03MzBi"
REVIEWS_LINK = "https://t.me/+HvuVKZkR2-03MzBi"
BOT_TAG = "@CavalierShopBot"

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
{bot_tag}

💬Чат :
{chat_link}

🥇Отзывы :
{reviews_link}

Оператор/Сапорт :
{operator}

🏦Баланс : <b>{balance}</b>
🛍️Количество заказов : <b>{orders}</b>
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦 Баланс: <b>{balance}</b>
🛍️ Количество заказов: <b>{orders}</b>

⬇️ Действия профиля:"""

HELP_TEXT = f"""Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту:
{OPERATOR_TAG}"""

WORK_TEXT = "A"  # ты заменишь сам


# =======================
# KEYBOARDS
# =======================
def reply_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MAIN), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_WORK)],
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def kb_main_inline_city() -> InlineKeyboardMarkup:
    # /start НЕ показывает эту кнопку. Только "ГЛАВНАЯ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одесса ⚓", callback_data="city:odessa")]
    ])


def kb_profile_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="profile:topup")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="profile:promocode")],
        [InlineKeyboardButton(text="🧾 История покупок", callback_data="profile:history")],
    ])


def kb_city_products(city: str, items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    # items: [(button_text, product_code), ...]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data=f"product:{city}:{code}")]
        for (txt, code) in items
    ])


def kb_product_buy(product_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Купить", callback_data=f"buy:{product_code}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="city:odessa")],
    ])


def kb_back_to_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile:open")]
    ])


# =======================
# FSM
# =======================
class PromoStates(StatesGroup):
    waiting_code = State()


# =======================
# DB
# =======================
pool: asyncpg.Pool | None = None
bot_ref: Bot | None = None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            orders_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            main_chat_id BIGINT,
            main_message_id BIGINT,
            profile_chat_id BIGINT,
            profile_message_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            button_text TEXT NOT NULL,
            name TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            description TEXT NOT NULL,
            link TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
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

        # Если товаров нет — добавим 3 демо позиции (легальные/универсальные)
        cnt = await con.fetchval("SELECT COUNT(*) FROM products;")
        if int(cnt) == 0:
            defaults = [
                ("odessa", "pos1", "1) Position 1", "Position 1", Decimal("100.00"), "Описание позиции 1 (замени под себя)", "https://example.com/pos1"),
                ("odessa", "pos2", "2) Position 2", "Position 2", Decimal("150.00"), "Описание позиции 2 (замени под себя)", "https://example.com/pos2"),
                ("odessa", "pos3", "3) Position 3", "Position 3", Decimal("200.00"), "Описание позиции 3 (замени под себя)", "https://example.com/pos3"),
            ]
            for city, code, btn, name, price, desc, link in defaults:
                await con.execute(
                    """
                    INSERT INTO products (city, code, button_text, name, price, description, link, is_active)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)
                    """,
                    city, code, btn, name, price, desc, link
                )


async def ensure_user(user_id: int) -> None:
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )


async def get_user_stats(user_id: int) -> tuple[Decimal, int]:
    await ensure_user(user_id)
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT balance, orders_count FROM users WHERE user_id=$1",
            user_id
        )
    bal = Decimal(row["balance"]) if row else Decimal("0.00")
    ords = int(row["orders_count"]) if row else 0
    return bal, ords


async def render_main_text(user_id: int) -> str:
    bal, ords = await get_user_stats(user_id)
    return MAIN_TEXT_TEMPLATE.format(
        bot_tag=BOT_TAG,
        chat_link=CHAT_LINK,
        reviews_link=REVIEWS_LINK,
        operator=OPERATOR_TAG,
        balance=f"{bal:.2f}",
        orders=ords
    )


async def save_refs(user_id: int, kind: str, chat_id: int, message_id: int) -> None:
    col_chat = "main_chat_id" if kind == "main" else "profile_chat_id"
    col_msg = "main_message_id" if kind == "main" else "profile_message_id"
    async with pool.acquire() as con:
        await con.execute(
            f"UPDATE users SET {col_chat}=$1, {col_msg}=$2 WHERE user_id=$3",
            chat_id, message_id, user_id
        )


async def refresh_main_message(user_id: int) -> None:
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT main_chat_id, main_message_id FROM users WHERE user_id=$1",
            user_id
        )
    if not row or not row["main_chat_id"] or not row["main_message_id"]:
        return
    try:
        await bot_ref.edit_message_text(
            chat_id=int(row["main_chat_id"]),
            message_id=int(row["main_message_id"]),
            text=await render_main_text(user_id),
            reply_markup=kb_main_inline_city(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        pass


async def refresh_profile_message(user_id: int) -> None:
    if bot_ref is None:
        return
    async with pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT profile_chat_id, profile_message_id FROM users WHERE user_id=$1",
            user_id
        )
    if not row or not row["profile_chat_id"] or not row["profile_message_id"]:
        return
    bal, ords = await get_user_stats(user_id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=ords)
    try:
        await bot_ref.edit_message_text(
            chat_id=int(row["profile_chat_id"]),
            message_id=int(row["profile_message_id"]),
            text=text,
            reply_markup=kb_profile_actions(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        pass


async def get_city_buttons(city: str) -> list[tuple[str, str]]:
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT button_text, code
            FROM products
            WHERE city=$1 AND is_active=TRUE
            ORDER BY id ASC
            """,
            city
        )
    return [(r["button_text"], r["code"]) for r in rows]


async def get_product(code: str):
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT city, code, name, price, description, link
            FROM products
            WHERE code=$1 AND is_active=TRUE
            """,
            code
        )


# =======================
# Promo apply (общая функция)
# =======================
async def promo_apply(user_id: int, code_in: str) -> tuple[bool, str]:
    code_in = " ".join((code_in or "").strip().split())
    if not code_in:
        return False, "❌ Пустой промокод."

    await ensure_user(user_id)

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                """
                SELECT code, amount, uses_left
                FROM promo_codes
                WHERE lower(code) = lower($1)
                  AND is_active = TRUE
                  AND uses_left > 0
                FOR UPDATE
                """,
                code_in
            )
            if not row:
                return False, "❌ Промокод недействителен или уже использован."

            already = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
                user_id, row["code"]
            )
            if already:
                return False, "❌ Вы уже активировали этот промокод."

            amount = Decimal(row["amount"])

            await con.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id=$2",
                amount, user_id
            )
            await con.execute(
                "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1",
                row["code"]
            )
            await con.execute(
                "INSERT INTO promo_activations (user_id, code, amount) VALUES ($1,$2,$3)",
                user_id, row["code"], amount
            )

    return True, f"✅ Промокод <b>{row['code']}</b> активирован!\n➕ Начислено: <b>{amount:.2f}</b>"


# =======================
# Handlers
# =======================
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    # /start БЕЗ инлайн "Одесса"
    await ensure_user(message.from_user.id)
    await message.answer(
        await render_main_text(message.from_user.id),
        reply_markup=reply_menu(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(F.text == BTN_MAIN)
async def btn_main(message: Message):
    # ГЛАВНАЯ с инлайн "Одесса" и запоминаем это сообщение для синхры
    await ensure_user(message.from_user.id)
    msg = await message.answer(
        await render_main_text(message.from_user.id),
        reply_markup=kb_main_inline_city(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await save_refs(message.from_user.id, "main", msg.chat.id, msg.message_id)


@dp.message(F.text == BTN_PROFILE)
async def btn_profile(message: Message):
    await ensure_user(message.from_user.id)
    bal, ords = await get_user_stats(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=ords)
    msg = await message.answer(
        text,
        reply_markup=kb_profile_actions(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await save_refs(message.from_user.id, "profile", msg.chat.id, msg.message_id)


@dp.message(F.text == BTN_HELP)
async def btn_help(message: Message):
    await message.answer(
        HELP_TEXT,
        reply_markup=reply_menu(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(F.text == BTN_WORK)
async def btn_work(message: Message):
    await message.answer(
        WORK_TEXT,
        reply_markup=reply_menu(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ----- Profile callbacks
@dp.callback_query(F.data == "profile:open")
async def cb_profile_open(call: CallbackQuery):
    # просто открыть профиль заново
    await call.answer()
    bal, ords = await get_user_stats(call.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{bal:.2f}", orders=ords)
    msg = await call.message.answer(
        text,
        reply_markup=kb_profile_actions(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await save_refs(call.from_user.id, "profile", msg.chat.id, msg.message_id)


@dp.callback_query(F.data == "profile:topup")
async def cb_profile_topup(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "💳 Пополнение баланса временно в разработке.",
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.callback_query(F.data == "profile:history")
async def cb_profile_history(call: CallbackQuery):
    await call.answer()
    await ensure_user(call.from_user.id)
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT product_name, link, price, created_at
            FROM purchases
            WHERE user_id=$1
            ORDER BY created_at DESC
            LIMIT 50
            """,
            call.from_user.id
        )
    if not rows:
        await call.message.answer(
            "🧾 История покупок пуста.",
            reply_markup=kb_back_to_profile(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    lines = ["🧾 <b>История покупок:</b>\n"]
    for r in rows:
        lines.append(f"• <b>{r['product_name']}</b> — {Decimal(r['price']):.2f}\n{r['link']}\n")

    await call.message.answer(
        "\n".join(lines),
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.callback_query(F.data == "profile:promocode")
async def cb_profile_promocode(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoStates.waiting_code)
    await call.message.answer(
        "🎟 Введите промокод одним сообщением.",
        reply_markup=kb_back_to_profile(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.message(PromoStates.waiting_code)
async def promo_input(message: Message, state: FSMContext):
    code_in = message.text or ""
    await state.clear()

    ok, msg = await promo_apply(message.from_user.id, code_in)
    await message.answer(msg, parse_mode="HTML", disable_web_page_preview=True)

    # синхра сразу
    await refresh_main_message(message.from_user.id)
    await refresh_profile_message(message.from_user.id)


# Резерв: команда /promo CODE (если FSM вдруг слетел)
@dp.message(Command("promo"))
async def promo_cmd(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❗️Формат: /promo ВАШ_ПРОМОКОД")
        return
    ok, msg = await promo_apply(message.from_user.id, parts[1])
    await message.answer(msg, parse_mode="HTML", disable_web_page_preview=True)
    await refresh_main_message(message.from_user.id)
    await refresh_profile_message(message.from_user.id)


# ----- City / products flow
@dp.callback_query(F.data == "city:odessa")
async def cb_city_odessa(call: CallbackQuery):
    await call.answer()
    items = await get_city_buttons("odessa")
    if not items:
        await call.message.answer("Пока нет позиций для этого города.")
        return

    await call.message.answer(
        "✅ Вы выбрали город <b>Одесса</b>. Выберите товар:",
        reply_markup=kb_city_products("odessa", items),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@dp.callback_query(F.data.startswith("product:"))
async def cb_product(call: CallbackQuery):
    # product:odessa:CODE
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("Ошибка данных", show_alert=True)
        return
    _, city, code = parts

    product = await get_product(code)
    if not product:
        await call.answer("Товар не найден", show_alert=True)
        return

    text = (
        f"📦 <b>{product['name']}</b>\n"
        f"🏙 Город: <b>{city.capitalize()}</b>\n"
        f"💳 Цена: <b>{Decimal(product['price']):.2f}</b>\n\n"
        f"{product['description']}\n\n"
        f"🔗 {product['link']}"
    )
    await call.message.answer(
        text,
        reply_markup=kb_product_buy(code),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await call.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) != 2:
        await call.answer("Ошибка данных", show_alert=True)
        return
    _, code = parts

    product = await get_product(code)
    if not product:
        await call.answer("Товар не найден", show_alert=True)
        return

    user_id = call.from_user.id
    await ensure_user(user_id)

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                "SELECT balance FROM users WHERE user_id=$1 FOR UPDATE",
                user_id
            )
            balance = Decimal(row["balance"])
            price = Decimal(product["price"])

            if balance < price:
                await call.answer("Недостаточно средств 😔", show_alert=True)
                return

            await con.execute(
                """
                UPDATE users
                SET balance = balance - $1,
                    orders_count = orders_count + 1
                WHERE user_id=$2
                """,
                price, user_id
            )

            await con.execute(
                """
                INSERT INTO purchases (user_id, product_code, product_name, price, link)
                VALUES ($1,$2,$3,$4,$5)
                """,
                user_id, product["code"], product["name"], price, product["link"]
            )

    await call.message.answer(
        f"✅ Покупка успешна!\n\n📦 <b>{product['name']}</b>\n💳 Списано: <b>{price:.2f}</b>\n🔗 {product['link']}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # синхра сразу
    await refresh_main_message(user_id)
    await refresh_profile_message(user_id)

    await call.answer()


# =======================
# ADMIN commands for products & promo
# =======================
@dp.message(Command("addproduct"))
async def admin_addproduct(message: Message):
    if not is_admin(message.from_user.id):
        return
    # Формат:
    # /addproduct city code "Button Text" "Name" 123.45 https://link Description...
    # Чтобы было проще — сделаем формат попроще:
    # /addproduct city code price https://link ButtonText | Name | Description
    # пример:
    # /addproduct odessa pos4 99.99 https://site.com Btn | Name | Desc

    parts = (message.text or "").split(maxsplit=4)
    if len(parts) < 5:
        await message.answer(
            "Формат:\n/addproduct city code price link Btn | Name | Description"
        )
        return

    _, city, code, price_str, rest = parts
    rest2 = rest.split(maxsplit=1)
    if len(rest2) < 2:
        await message.answer("Формат:\n/addproduct city code price link Btn | Name | Description")
        return
    link = rest2[0].strip()
    meta = rest2[1].strip()

    try:
        price = Decimal(price_str)
    except Exception:
        await message.answer("Цена должна быть числом типа 100 или 100.00")
        return

    # meta: "Btn | Name | Description"
    chunks = [x.strip() for x in meta.split("|")]
    if len(chunks) < 3:
        await message.answer("Нужно: Btn | Name | Description")
        return

    button_text, name, description = chunks[0], chunks[1], "|".join(chunks[2:]).strip()

    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO products (city, code, button_text, name, price, description, link, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)
            ON CONFLICT (code) DO UPDATE
            SET city=EXCLUDED.city,
                button_text=EXCLUDED.button_text,
                name=EXCLUDED.name,
                price=EXCLUDED.price,
                description=EXCLUDED.description,
                link=EXCLUDED.link,
                is_active=TRUE
            """,
            city.lower(), code, button_text, name, price, description, link
        )
    await message.answer(f"✅ Товар добавлен/обновлён: {code}")


@dp.message(Command("listproducts"))
async def admin_listproducts(message: Message):
    if not is_admin(message.from_user.id):
        return
    city = "odessa"
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        city = parts[1].strip().lower()

    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT code, button_text, name, price, is_active
            FROM products
            WHERE city=$1
            ORDER BY id ASC
            """,
            city
        )
    if not rows:
        await message.answer("Пусто.")
        return

    lines = [f"📦 <b>Товары ({city}):</b>\n"]
    for r in rows:
        st = "✅" if r["is_active"] else "❌"
        lines.append(f"{st} <b>{r['code']}</b> — {r['button_text']} — {r['name']} — {Decimal(r['price']):.2f}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("delproduct"))
async def admin_delproduct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /delproduct code")
        return
    code = parts[1].strip()
    async with pool.acquire() as con:
        await con.execute("UPDATE products SET is_active=FALSE WHERE code=$1", code)
    await message.answer(f"✅ Отключено: {code}")


@dp.message(Command("addpromo"))
async def admin_addpromo(message: Message):
    if not is_admin(message.from_user.id):
        return
    # /addpromo CODE 300 5
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: /addpromo CODE AMOUNT [USES]")
        return
    code = parts[1].strip()
    try:
        amount = Decimal(parts[2])
    except Exception:
        await message.answer("AMOUNT должно быть числом, напр: 300 или 300.00")
        return
    uses = 1
    if len(parts) >= 4 and parts[3].isdigit():
        uses = int(parts[3])

    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO promo_codes (code, amount, is_active, uses_left)
            VALUES ($1,$2,TRUE,$3)
            ON CONFLICT (code) DO UPDATE
            SET amount=EXCLUDED.amount,
                is_active=TRUE,
                uses_left=EXCLUDED.uses_left
            """,
            code, amount, uses
        )
    await message.answer(f"✅ Промокод добавлен: {code} (+{amount:.2f}, uses={uses})")


# =======================
# MAIN
# =======================
async def main():
    global bot_ref
    await db_init()

    bot = Bot(token=BOT_TOKEN)
    bot_ref = bot

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
