import os
import asyncio
import decimal
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in environment variables.")


# ========= TEXTS (как ты просил — оставляю максимально твоё) =========
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
https://t.me/gskalye

🏦Баланс : {balance}
🛍️Количество заказов : {orders}
"""

PROFILE_TEXT_TEMPLATE = """👤 Профиль

🏦 Баланс: {balance}
🛍️ Количество заказов: {orders}
"""

HELP_TEXT = """Если ты возник с проблемой, или есть какой либо вопрос, пиши Оператору/Сапорту :
https://t.me/gskalye
"""

# "РАБОТА" — как ты просил, одну букву, потом сам заменишь
WORK_TEXT = "."

# ========= UI =========
def bottom_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ГЛАВНАЯ 🔘"), KeyboardButton(text="ПРОФИЛЬ 👤")],
            [KeyboardButton(text="ПОМОЩЬ 💬"), KeyboardButton(text="РАБОТА 💸")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_inline_kb() -> InlineKeyboardMarkup:
    # кнопка "Одесса" прикреплена к сообщению (inline)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Одесса ⚓", callback_data="city:odesa")]
        ]
    )


def city_odesa_kb() -> InlineKeyboardMarkup:
    # тут потом легко добавишь кнопки/категории
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Позиции", callback_data="odesa:products")],
            [InlineKeyboardButton(text="Назад", callback_data="back:main")],
        ]
    )


def profile_actions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пополнить баланс", callback_data="profile:topup")],
            [InlineKeyboardButton(text="Активировать промокод", callback_data="profile:promo")],
            [InlineKeyboardButton(text="История покупок", callback_data="profile:history")],
        ]
    )


# ========= DB =========
@dataclass
class UserRow:
    user_id: int
    balance: decimal.Decimal
    orders_count: int


pool: asyncpg.Pool | None = None


async def db_init() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as con:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance NUMERIC(12,2) NOT NULL DEFAULT 0,
                orders_count INT NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
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
                code TEXT NOT NULL REFERENCES promo_codes(code),
                activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(user_id, code)
            );

            CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY,
                city TEXT NOT NULL,
                name TEXT NOT NULL,
                price NUMERIC(12,2) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
            """
        )

        # демо-товары (можешь удалить)
        # добавляются только если таблица пустая
        count = await con.fetchval("SELECT COUNT(*) FROM products;")
        if count == 0:
            await con.executemany(
                "INSERT INTO products(city, name, price) VALUES($1, $2, $3);",
                [
                    ("odesa", "Позиция #1", decimal.Decimal("10.00")),
                    ("odesa", "Позиция #2", decimal.Decimal("25.00")),
                ],
            )


async def get_or_create_user(user_id: int) -> UserRow:
    assert pool is not None
    async with pool.acquire() as con:
        row = await con.fetchrow("SELECT user_id, balance, orders_count FROM users WHERE user_id=$1;", user_id)
        if row is None:
            await con.execute("INSERT INTO users(user_id) VALUES($1);", user_id)
            row = await con.fetchrow("SELECT user_id, balance, orders_count FROM users WHERE user_id=$1;", user_id)
        return UserRow(user_id=row["user_id"], balance=row["balance"], orders_count=row["orders_count"])


async def add_balance(user_id: int, amount: decimal.Decimal) -> None:
    assert pool is not None
    async with pool.acquire() as con:
        await con.execute(
            "UPDATE users SET balance = balance + $2 WHERE user_id=$1;",
            user_id, amount
        )


async def list_products(city: str):
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id, name, price FROM products WHERE city=$1 AND is_active=TRUE ORDER BY id ASC;",
            city
        )
    return rows


async def buy_product(user_id: int, product_id: int) -> tuple[bool, str]:
    """
    Возвращает (ok, message)
    """
    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            user = await con.fetchrow("SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE;", user_id)
            if not user:
                await con.execute("INSERT INTO users(user_id) VALUES($1);", user_id)
                user = await con.fetchrow("SELECT balance, orders_count FROM users WHERE user_id=$1 FOR UPDATE;", user_id)

            product = await con.fetchrow("SELECT name, price FROM products WHERE id=$1 AND is_active=TRUE;", product_id)
            if not product:
                return False, "Эта позиция недоступна."

            balance = decimal.Decimal(user["balance"])
            price = decimal.Decimal(product["price"])
            if balance < price:
                return False, "Недостаточно средств на балансе."

            # списать баланс
            await con.execute("UPDATE users SET balance = balance - $2, orders_count = orders_count + 1 WHERE user_id=$1;",
                              user_id, price)

            # заглушка ссылки (потом заменишь на реальную выдачу)
            # Важно: это НЕ “выдача запрещённого”, а просто поле ссылки/ключа/инфы для легального товара.
            link = f"https://example.com/order/{user_id}-{product_id}-{int(datetime.now(timezone.utc).timestamp())}"

            await con.execute(
                "INSERT INTO purchases(user_id, product_name, price, link) VALUES($1, $2, $3, $4);",
                user_id, product["name"], price, link
            )

            return True, f"✅ Покупка успешна!\n\n📦 {product['name']}\n💳 Списано: {price}\n🔗 Ссылка: {link}"


async def get_history(user_id: int) -> list[asyncpg.Record]:
    assert pool is not None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT product_name, link, created_at FROM purchases WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20;",
            user_id
        )
    return rows


async def activate_promo(user_id: int, code: str) -> tuple[bool, str]:
    code = code.strip().upper()
    assert pool is not None
    async with pool.acquire() as con:
        async with con.transaction():
            promo = await con.fetchrow(
                "SELECT code, amount, is_active, uses_left FROM promo_codes WHERE code=$1 FOR UPDATE;",
                code
            )
            if not promo or not promo["is_active"] or promo["uses_left"] <= 0:
                return False, "❌ Промокод недействителен."

            # не даём активировать один и тот же код повторно
            used = await con.fetchval(
                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2;",
                user_id, code
            )
            if used:
                return False, "❌ Ты уже активировал этот промокод."

            amount = decimal.Decimal(promo["amount"])

            await con.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1;", code)
            await con.execute("INSERT INTO promo_activations(user_id, code) VALUES($1, $2);", user_id, code)
            await con.execute("UPDATE users SET balance = balance + $2 WHERE user_id=$1;", user_id, amount)

            return True, f"✅ Промокод активирован!\n🏦 Начислено: {amount}"


# ========= FSM =========
class PromoStates(StatesGroup):
    waiting_code = State()


# ========= HANDLERS =========
async def send_main(message: Message) -> None:
    user = await get_or_create_user(message.from_user.id)
    text = MAIN_TEXT_TEMPLATE.format(balance=f"{user.balance:.2f}", orders=user.orders_count)
    await message.answer(text, reply_markup=bottom_menu_kb())
    await message.answer(" ", reply_markup=main_inline_kb())  # inline кнопка под “пустым” сообщением


# Вариант “без засора”: редактируем второе сообщение вместо отправки нового
# Но Telegram не позволяет прикрепить inline к уже отправленному “первому” сообщению другим хендлером.
# Поэтому: держим отдельное “техническое” сообщение-держатель с inline-кнопками, и редактируем его.


async def start_cmd(message: Message) -> None:
    await send_main(message)


async def main_btn(message: Message) -> None:
    await send_main(message)


async def profile_btn(message: Message) -> None:
    user = await get_or_create_user(message.from_user.id)
    text = PROFILE_TEXT_TEMPLATE.format(balance=f"{user.balance:.2f}", orders=user.orders_count)
    await message.answer(text, reply_markup=bottom_menu_kb())
    await message.answer("Действия профиля:", reply_markup=profile_actions_kb())


async def help_btn(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=bottom_menu_kb())


async def work_btn(message: Message) -> None:
    await message.answer(WORK_TEXT, reply_markup=bottom_menu_kb())


async def on_city(callback: CallbackQuery) -> None:
    if callback.data != "city:odesa":
        return
    await callback.answer()
    await callback.message.edit_text("Одесса ⚓", reply_markup=city_odesa_kb())


async def on_back_main(callback: CallbackQuery) -> None:
    await callback.answer()
    # возвращаем “держатель” обратно на кнопку Одесса
    await callback.message.edit_text(" ", reply_markup=main_inline_kb())


async def on_odesa_products(callback: CallbackQuery) -> None:
    await callback.answer()

    rows = await list_products("odesa")
    if not rows:
        await callback.message.edit_text("Пока нет позиций.", reply_markup=city_odesa_kb())
        return

    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['name']} — {decimal.Decimal(r['price']):.2f}",
                                        callback_data=f"buy:{r['id']}")])
    kb.append([InlineKeyboardButton(text="Назад", callback_data="city:odesa")])

    await callback.message.edit_text("Выбери позицию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def on_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    _, pid = callback.data.split(":", 1)
    ok, msg = await buy_product(callback.from_user.id, int(pid))
    await callback.message.answer(msg)


async def on_profile_topup(callback: CallbackQuery) -> None:
    await callback.answer()
    # заглушка — позже подключишь оплату
    await callback.message.answer("Пополнение баланса: скоро добавим 💳")


async def on_profile_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PromoStates.waiting_code)
    await callback.message.answer("Введи промокод одним сообщением (пример: PROMO_TEST)")


async def promo_code_message(message: Message, state: FSMContext) -> None:
    code = message.text.strip()
    ok, msg = await activate_promo(message.from_user.id, code)
    await message.answer(msg)
    await state.clear()


async def on_profile_history(callback: CallbackQuery) -> None:
    await callback.answer()
    rows = await get_history(callback.from_user.id)
    if not rows:
        await callback.message.answer("История пуста.")
        return

    text = "🧾 История покупок:\n\n"
    for r in rows:
        dt = r["created_at"].strftime("%Y-%m-%d %H:%M")
        text += f"• {r['product_name']} ({dt})\n{r['link']}\n\n"

    await callback.message.answer(text)


# ========= MAIN =========
async def main() -> None:
    await db_init()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # /start и “ГЛАВНАЯ” одинаково
    dp.message.register(start_cmd, CommandStart())
    dp.message.register(main_btn, F.text == "ГЛАВНАЯ 🔘")

    # низ меню
    dp.message.register(profile_btn, F.text == "ПРОФИЛЬ 👤")
    dp.message.register(help_btn, F.text == "ПОМОЩЬ 💬")
    dp.message.register(work_btn, F.text == "РАБОТА 💸")

    # FSM промокоды
    dp.message.register(promo_code_message, PromoStates.waiting_code)

    # callbacks
    dp.callback_query.register(on_city, F.data == "city:odesa")
    dp.callback_query.register(on_back_main, F.data == "back:main")
    dp.callback_query.register(on_odesa_products, F.data == "odesa:products")
    dp.callback_query.register(on_buy, F.data.startswith("buy:"))

    dp.callback_query.register(on_profile_topup, F.data == "profile:topup")
    dp.callback_query.register(on_profile_promo, F.data == "profile:promo")
    dp.callback_query.register(on_profile_history, F.data == "profile:history")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
