diff --git a/bot.py b/bot.py
index 693556cd93ac92a38cb35e252fb0137b2c57e7cf..9f6fd1b01c03f71283dc720f85dde338cde373ea 100644
--- a/bot.py
+++ b/bot.py
@@ -1,434 +1,470 @@
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
+from typing import List, Optional, Tuple
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
+# Админ(ы): через запятую, напр: "123,456"
+ADMIN_IDS = set()
+_raw_admins = os.getenv("ADMIN_IDS", "").strip()
+if _raw_admins:
+    for value in _raw_admins.split(","):
+        value = value.strip()
+        if value.isdigit():
+            ADMIN_IDS.add(int(value))
+
+pool: Optional[asyncpg.Pool] = None
+
+# ---- ВИТРИНА (3 кнопки под “Одесса”) ----
+# Меняешь тут легко: (код, отображаемое_имя)
+ODESSA_ITEMS: List[Tuple[str, str]] = [
+    ("saint", "1) saint"),
+    ("big_bob", "2) big bob"),
+    ("shenen", "3) shenen"),
+]
+
+# Эти “коды” должны существовать в таблице products.code (мы автосоздадим демо при запуске)
+DEMO_PRODUCTS = [
+    # code, title, price, link_to_deliver
+    ("saint", "saint", Decimal("10.00"), "https://example.com/saint"),
+    ("big_bob", "big bob", Decimal("15.00"), "https://example.com/big-bob"),
+    ("shenen", "shenen", Decimal("20.00"), "https://example.com/shenen"),
+]
+
+
+def _require_env() -> None:
+    if not BOT_TOKEN:
+        raise RuntimeError("BOT_TOKEN is missing")
+    if not DATABASE_URL:
+        raise RuntimeError("DATABASE_URL is missing (set Postgres -> DATABASE_URL in Railway Variables)")
+
+
+# ----------------- DB -----------------
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
+                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
+            );
+            """
+        )
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
+        # Миграция для старых БД: добиваем отсутствующие поля и ограничения.
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS title TEXT")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS price NUMERIC(12,2)")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS link TEXT")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN")
+        await con.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ")
+
+        await con.execute("UPDATE products SET title = code WHERE title IS NULL")
+        await con.execute("UPDATE products SET price = 0 WHERE price IS NULL")
+        await con.execute("UPDATE products SET link = '' WHERE link IS NULL")
+        await con.execute("UPDATE products SET is_active = TRUE WHERE is_active IS NULL")
+        await con.execute("UPDATE products SET created_at = NOW() WHERE created_at IS NULL")
+
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
+        for code, title, price, link in DEMO_PRODUCTS:
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
+                code,
+                title,
+                price,
+                link,
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
+        return await con.fetchrow("SELECT user_id, balance, orders_count FROM users WHERE user_id=$1", user_id)
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
+            LIMIT 50
+            """,
+            user_id,
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
+            price = Decimal(str(product["price"]))
+            bal = Decimal(str(user["balance"]))
+            if bal < price:
+                return False, "Недостаточно средств."
+
+            await con.execute(
+                "UPDATE users SET balance = balance - $1, orders_count = orders_count + 1 WHERE user_id=$2",
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
+async def add_promo(code: str, amount: Decimal, uses_left: int, is_active: bool = True) -> None:
+    async with pool.acquire() as con:
+        await con.execute(
+            """
+            INSERT INTO promo_codes(code, amount, uses_left, is_active)
+            VALUES($1,$2,$3,$4)
+            ON CONFLICT (code) DO UPDATE
+            SET amount=EXCLUDED.amount, uses_left=EXCLUDED.uses_left, is_active=EXCLUDED.is_active
+            """,
+            code,
+            amount,
+            uses_left,
+            is_active,
+        )
+
+
+async def apply_promo(user_id: int, code: str):
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
+            if not promo or not promo["is_active"]:
+                return False, "❌ Промокод недействителен или уже использован."
+
+            if promo["uses_left"] <= 0:
+                return False, "❌ Промокод недействителен или уже использован."
+
+            used = await con.fetchrow("SELECT 1 FROM promo_activations WHERE user_id=$1 AND code=$2", user_id, code)
+            if used:
+                return False, "❌ Промокод недействителен или уже использован."
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
+            await con.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)
+
+            return True, f"✅ Промокод активирован: +{amount:.2f}"
+
+
+# ----------------- UI (кнопки) -----------------
+def kb_main_reply():
+    b = ReplyKeyboardBuilder()
+    b.button(text="ГЛАВНАЯ 🏠")
+    b.button(text="ПРОФИЛЬ 👤")
+    b.button(text="ПОМОЩЬ 💬")
+    b.button(text="РАБОТА 🧳")
+    b.adjust(2, 2)
+    return b.as_markup(resize_keyboard=True)
+
+
+def kb_main_inline():
+    b = InlineKeyboardBuilder()
+    b.button(text="Одесса ⚓️", callback_data="city:odessa")
+    return b.as_markup()
+
+
+def kb_odessa_items():
+    b = InlineKeyboardBuilder()
+    for code, title in ODESSA_ITEMS:
+        b.button(text=title, callback_data=f"buy:{code}")
+    b.adjust(1, 1, 1)
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
+# ----------------- Handlers -----------------
+dp = Dispatcher()
+
+
+@dp.message(CommandStart())
+async def start_cmd(message: Message):
+    await ensure_user(message.from_user.id)
+    await message.answer("Добро пожаловать.", reply_markup=kb_main_reply())
+
+
+@dp.message(F.text == "ГЛАВНАЯ 🏠")
+async def main_menu(message: Message):
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    bal = Decimal(str(user["balance"])) if user else Decimal("0")
+    orders = int(user["orders_count"]) if user else 0
+
+    text = (
+        "Актуальные ссылки\n\n"
+        "Оператор/Сапорт: @gskalye\n\n"
+        f"💵 Баланс: {bal:.2f}\n"
+        f"🛍 Количество заказов: {orders}\n"
+    )
+    await message.answer(text, reply_markup=kb_main_inline())
+
+
+@dp.message(F.text == "ПРОФИЛЬ 👤")
+async def profile_menu(message: Message):
+    await ensure_user(message.from_user.id)
+    user = await get_user(message.from_user.id)
+    bal = Decimal(str(user["balance"])) if user else Decimal("0")
+    orders = int(user["orders_count"]) if user else 0
+
+    text = (
+        "👤 Профиль\n\n"
+        f"💵 Баланс: {bal:.2f}\n"
+        f"🛍 Количество заказов: {orders}\n"
+    )
+    await message.answer(text, reply_markup=kb_profile_inline())
+
+
+@dp.message(F.text == "ПОМОЩЬ 💬")
+async def help_menu(message: Message):
+    await message.answer("Поддержка: @gskalye", reply_markup=kb_main_reply())
+
+
+@dp.message(F.text == "РАБОТА 🧳")
+async def work_menu(message: Message):
+    await message.answer("Раздел в разработке.", reply_markup=kb_main_reply())
+
+
+@dp.callback_query(F.data == "city:odessa")
+async def city_odessa(call: CallbackQuery):
+    await call.message.answer("Вы выбрали город Одесса. Выберите товар:", reply_markup=kb_odessa_items())
+    await call.answer()
+
+
+@dp.callback_query(F.data.startswith("buy:"))
+async def buy_item(call: CallbackQuery):
+    await ensure_user(call.from_user.id)
+    code = call.data.split(":", 1)[1]
+
+    ok, result = await make_purchase(call.from_user.id, code)
+    if not ok:
+        await call.message.answer(f"❌ {result}")
+        await call.answer()
+        return
+
+    await call.message.answer(f"✅ Покупка успешна.\nВаша ссылка: {result}")
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:topup")
+async def profile_topup(call: CallbackQuery):
+    await call.message.answer("Пополнение в разработке.")
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:promo")
+async def profile_promo(call: CallbackQuery):
+    await call.message.answer("Введите промокод командой:\n/promo ВАШ_ПРОМОКОД")
+    await call.answer()
+
+
+@dp.callback_query(F.data == "profile:history")
+async def profile_history(call: CallbackQuery):
+    await ensure_user(call.from_user.id)
+    rows = await list_user_purchases(call.from_user.id)
+    if not rows:
+        await call.message.answer("История пуста.")
+        await call.answer()
+        return
+
+    text = "🧾 История покупок:\n\n"
+    for row in rows:
+        text += f"• {row['product_title']}\n{row['link']}\n\n"
+    await call.message.answer(text)
+    await call.answer()
+
+
+@dp.message(Command("promo"))
+async def cmd_promo(message: Message):
+    await ensure_user(message.from_user.id)
+
+    parts = message.text.strip().split(maxsplit=1)
+    if len(parts) < 2:
+        await message.answer("Формат: /promo ВАШ_ПРОМОКОД")
+        return
+
+    code = parts[1].strip()
+    ok, txt = await apply_promo(message.from_user.id, code)
+    await message.answer(txt if ok else txt)
+
+
+@dp.message(Command("addpromo"))
+async def cmd_addpromo(message: Message):
+    if message.from_user.id not in ADMIN_IDS:
+        await message.answer("Нет доступа.")
+        return
+
+    parts = message.text.strip().split()
+    # /addpromo CODE AMOUNT USES
+    if len(parts) < 4:
+        await message.answer("Формат: /addpromo CODE AMOUNT USES")
+        return
+
+    code = parts[1].strip()
+    try:
+        amount = Decimal(parts[2].replace(",", "."))
+        uses = int(parts[3])
+        if uses < 1:
+            raise ValueError
+    except (InvalidOperation, ValueError):
+        await message.answer("Неверные данные. Пример: /addpromo TEST300 300 1")
+        return
+
+    await add_promo(code, amount, uses, True)
+    await message.answer(f"✅ Промокод создан: {code} (+{amount:.2f}, uses={uses})")
+
+
+async def main() -> None:
+    _require_env()
+    await db_init()
+    bot = Bot(BOT_TOKEN)
+    logger.info("Bot started")
+    await dp.start_polling(bot)
+
+
+if __name__ == "__main__":
+    asyncio.run(main())
