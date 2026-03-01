diff --git a/bot.py b/bot.py
index 693556cd93ac92a38cb35e252fb0137b2c57e7cf..fe26bc4d43c5d05ac3706b11fdff5fdedbb5365f 100644
--- a/bot.py
+++ b/bot.py
@@ -1,434 +1,697 @@
-import os
-import asyncio
-import logging
-from decimal import Decimal, InvalidOperation
-from typing import Optional, List, Tuple
-
-import asyncpg
-from aiogram import Bot, Dispatcher, F
-from aiogram.types import Message, CallbackQuery
-from aiogram.filters import CommandStart, Command
-
-logging.basicConfig(level=logging.INFO)
-
-BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
-DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
-
-# Админ(ы): через запятую, напр: "123,456"
-ADMIN_IDS = set()
-_raw_admins = os.getenv("ADMIN_IDS", "").strip()
-if _raw_admins:
-    for x in _raw_admins.split(","):
-        x = x.strip()
-        if x.isdigit():
-            ADMIN_IDS.add(int(x))
-
-if not BOT_TOKEN:
-    raise RuntimeError("BOT_TOKEN is missing")
-if not DATABASE_URL:
-    raise RuntimeError("DATABASE_URL is missing (set Postgres -> DATABASE_URL in Railway Variables)")
-
-pool: Optional[asyncpg.Pool] = None
-
-# ---- ВИТРИНА (3 кнопки под “Одесса”) ----
-# Меняешь тут легко: (код, отображаемое_имя)
-ODESSA_ITEMS: List[Tuple[str, str]] = [
-    ("saint", "1) saint"),
-    ("big_bob", "2) big bob"),
-    ("shenen", "3) shenen"),
-]
-
-# Эти “коды” должны существовать в таблице products.code (мы автосоздадим демо при запуске)
-DEMO_PRODUCTS = [
-    # code, title, price, link_to_deliver
-    ("saint", "saint", Decimal("10.00"), "https://example.com/saint"),
-    ("big_bob", "big bob", Decimal("15.00"), "https://example.com/big-bob"),
-    ("shenen", "shenen", Decimal("20.00"), "https://example.com/shenen"),
-]
-
-
-# ----------------- DB -----------------
-async def db_init():
-    global pool
-    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
-
-    async with pool.acquire() as con:
-        # users
-        await con.execute("""
-        CREATE TABLE IF NOT EXISTS users (
-            user_id BIGINT PRIMARY KEY,
-            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
-            orders_count INT NOT NULL DEFAULT 0,
-            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-        );
-        """)
-
-        # products
-        await con.execute("""
-        CREATE TABLE IF NOT EXISTS products (
-            code TEXT PRIMARY KEY,
-            title TEXT NOT NULL,
-            price NUMERIC(12,2) NOT NULL,
-            link TEXT NOT NULL,
-            is_active BOOLEAN NOT NULL DEFAULT TRUE,
-            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-        );
-        """)
-
-        # purchases
-        await con.execute("""
-        CREATE TABLE IF NOT EXISTS purchases (
-            id BIGSERIAL PRIMARY KEY,
-            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
-            product_code TEXT NOT NULL REFERENCES products(code),
-            product_title TEXT NOT NULL,
-            price NUMERIC(12,2) NOT NULL,
-            link TEXT NOT NULL,
-            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-        );
-        """)
-
-        # promo_codes
-        await con.execute("""
-        CREATE TABLE IF NOT EXISTS promo_codes (
-            code TEXT PRIMARY KEY,
-            amount NUMERIC(12,2) NOT NULL,
-            uses_left INT NOT NULL DEFAULT 1,
-            is_active BOOLEAN NOT NULL DEFAULT TRUE,
-            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-        );
-        """)
-
-        # promo_activations (ВАЖНО: есть amount -> чтобы не было вашей ошибки)
-        await con.execute("""
-        CREATE TABLE IF NOT EXISTS promo_activations (
-            id BIGSERIAL PRIMARY KEY,
-            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
-            code TEXT NOT NULL REFERENCES promo_codes(code),
-            amount NUMERIC(12,2) NOT NULL,
-            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
-            UNIQUE(user_id, code)
-        );
-        """)
-
-        # демо товары (чтобы сразу работало)
-        for code, title, price, link in DEMO_PRODUCTS:
-            await con.execute("""
-            INSERT INTO products(code, title, price, link, is_active)
-            VALUES($1,$2,$3,$4,TRUE)
-            ON CONFLICT (code) DO NOTHING;
-            """, code, title, price, link)
-
-
-async def ensure_user(user_id: int):
-    async with pool.acquire() as con:
-        await con.execute("""
-        INSERT INTO users(user_id) VALUES($1)
-        ON CONFLICT (user_id) DO NOTHING;
-        """, user_id)
-
-
-async def get_user(user_id: int):
-    async with pool.acquire() as con:
-        row = await con.fetchrow("SELECT user_id, balance, orders_count FROM users WHERE user_id=$1", user_id)
-        return row
-
-
-async def add_balance(user_id: int, amount: Decimal):
-    async with pool.acquire() as con:
-        await con.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)
-
-
-async def dec_balance(user_id: int, amount: Decimal):
-    async with pool.acquire() as con:
-        await con.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2", amount, user_id)
-
-
-async def inc_orders(user_id: int):
-    async with pool.acquire() as con:
-        await con.execute("UPDATE users SET orders_count = orders_count + 1 WHERE user_id=$1", user_id)
-
-
-async def list_user_purchases(user_id: int):
-    async with pool.acquire() as con:
-        rows = await con.fetch("""
-        SELECT product_title, link, created_at
-        FROM purchases
-        WHERE user_id=$1
-        ORDER BY created_at DESC
-        LIMIT 50
-        """, user_id)
-        return rows
-
-
-async def get_product(code: str):
-    async with pool.acquire() as con:
-        row = await con.fetchrow("""
-        SELECT code, title, price, link, is_active
-        FROM products
-        WHERE code=$1
-        """, code)
-        return row
-
-
-async def make_purchase(user_id: int, product_code: str):
-    async with pool.acquire() as con:
-        async with con.transaction():
-            user = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", user_id)
-            if not user:
-                return False, "Пользователь не найден."
-
-            product = await con.fetchrow("""
-            SELECT code, title, price, link, is_active
-            FROM products
-            WHERE code=$1
-            """, product_code)
-            if not product or not product["is_active"]:
-                return False, "Товар недоступен."
-
-            price = Decimal(str(product["price"]))
-            bal = Decimal(str(user["balance"]))
-            if bal < price:
-                return False, "Недостаточно средств."
-
-            await con.execute("UPDATE users SET balance = balance - $1, orders_count = orders_count + 1 WHERE user_id=$2", price, user_id)
-            await con.execute("""
-            INSERT INTO purchases(user_id, product_code, product_title, price, link)
-            VALUES($1,$2,$3,$4,$5)
-            """, user_id, product["code"], product["title"], price, product["link"])
-
-            return True, product["link"]
-
-
-async def add_promo(code: str, amount: Decimal, uses_left: int, is_active: bool = True):
-    async with pool.acquire() as con:
-        await con.execute("""
-        INSERT INTO promo_codes(code, amount, uses_left, is_active)
-        VALUES($1,$2,$3,$4)
-        ON CONFLICT (code) DO UPDATE
-        SET amount=EXCLUDED.amount, uses_left=EXCLUDED.uses_left, is_active=EXCLUDED.is_active
-        """, code, amount, uses_left, is_active)
-
-
-async def apply_promo(user_id: int, code: str):
-    async with pool.acquire() as con:
-        async with con.transaction():
-            promo = await con.fetchrow("""
-            SELECT code, amount, uses_left, is_active
-            FROM promo_codes
-            WHERE code=$1
-            FOR UPDATE
-            """, code)
-            if not promo or not promo["is_active"]:
-                return False, "❌ Промокод недействителен или уже использован."
-
-            if promo["uses_left"] <= 0:
-                return False, "❌ Промокод недействителен или уже использован."
-
-            # был ли уже активирован этим юзером
-            used = await con.fetchrow("""
-            SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2
-            """, user_id, code)
-            if used:
-                return False, "❌ Промокод недействителен или уже использован."
-
-            amount = Decimal(str(promo["amount"]))
-
-            await con.execute("""
-            INSERT INTO promo_activations(user_id, code, amount)
-            VALUES($1,$2,$3)
-            """, user_id, code, amount)
-
-            await con.execute("""
-            UPDATE promo_codes SET uses_left = uses_left - 1
-            WHERE code=$1
-            """, code)
-
-            await con.execute("""
-            UPDATE users SET balance = balance + $1
-            WHERE user_id=$2
-            """, amount, user_id)
-
-            return True, f"✅ Промокод активирован: +{amount:.2f}"
-
-
-# ----------------- UI (кнопки) -----------------
-from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
-
-def kb_main_reply():
-    b = ReplyKeyboardBuilder()
-    b.button(text="ГЛАВНАЯ 🏠")
-    b.button(text="ПРОФИЛЬ 👤")
-    b.button(text="ПОМОЩЬ 💬")
-    b.button(text="РАБОТА 🧳")
-    b.adjust(2, 2)
-    return b.as_markup(resize_keyboard=True)
-
-def kb_main_inline():
-    b = InlineKeyboardBuilder()
-    b.button(text="Одесса ⚓️", callback_data="city:odessa")
-    return b.as_markup()
-
-def kb_odessa_items():
-    b = InlineKeyboardBuilder()
-    for code, title in ODESSA_ITEMS:
-        b.button(text=title, callback_data=f"buy:{code}")
-    b.adjust(1, 1, 1)
-    return b.as_markup()
-
-def kb_profile_inline():
-    b = InlineKeyboardBuilder()
-    b.button(text="💳 Пополнить баланс", callback_data="profile:topup")
-    b.button(text="🎟 Активировать промокод", callback_data="profile:promo")
-    b.button(text="🧾 История покупок", callback_data="profile:history")
-    b.adjust(1, 1, 1)
-    return b.as_markup()
-
-
-# ----------------- Handlers -----------------
-dp = Dispatcher()
-
-@dp.message(CommandStart())
-async def start_cmd(message: Message):
-    await ensure_user(message.from_user.id)
-    await message.answer("Добро пожаловать.", reply_markup=kb_main_reply())
-
-@dp.message(F.text == "ГЛАВНАЯ 🏠")
-async def main_menu(message: Message):
-    await ensure_user(message.from_user.id)
-    u = await get_user(message.from_user.id)
-    bal = Decimal(str(u["balance"])) if u else Decimal("0")
-    orders = int(u["orders_count"]) if u else 0
-
-    text = (
-        "Актуальные ссылки\n\n"
-        f"Оператор/Сапорт: @gskalye\n\n"
-        f"💵 Баланс: {bal:.2f}\n"
-        f"🛍 Количество заказов: {orders}\n"
-    )
-    await message.answer(text, reply_markup=kb_main_inline())
-
-@dp.message(F.text == "ПРОФИЛЬ 👤")
-async def profile_menu(message: Message):
-    await ensure_user(message.from_user.id)
-    u = await get_user(message.from_user.id)
-    bal = Decimal(str(u["balance"])) if u else Decimal("0")
-    orders = int(u["orders_count"]) if u else 0
-
-    text = (
-        "👤 Профиль\n\n"
-        f"💵 Баланс: {bal:.2f}\n"
-        f"🛍 Количество заказов: {orders}\n"
-    )
-    await message.answer(text, reply_markup=kb_profile_inline())
-
-@dp.message(F.text == "ПОМОЩЬ 💬")
-async def help_menu(message: Message):
-    await message.answer("Поддержка: @gskalye", reply_markup=kb_main_reply())
-
-@dp.message(F.text == "РАБОТА 🧳")
-async def work_menu(message: Message):
-    await message.answer("Раздел в разработке.", reply_markup=kb_main_reply())
-
-
-# ---- Odessa ----
-@dp.callback_query(F.data == "city:odessa")
-async def city_odessa(call: CallbackQuery):
-    # ВАЖНО: отдельное сообщение с товарами, не меняем основное
-    await call.message.answer(
-        "Вы выбрали город Одесса. Выберите товар:",
-        reply_markup=kb_odessa_items()
-    )
-    await call.answer()
-
-# ---- Buy ----
-@dp.callback_query(F.data.startswith("buy:"))
-async def buy_item(call: CallbackQuery):
-    await ensure_user(call.from_user.id)
-    code = call.data.split(":", 1)[1]
-
-    ok, result = await make_purchase(call.from_user.id, code)
-    if not ok:
-        await call.message.answer(f"❌ {result}")
-        await call.answer()
-        return
-
-    # result = link
-    await call.message.answer(f"✅ Покупка успешна.\nВаша ссылка: {result}")
-    await call.answer()
-
-# ---- Profile buttons ----
-@dp.callback_query(F.data == "profile:topup")
-async def profile_topup(call: CallbackQuery):
-    await call.message.answer("Пополнение в разработке.")
-    await call.answer()
-
-@dp.callback_query(F.data == "profile:promo")
-async def profile_promo(call: CallbackQuery):
-    await call.message.answer("Введите промокод командой:\n/promo ВАШ_ПРОМОКОД")
-    await call.answer()
-
-@dp.callback_query(F.data == "profile:history")
-async def profile_history(call: CallbackQuery):
-    await ensure_user(call.from_user.id)
-    rows = await list_user_purchases(call.from_user.id)
-    if not rows:
-        await call.message.answer("История пуста.")
-        await call.answer()
-        return
-
-    text = "🧾 История покупок:\n\n"
-    for r in rows:
-        text += f"• {r['product_title']}\n{r['link']}\n\n"
-    await call.message.answer(text)
-    await call.answer()
-
-
-# ---- PROMO commands ----
-@dp.message(Command("promo"))
-async def cmd_promo(message: Message):
-    await ensure_user(message.from_user.id)
-
-    parts = message.text.strip().split(maxsplit=1)
-    if len(parts) < 2:
-        await message.answer("Формат: /promo ВАШ_ПРОМОКОД")
-        return
-
-    code = parts[1].strip()
-    ok, txt = await apply_promo(message.from_user.id, code)
-    await message.answer(txt)
-
-
-@dp.message(Command("addpromo"))
-async def cmd_addpromo(message: Message):
-    if message.from_user.id not in ADMIN_IDS:
-        await message.answer("Нет доступа.")
-        return
-
-    parts = message.text.strip().split()
-    # /addpromo CODE AMOUNT USES
-    if len(parts) < 4:
-        await message.answer("Формат: /addpromo CODE AMOUNT USES")
-        return
-
-    code = parts[1].strip()
-    try:
-        amount = Decimal(parts[2].replace(",", "."))
-        uses = int(parts[3])
-        if uses < 1:
-            raise ValueError
-    except (InvalidOperation, ValueError):
-        await message.answer("Неверные данные. Пример: /addpromo TEST300 300 1")
-        return
-
-    await add_promo(code, amount, uses, True)
-    await message.answer(f"✅ Промокод создан: {code} (+{amount:.2f}, uses={uses})")
-
-
-async def main():
-    await db_init()
-    bot = Bot(BOT_TOKEN)
-    await dp.start_polling(bot)
-
-if __name__ == "__main__":
-    asyncio.run(main())
+import asyncio
+import logging
+import os
+from decimal import Decimal, InvalidOperation
+from typing import Optional
+
+import asyncpg
+from aiogram import Bot, Dispatcher, F
+from aiogram.filters import Command, CommandStart
+from aiogram.types import CallbackQuery, Message
+from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
+
+logging.basicConfig(level=logging.INFO)
+logger = logging.getLogger(__name__)
+
+BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
+DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
+
+ADMIN_IDS = set()
+_raw_admins = os.getenv("ADMIN_IDS", "").strip()
+if _raw_admins:
+    for x in _raw_admins.split(","):
+        x = x.strip()
+        if x.isdigit():
+            ADMIN_IDS.add(int(x))
+
+pool: Optional[asyncpg.Pool] = None
+
+PRODUCTS = [
+    {
+        "code": "saint",
+        "button": "1 saint",
+        "title": "saint",
+        "price": Decimal("10.00"),
+        "link": "https://example.com/saint",
+        "description": "Готовая позиция Odessa.",
+    },
+    {
+        "code": "big_bob",
+        "button": "2 big bob",
+        "title": "big bob",
+        "price": Decimal("15.00"),
+        "link": "https://example.com/big-bob",
+        "description": "Горячая позиция Odessa.",
+    },
+    {
+        "code": "shenen",
+        "button": "3 shenen",
+        "title": "shenen",
+        "price": Decimal("20.00"),
+        "link": "https://example.com/shenen",
+        "description": "Превосходное качество товара.",
+    },
+]
+
+
+def require_env() -> None:
+    if not BOT_TOKEN:
+        raise RuntimeError("BOT_TOKEN is missing")
+    if not DATABASE_URL:
+        raise RuntimeError("DATABASE_URL is missing")
+
+
+def normalize_promo_code(raw: str) -> str:
+    return " ".join(raw.strip().upper().split())
+
+
+async def db_init() -> None:
+    global pool
+    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
+
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            CREATE TABLE IF NOT EXISTS users (
+                user_id BIGINT PRIMARY KEY,
+                balance NUMERIC(12,2) NOT NULL DEFAULT 0,
+                orders_count INT NOT NULL DEFAULT 0,
+                awaiting_promo BOOLEAN NOT NULL DEFAULT FALSE,
+                main_chat_id BIGINT NULL,
+                main_message_id BIGINT NULL,
+                profile_chat_id BIGINT NULL,
+                profile_message_id BIGINT NULL,
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
+                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
+            );
+            """
+        )
+
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS awaiting_promo BOOLEAN")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS main_chat_id BIGINT")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS main_message_id BIGINT")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_chat_id BIGINT")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_message_id BIGINT")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ")
+        await con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ")
+        await con.execute("UPDATE users SET awaiting_promo = FALSE WHERE awaiting_promo IS NULL")
+        await con.execute("UPDATE users SET created_at = NOW() WHERE created_at IS NULL")
+        await con.execute("UPDATE users SET updated_at = NOW() WHERE updated_at IS NULL")
+        await con.execute("ALTER TABLE users ALTER COLUMN awaiting_promo SET DEFAULT FALSE")
+        await con.execute("ALTER TABLE users ALTER COLUMN awaiting_promo SET NOT NULL")
+        await con.execute("ALTER TABLE users ALTER COLUMN created_at SET DEFAULT NOW()")
+        await con.execute("ALTER TABLE users ALTER COLUMN created_at SET NOT NULL")
+        await con.execute("ALTER TABLE users ALTER COLUMN updated_at SET DEFAULT NOW()")
+        await con.execute("ALTER TABLE users ALTER COLUMN updated_at SET NOT NULL")
+
+        await con.execute(
+            """
+            CREATE TABLE IF NOT EXISTS products (
+                code TEXT PRIMARY KEY,
+                title TEXT NOT NULL,
+                price NUMERIC(12,2) NOT NULL,
+                link TEXT NOT NULL,
+                is_active BOOLEAN NOT NULL DEFAULT TRUE,
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
+            );
+            """
+        )
+
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS title TEXT")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS price NUMERIC(12,2)")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS link TEXT")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ")
+        await con.execute("UPDATE products SET title = code WHERE title IS NULL")
+        await con.execute("UPDATE products SET price = 0 WHERE price IS NULL")
+        await con.execute("UPDATE products SET link = '' WHERE link IS NULL")
+        await con.execute("UPDATE products SET is_active = TRUE WHERE is_active IS NULL")
+        await con.execute("UPDATE products SET created_at = NOW() WHERE created_at IS NULL")
+        await con.execute("ALTER TABLE products ALTER COLUMN title SET NOT NULL")
+        await con.execute("ALTER TABLE products ALTER COLUMN price SET NOT NULL")
+        await con.execute("ALTER TABLE products ALTER COLUMN link SET NOT NULL")
+        await con.execute("ALTER TABLE products ALTER COLUMN is_active SET DEFAULT TRUE")
+        await con.execute("ALTER TABLE products ALTER COLUMN is_active SET NOT NULL")
+        await con.execute("ALTER TABLE products ALTER COLUMN created_at SET DEFAULT NOW()")
+        await con.execute("ALTER TABLE products ALTER COLUMN created_at SET NOT NULL")
+
+        await con.execute(
+            """
+            CREATE TABLE IF NOT EXISTS purchases (
+                id BIGSERIAL PRIMARY KEY,
+                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
+                product_code TEXT NOT NULL REFERENCES products(code),
+                product_title TEXT NOT NULL,
+                price NUMERIC(12,2) NOT NULL,
+                link TEXT NOT NULL,
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
+            );
+            """
+        )
+
+        await con.execute(
+            """
+            CREATE TABLE IF NOT EXISTS promo_codes (
+                code TEXT PRIMARY KEY,
+                amount NUMERIC(12,2) NOT NULL,
+                uses_left INT NOT NULL DEFAULT 1,
+                is_active BOOLEAN NOT NULL DEFAULT TRUE,
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
+            );
+            """
+        )
+
+        await con.execute(
+            """
+            CREATE TABLE IF NOT EXISTS promo_activations (
+                id BIGSERIAL PRIMARY KEY,
+                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
+                code TEXT NOT NULL REFERENCES promo_codes(code),
+                amount NUMERIC(12,2) NOT NULL,
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
+                UNIQUE(user_id, code)
+            );
+            """
+        )
+
+        for p in PRODUCTS:
+            await con.execute(
+                """
+                INSERT INTO products(code, title, price, link, is_active)
+                VALUES($1,$2,$3,$4,TRUE)
+                ON CONFLICT (code) DO UPDATE
+                SET title = EXCLUDED.title,
+                    price = EXCLUDED.price,
+                    link = EXCLUDED.link,
+                    is_active = TRUE;
+                """,
+                p["code"],
+                p["title"],
+                p["price"],
+                p["link"],
+            )
+
+
+async def ensure_user(user_id: int) -> None:
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            INSERT INTO users(user_id) VALUES($1)
+            ON CONFLICT (user_id) DO NOTHING;
+            """,
+            user_id,
+        )
+
+
+async def get_user(user_id: int):
+    async with pool.acquire() as con:
+        return await con.fetchrow(
+            """
+            SELECT user_id, balance, orders_count, awaiting_promo,
+                   main_chat_id, main_message_id, profile_chat_id, profile_message_id
+            FROM users
+            WHERE user_id=$1
+            """,
+            user_id,
+        )
+
+
+async def set_awaiting_promo(user_id: int, value: bool) -> None:
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            UPDATE users
+            SET awaiting_promo=$1, updated_at=NOW()
+            WHERE user_id=$2
+            """,
+            value,
+            user_id,
+        )
+
+
+async def save_main_message(user_id: int, chat_id: int, message_id: int) -> None:
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            UPDATE users
+            SET main_chat_id=$1, main_message_id=$2, updated_at=NOW()
+            WHERE user_id=$3
+            """,
+            chat_id,
+            message_id,
+            user_id,
+        )
+
+
+async def save_profile_message(user_id: int, chat_id: int, message_id: int) -> None:
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            UPDATE users
+            SET profile_chat_id=$1, profile_message_id=$2, updated_at=NOW()
+            WHERE user_id=$3
+            """,
+            chat_id,
+            message_id,
+            user_id,
+        )
+
+
+async def list_user_purchases(user_id: int):
+    async with pool.acquire() as con:
+        return await con.fetch(
+            """
+            SELECT product_title, link, created_at
+            FROM purchases
+            WHERE user_id=$1
+            ORDER BY created_at DESC
+            LIMIT 20
+            """,
+            user_id,
+        )
+
+
+async def get_product(code: str):
+    async with pool.acquire() as con:
+        return await con.fetchrow(
+            """
+            SELECT code, title, price, link, is_active
+            FROM products
+            WHERE code=$1
+            """,
+            code,
+        )
+
+
+async def make_purchase(user_id: int, product_code: str):
+    async with pool.acquire() as con:
+        async with con.transaction():
+            user = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", user_id)
+            if not user:
+                return False, "Пользователь не найден."
+
+            product = await con.fetchrow(
+                """
+                SELECT code, title, price, link, is_active
+                FROM products
+                WHERE code=$1
+                """,
+                product_code,
+            )
+            if not product or not product["is_active"]:
+                return False, "Товар недоступен."
+
+            balance = Decimal(str(user["balance"]))
+            price = Decimal(str(product["price"]))
+            if balance < price:
+                return False, "Недостаточно средств."
+
+            await con.execute(
+                """
+                UPDATE users
+                SET balance = balance - $1,
+                    orders_count = orders_count + 1,
+                    updated_at = NOW()
+                WHERE user_id=$2
+                """,
+                price,
+                user_id,
+            )
+            await con.execute(
+                """
+                INSERT INTO purchases(user_id, product_code, product_title, price, link)
+                VALUES($1,$2,$3,$4,$5)
+                """,
+                user_id,
+                product["code"],
+                product["title"],
+                price,
+                product["link"],
+            )
+
+            return True, product["link"]
+
+
+async def add_or_update_promo(code: str, amount: Decimal, uses_left: int) -> None:
+    code = normalize_promo_code(code)
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            INSERT INTO promo_codes(code, amount, uses_left, is_active)
+            VALUES($1,$2,$3,TRUE)
+            ON CONFLICT (code) DO UPDATE
+            SET amount=EXCLUDED.amount,
+                uses_left=EXCLUDED.uses_left,
+                is_active=TRUE
+            """,
+            code,
+            amount,
+            uses_left,
+        )
+
+
+async def apply_promo(user_id: int, code: str):
+    code = normalize_promo_code(code)
+    async with pool.acquire() as con:
+        async with con.transaction():
+            promo = await con.fetchrow(
+                """
+                SELECT code, amount, uses_left, is_active
+                FROM promo_codes
+                WHERE code=$1
+                FOR UPDATE
+                """,
+                code,
+            )
+
+            if not promo or not promo["is_active"] or promo["uses_left"] <= 0:
+                await con.execute("UPDATE users SET awaiting_promo=FALSE, updated_at=NOW() WHERE user_id=$1", user_id)
+                return False, "❌ Промокод недействителен"
+
+            used = await con.fetchrow(
+                "SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2",
+                user_id,
+                code,
+            )
+            if used:
+                await con.execute("UPDATE users SET awaiting_promo=FALSE, updated_at=NOW() WHERE user_id=$1", user_id)
+                return False, "❌ Промокод недействителен"
+
+            amount = Decimal(str(promo["amount"]))
+
+            await con.execute(
+                "INSERT INTO promo_activations(user_id, code, amount) VALUES($1,$2,$3)",
+                user_id,
+                code,
+                amount,
+            )
+            await con.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=$1", code)
+            await con.execute(
+                """
+                UPDATE users
+                SET balance = balance + $1,
+                    awaiting_promo = FALSE,
+                    updated_at = NOW()
+                WHERE user_id=$2
+                """,
+                amount,
+                user_id,
+            )
+
+            return True, f"✅ Промокод АКТИВИРОВАН! +{amount:.2f}"
+
+
+def kb_main_reply():
+    b = ReplyKeyboardBuilder()
+    b.button(text="ГЛАВНАЯ ⚪")
+    b.button(text="ПРОФИЛЬ 👤")
+    b.button(text="ПОМОЩЬ 💬")
+    b.button(text="РАБОТА 🛠️")
+    b.adjust(2, 2)
+    return b.as_markup(resize_keyboard=True)
+
+
+def kb_main_inline():
+    b = InlineKeyboardBuilder()
+    b.button(text="Одесса ⚓", callback_data="city:odessa")
+    return b.as_markup()
+
+
+def kb_odessa_items():
+    b = InlineKeyboardBuilder()
+    for p in PRODUCTS:
+        b.button(text=p["button"], callback_data=f"product:{p['code']}")
+    b.adjust(1, 1, 1)
+    return b.as_markup()
+
+
+def kb_product_card(code: str):
+    b = InlineKeyboardBuilder()
+    b.button(text="✅ Купить", callback_data=f"buy:{code}")
+    b.button(text="⬅️ Назад", callback_data="city:odessa")
+    b.adjust(1, 1)
+    return b.as_markup()
+
+
+def kb_profile_inline():
+    b = InlineKeyboardBuilder()
+    b.button(text="💳 Пополнить баланс", callback_data="profile:topup")
+    b.button(text="🎟 Активировать промокод", callback_data="profile:promo")
+    b.button(text="🧾 История покупок", callback_data="profile:history")
+    b.adjust(1, 1, 1)
+    return b.as_markup()
+
+
+def render_main_text(balance: Decimal, orders: int) -> str:
+    return (
+        "✋🏻 Здравствуй! Кавалер 🎩\n"
+        "👑Вы находитесь в Cavalier Shop👑\n\n"
+        "✍🏻Кратко о нашем сервисе\n\n"
+        "°Готовые позиции\n"
+        "°Горячие позиции\n"
+        "°Превосходное качество товара\n"
+        "°ОПТ\n"
+        "°Разновидные способы оплаты \n"
+        "°Отправки NovaPost 🇺🇦 \n"
+        "°Оператор/Сапорт в сети 24/7 \n\n"
+        "Актуальные ссылки \n\n"
+        "Бот : \n"
+        "@CavalierShopBot\n\n"
+        "💬Чат : \n"
+        "https://t.me/+HvuVKZkR2-03MzBi\n\n"
+        "🥇Отзывы :\n"
+        "https://t.me/+HvuVKZkR2-03MzBi\n\n"
+        "Оператор/Сапорт : \n"
+        "@gskalye\n\n"
+        f"🏦Баланс : <b>{balance:.2f}</b>\n"
+        f"🛍️Количество заказов : <b>{orders}</b>"
+    )
+
+
+def render_profile_text(balance: Decimal, orders: int) -> str:
+    return (
+        f"Баланс : <b>{balance:.2f}</b>\n\n"
+        f"Количество заказов : <b>{orders}</b>"
+    )
+
+
+async def refresh_saved_views(bot: Bot, user_id: int) -> None:
+    user = await get_user(user_id)
+    if not user:
+        return
+
+    balance = Decimal(str(user["balance"]))
+    orders = int(user["orders_count"])
+
+    if user["main_chat_id"] and user["main_message_id"]:
+        try:
+            await bot.edit_message_text(
+                chat_id=user["main_chat_id"],
+                message_id=user["main_message_id"],
+                text=render_main_text(balance, orders),
+                reply_markup=kb_main_inline(),
+                parse_mode="HTML",
+                disable_web_page_preview=True,
+            )
+        except Exception:
+            pass
+
+    if user["profile_chat_id"] and user["profile_message_id"]:
+        try:
+            await bot.edit_message_text(
+                chat_id=user["profile_chat_id"],
+                message_id=user["profile_message_id"],
+                text=render_profile_text(balance, orders),
+                reply_markup=kb_profile_inline(),
+                parse_mode="HTML",
+                disable_web_page_preview=True,
+            )
+        except Exception:
+            pass
+
+
+dp = Dispatcher()
+
+
+@dp.message(CommandStart())
+async def start_cmd(message: Message):
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    balance = Decimal(str(user["balance"])) if user else Decimal("0")
+    orders = int(user["orders_count"]) if user else 0
+
+    await message.answer(
+        render_main_text(balance, orders),
+        parse_mode="HTML",
+        disable_web_page_preview=True,
+        reply_markup=kb_main_reply(),
+    )
+
+
+@dp.message(F.text == "ГЛАВНАЯ ⚪")
+async def main_menu(message: Message):
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    balance = Decimal(str(user["balance"])) if user else Decimal("0")
+    orders = int(user["orders_count"]) if user else 0
+
+    sent = await message.answer(
+        render_main_text(balance, orders),
+        parse_mode="HTML",
+        disable_web_page_preview=True,
+        reply_markup=kb_main_inline(),
+    )
+    await save_main_message(message.from_user.id, sent.chat.id, sent.message_id)
+
+
+@dp.message(F.text == "ПРОФИЛЬ 👤")
+async def profile_menu(message: Message):
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    balance = Decimal(str(user["balance"])) if user else Decimal("0")
+    orders = int(user["orders_count"]) if user else 0
+
+    sent = await message.answer(
+        render_profile_text(balance, orders),
+        parse_mode="HTML",
+        disable_web_page_preview=True,
+        reply_markup=kb_profile_inline(),
+    )
+    await save_profile_message(message.from_user.id, sent.chat.id, sent.message_id)
+
+
+@dp.message(F.text == "ПОМОЩЬ 💬")
+async def help_menu(message: Message):
+    await message.answer("Оператор/Сапорт :\n@gskalye", reply_markup=kb_main_reply())
+
+
+@dp.message(F.text == "РАБОТА 🛠️")
+async def work_menu(message: Message):
+    await message.answer("Раздел в разработке.", reply_markup=kb_main_reply())
+
+
+@dp.callback_query(F.data == "city:odessa")
+async def city_odessa(call: CallbackQuery):
+    await call.message.answer("✅ Вы выбрали город Одесса. Выберите товар:", reply_markup=kb_odessa_items())
+    await call.answer()
+
+
+@dp.callback_query(F.data.startswith("product:"))
+async def product_card(call: CallbackQuery):
+    code = call.data.split(":", 1)[1]
+    p = await get_product(code)
+    if not p or not p["is_active"]:
+        await call.message.answer("❌ Товар недоступен.")
+        await call.answer()
+        return
+
+    text = (
+        f"📦 <b>{p['title']}</b>\n"
+        f"💵 Цена: <b>{Decimal(str(p['price'])):.2f}</b>\n"
+        "📝 Описание: готово к выдаче."
+    )
+    await call.message.answer(text, parse_mode="HTML", reply_markup=kb_product_card(code))
+    await call.answer()
+
+
+@dp.callback_query(F.data.startswith("buy:"))
+async def buy_item(call: CallbackQuery):
+    await ensure_user(call.from_user.id)
+    code = call.data.split(":", 1)[1]
+    ok, result = await make_purchase(call.from_user.id, code)
+    if not ok:
+        await call.message.answer(f"❌ {result}")
+        await call.answer()
+        return
+
+    await call.message.answer(f"✅ Покупка успешна\nСсылка: {result}")
+    await refresh_saved_views(call.bot, call.from_user.id)
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:topup")
+async def profile_topup(call: CallbackQuery):
+    # Место под будущую интеграцию легальной платежной системы (payments table + provider API).
+    await call.message.answer("💳 Пополнение в разработке")
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:promo")
+async def profile_promo(call: CallbackQuery):
+    await ensure_user(call.from_user.id)
+    await set_awaiting_promo(call.from_user.id, True)
+    await call.message.answer("🎟 Введите промокод одним сообщением:")
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:history")
+async def profile_history(call: CallbackQuery):
+    await ensure_user(call.from_user.id)
+    rows = await list_user_purchases(call.from_user.id)
+    if not rows:
+        await call.message.answer("🧾 История покупок пуста.")
+        await call.answer()
+        return
+
+    lines = []
+    for r in rows:
+        lines.append(f"• {r['product_title']} — {Decimal(str(r['price'])):.2f}\n{r['link']}")
+    await call.message.answer("🧾 История покупок:\n\n" + "\n\n".join(lines), disable_web_page_preview=True)
+    await call.answer()
+
+
+@dp.message(Command("addpromo"))
+async def cmd_addpromo(message: Message):
+    allow = (not ADMIN_IDS) or (message.from_user.id in ADMIN_IDS)
+    if not allow:
+        await message.answer("Нет доступа.")
+        return
+
+    parts = (message.text or "").strip().split()
+    if len(parts) < 4:
+        await message.answer("Формат: /addpromo CODE AMOUNT USES")
+        return
+
+    code = normalize_promo_code(parts[1])
+    try:
+        amount = Decimal(parts[2].replace(",", "."))
+        uses = int(parts[3])
+        if amount <= 0 or uses < 1:
+            raise ValueError
+    except (InvalidOperation, ValueError):
+        await message.answer("Неверные данные. Пример: /addpromo TEST300 300 1")
+        return
+
+    await add_or_update_promo(code, amount, uses)
+    await message.answer(f"✅ Промокод создан: {code} (+{amount:.2f}, uses={uses})")
+
+
+@dp.message(F.text)
+async def promo_input_handler(message: Message):
+    if message.text.startswith("/"):
+        return
+
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    if not user or not user["awaiting_promo"]:
+        return
+
+    code = normalize_promo_code(message.text)
+    ok, txt = await apply_promo(message.from_user.id, code)
+    await message.answer(txt)
+    if ok:
+        await refresh_saved_views(message.bot, message.from_user.id)
+
+
+async def main() -> None:
+    require_env()
+    await db_init()
+
+    bot = Bot(BOT_TOKEN)
+    await bot.delete_webhook(drop_pending_updates=True)
+    logger.info("Bot started (long polling)")
+    await dp.start_polling(bot)
+
+
+if __name__ == "__main__":
+    asyncio.run(main())
