import asyncio
import csv
import io
import logging
import math
import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    URLInputFile,
    FSInputFile,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InputMediaPhoto,
    LabeledPrice,
    PreCheckoutQuery,
    SwitchInlineQueryChosenChat,
)


# =============================================================================
# КОНФИГ
# =============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8979440402:AAEtpoD_e_jNHHxrECIMU3iOdyl8FhXucTY")
DB_PATH = "proxy_bot.db"

START_PHOTO_URL = "https://i.postimg.cc/ryDJ6qGS/izobrazenie-2026-09-27-160047764.png"
PROFILE_PHOTO_URL = "https://i.postimg.cc/cCDQwnZQ/izobrazenie-2026-09-27-142459893.png"
BUY_PROXY_PHOTO_URL = "https://i.postimg.cc/506wMmCB/izobrazenie-2026-09-27-160144401.png"
MANAGE_PROXY_PHOTO_URL = "https://i.postimg.cc/wBBNhk0D/izobrazenie-2026-09-27-160239365.png"
TOPUP_BALANCE_PHOTO_URL = "https://i.postimg.cc/FFSLgzgS/izobrazenie-2026-09-27-160336052.png"
PROMO_PHOTO_URL = "https://i.postimg.cc/J0BTq87P/izobrazenie-2026-09-27-165927877.png"
FREE_TRIAL_PHOTO_URL = "https://i.postimg.cc/zv5jngMZ/izobrazenie-2026-09-27-170025325.png"
HELP_PHOTO_URL = "https://i.postimg.cc/c4tHQbRf/izobrazenie-2026-09-27-170155781.png"

# ID владельца бота — всегда админ, независимо от таблицы admins в БД
OWNER_ID = 1857112360

# Доля реферала от пополнений приглашённых (0.35 = 35%)
REF_PERCENT = 0.35

# Сколько дней подписки получает пригласивший, когда по его ссылке пришёл новый друг
REF_BONUS_DAYS = 3

# Пополнение за Telegram Stars: сколько рублей на баланс даёт 1 звезда. Поменяйте под себя.
STAR_RUB_RATE = 1.0
STARS_MAX = 10_000  # лимит Telegram на один счёт в звёздах

# Тарифы: ключ -> параметры. Цена в ₽ за PLAN_DAYS дней.
PLAN_DAYS = 30
PLANS = {
    "plus": {
        "title": "Plus", "price": 69, "devices": 2,
        "specs": "200 GB · 20 Mbit/s · 2 устр.",
        "emoji": "5258012149036365477", "fallback": "🔹",
    },
    "pro": {
        "title": "Pro", "price": 119, "devices": 3,
        "specs": "Безлимит · 50 Mbit/s · 3 устр.",
        "emoji": "5255813559572508065", "fallback": "💎",
    },
    "max": {
        "title": "Max", "price": 199, "devices": 5,
        "specs": "Безлимит · 100 Mbit/s · 5 устр.",
        "emoji": "5271934788037517525", "fallback": "♾",
    },
}

# Текст, который друг получает при нажатии «Поделиться с другом» (отправляется жирным)
SHARE_TEXT = "🔥 3 дня БЕСПЛАТНОГО пользования — подключайся сейчас и не жди!"

# ID кастомных эмодзи для кнопок оплаты
PAY_ICON = "5195072744798051557"
CHECK_ICON = "5267045723685264285"

# =============================================================================
# ПЛАТЁЖНЫЕ ПРОВАЙДЕРЫ: Crypto Bot, xRocket, ЮKassa (СБП / карта)
# Ключи лучше хранить в переменных окружения, а не в коде.
# =============================================================================

MIN_TOPUP = 5.0
MAX_TOPUP = 100_000.0

CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "616219:AAowlz9gOg6wdfdxt5NcaxlxAqhz5vCVg8J")
XROCKET_TOKEN = os.getenv("XROCKET_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiIweDAiLCJ1c2VySWQiOiIxMDg4MTgwNSIsImp0aSI6InVzZXI6MTA4ODE4MDU6NDhiNTIwYjQtYjdiYy00MmI3LTliNzItYzVkNjg4N2VhNThjIiwidHlwZSI6ImV4Y2hhbmdlIiwicGxhdGZvcm0iOiJleGNoYW5nZV9hcGkiLCJpYXQiOjE3OTA4NjE0MDl9.9HuU8ThMmk3joJ3l9FShMcnhbFlSdCaBl3y6rtmKXcc")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1370481")  # ID магазина из кабинета ЮKassa
YOOKASSA_SECRET = os.getenv("YOOKASSA_SECRET", "live_rBz_baWL6_ixr3Bg81i3cCgJEbfqTeKNph-nF_bKPe0")  # live_...

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class PaymentError(Exception):
    pass


async def _request(method: str, url: str, **kwargs) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.request(method, url, **kwargs) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                if resp.status >= 400:
                    raise PaymentError(f"{url} -> {resp.status}: {data}")
                return data
    except aiohttp.ClientError as exc:
        raise PaymentError(str(exc)) from exc


# --- Crypto Bot ---------------------------------------------------------------

_CB = "https://pay.crypt.bot/api"


def _cb_headers() -> dict:
    return {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}


async def _usdt_rub_rate() -> float:
    """Курс USDT→RUB из Crypto Bot (нужен для xRocket, где счета только в крипте)."""
    data = await _request("GET", f"{_CB}/getExchangeRates", headers=_cb_headers())
    for item in data.get("result", []):
        if item.get("source") == "USDT" and item.get("target") == "RUB" and item.get("is_valid"):
            return float(item["rate"])
    raise PaymentError("Не удалось получить курс USDT/RUB")


async def _cryptobot_create(amount: float, **_) -> tuple[str, str]:
    data = await _request(
        "POST",
        f"{_CB}/createInvoice",
        headers=_cb_headers(),
        json={
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{amount:.2f}",
            "description": "Пополнение баланса",
            "expires_in": 3600,
        },
    )
    if not data.get("ok"):
        raise PaymentError(str(data))
    res = data["result"]
    return str(res["invoice_id"]), res.get("bot_invoice_url") or res["pay_url"]


async def _cryptobot_paid(ext_id: str) -> bool:
    data = await _request(
        "GET", f"{_CB}/getInvoices", headers=_cb_headers(), params={"invoice_ids": ext_id}
    )
    result = data.get("result", [])
    items = result.get("items", []) if isinstance(result, dict) else result
    return bool(items) and items[0].get("status") == "paid"


# --- xRocket ------------------------------------------------------------------

_XR = "https://pay.xrocket.tg"


def _xr_headers() -> dict:
    return {"Rocket-Pay-Key": XROCKET_TOKEN}


async def _xrocket_create(amount: float, **_) -> tuple[str, str]:
    rate = await _usdt_rub_rate()
    usdt = math.ceil(amount / rate * 100) / 100  # округляем вверх до цента
    data = await _request(
        "POST",
        f"{_XR}/tg-invoices",
        headers=_xr_headers(),
        json={
            "amount": usdt,
            "currency": "USDT",
            "numPayments": 1,
            "description": "Пополнение баланса",
            "expiredIn": 3600,
        },
    )
    if not data.get("success"):
        raise PaymentError(str(data))
    res = data["data"]
    return str(res["id"]), res["link"]


async def _xrocket_paid(ext_id: str) -> bool:
    data = await _request("GET", f"{_XR}/tg-invoices/{ext_id}", headers=_xr_headers())
    return data.get("data", {}).get("status") == "paid"


# --- ЮKassa (СБП и карта) -----------------------------------------------------

_YK = "https://api.yookassa.ru/v3/payments"


def _yk_auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET)


async def _yookassa_create(amount: float, pm_type: str, return_url: str) -> tuple[str, str]:
    data = await _request(
        "POST",
        _YK,
        auth=_yk_auth(),
        headers={"Idempotence-Key": str(uuid.uuid4())},
        json={
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": "Пополнение баланса",
            "payment_method_data": {"type": pm_type},  # "sbp" или "bank_card"
        },
    )
    return data["id"], data["confirmation"]["confirmation_url"]


async def _yookassa_paid(ext_id: str) -> bool:
    data = await _request("GET", f"{_YK}/{ext_id}", auth=_yk_auth())
    return data.get("status") == "succeeded"


# --- Единый интерфейс ---------------------------------------------------------

PAY_TITLES = {
    "stars": "Stars",
    "cryptobot": "Crypto Bot",
    "yk_sbp": "ЮKassa (СБП)",
    "yk_card": "Карта (руб.)",
    "xrocket": "xRocket",
}


async def create_payment(method: str, amount: float, return_url: str) -> tuple[str, str]:
    """Создаёт счёт. Возвращает (id счёта у провайдера, ссылка на оплату)."""
    if method == "cryptobot":
        return await _cryptobot_create(amount)
    if method == "xrocket":
        return await _xrocket_create(amount)
    if method == "yk_sbp":
        return await _yookassa_create(amount, "sbp", return_url)
    if method == "yk_card":
        return await _yookassa_create(amount, "bank_card", return_url)
    raise PaymentError(f"Неизвестный способ оплаты: {method}")


async def is_paid(method: str, ext_id: str) -> bool:
    if method == "cryptobot":
        return await _cryptobot_paid(ext_id)
    if method == "xrocket":
        return await _xrocket_paid(ext_id)
    if method in ("yk_sbp", "yk_card"):
        return await _yookassa_paid(ext_id)
    raise PaymentError(f"Неизвестный способ оплаты: {method}")

# =============================================================================
# БАЗА ДАННЫХ
# =============================================================================


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                subscription_active INTEGER NOT NULL DEFAULT 0,
                subscription_until TEXT,
                balance REAL NOT NULL DEFAULT 0,
                ref_balance REAL NOT NULL DEFAULT 0,
                devices_limit INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                bonus_amount REAL NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'admin',
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                ext_id TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                plan TEXT
            )
            """
        )
        try:  # миграция для баз, созданных до появления тарифов
            await db.execute("ALTER TABLE invoices ADD COLUMN plan TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def get_or_create_user(
    user_id: int, username: str | None, referrer_id: int | None = None
) -> dict:
    """Возвращает данные пользователя, создавая запись при первом обращении.

    referrer_id учитывается только при СОЗДАНИИ нового пользователя (из /start ref_<id>)
    и никогда не перезаписывается у уже существующих пользователей.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

        if row is None:
            await db.execute(
                "INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)",
                (user_id, username, referrer_id),
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()

        return dict(row)


async def count_referrals(user_id: int) -> int:
    """Считает, сколько пользователей пришло по реферальной ссылке этого user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)
        )
        (count,) = await cursor.fetchone()
        return count


async def update_user(user_id: int, **fields) -> None:
    """Обновляет произвольные поля пользователя. Пример: update_user(1, balance=10)."""
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {columns} WHERE user_id = ?", values)
        await db.commit()


async def adjust_balance(user_id: int, delta: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id)
        )
        await db.commit()


async def adjust_ref_balance(user_id: int, delta: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET ref_balance = ref_balance + ? WHERE user_id = ?", (delta, user_id)
        )
        await db.commit()


async def set_subscription(user_id: int, days: int) -> str | None:
    """days > 0 — активировать на N дней, days == 0 — деактивировать. Возвращает дату окончания."""
    if days > 0:
        until = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y в %H:%M")
        await update_user(user_id, subscription_active=1, subscription_until=until)
        return until
    await update_user(user_id, subscription_active=0, subscription_until=None)
    return None


async def user_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None


async def extend_subscription(user_id: int, days: int) -> str:
    """Продлевает подписку на N дней (от даты окончания, если подписка ещё активна)."""
    user = await get_or_create_user(user_id, None)
    start = datetime.now()
    if user["subscription_active"] and user.get("subscription_until"):
        try:
            current = datetime.strptime(user["subscription_until"], "%d.%m.%Y в %H:%M")
            if current > start:
                start = current
        except ValueError:
            pass
    until = (start + timedelta(days=days)).strftime("%d.%m.%Y в %H:%M")
    await update_user(user_id, subscription_active=1, subscription_until=until)
    return until


async def spend_balance(user_id: int, field: str, amount: float) -> bool:
    """Атомарно списывает с balance / ref_balance. False, если не хватает средств."""
    assert field in ("balance", "ref_balance")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE users SET {field} = {field} - ? WHERE user_id = ? AND {field} >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cursor.rowcount > 0


async def activate_plan(user_id: int, plan_key: str) -> str:
    """Продлевает подписку на PLAN_DAYS дней и выставляет лимит устройств тарифа."""
    until = await extend_subscription(user_id, PLAN_DAYS)
    await update_user(user_id, devices_limit=PLANS[plan_key]["devices"])
    return until


# --- Роли админов -------------------------------------------------------------

async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None


async def add_admin(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, role) VALUES (?, 'admin')", (user_id,)
        )
        await db.commit()


async def remove_admin(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_admins() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM admins ORDER BY added_at")
        return [dict(row) for row in await cursor.fetchall()]


# --- Промокоды ------------------------------------------------------------

async def create_promo(code: str, bonus_amount: float, max_uses: int) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO promo_codes (code, bonus_amount, max_uses) VALUES (?, ?, ?)",
                (code, bonus_amount, max_uses),
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def delete_promo(code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
        await db.commit()
        return cursor.rowcount > 0


async def get_promo(code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def increment_promo_use(code: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", (code,)
        )
        await db.commit()


async def list_promos() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
        return [dict(row) for row in await cursor.fetchall()]


# --- Пополнения и покупки ---------------------------------------------------

async def add_topup(user_id: int, amount: float, method: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO topups (user_id, amount, method) VALUES (?, ?, ?)",
            (user_id, amount, method),
        )
        await db.commit()


async def list_topups(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM topups ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]


async def add_purchase(user_id: int, item: str, amount: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO purchases (user_id, item, amount) VALUES (?, ?, ?)",
            (user_id, item, amount),
        )
        await db.commit()


async def list_purchases(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM purchases ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]


# --- Счета на оплату (платёжные системы) ------------------------------------

async def add_invoice(
    user_id: int, method: str, ext_id: str, amount: float, plan: str | None = None
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO invoices (user_id, method, ext_id, amount, plan) VALUES (?, ?, ?, ?, ?)",
            (user_id, method, ext_id, amount, plan),
        )
        await db.commit()
        return cursor.lastrowid


async def get_invoice(invoice_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def claim_invoice(invoice_id: int) -> bool:
    """Атомарно помечает счёт оплаченным.

    True получит только первый вызов — защита от двойного зачисления.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE invoices SET status = 'paid' WHERE id = ? AND status = 'pending'",
            (invoice_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def set_invoice_ext(invoice_id: int, ext_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE invoices SET ext_id = ? WHERE id = ?", (ext_id, invoice_id))
        await db.commit()


# --- Пользователи (для списка/экспорта/рассылки) ----------------------------

async def list_users(limit: int = 10, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [dict(row) for row in await cursor.fetchall()]


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        (count,) = await cursor.fetchone()
        return count


async def all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users")
        return [row[0] for row in await cursor.fetchall()]


# =============================================================================
# ТЕКСТЫ
# Кастомные эмодзи вставлены через <tg-emoji emoji-id="ID">запасной_эмодзи</tg-emoji>.
# Запасной эмодзи показывается тем, у кого нет Telegram Premium. Требуется parse_mode HTML.
# =============================================================================


def subscription_note(user: dict) -> str:
    """Цитируемый блок про статус подписки — используется и на /start, и в Профиле."""
    if user["subscription_active"]:
        return (
            '<blockquote><tg-emoji emoji-id="5197288647275071607">✅</tg-emoji> '
            'Подписка на VPN оформлена и активна — пользуйтесь всеми возможностями сервиса.</blockquote>'
        )
    return (
        '<blockquote><tg-emoji emoji-id="5251281810729480172">⚠️</tg-emoji> '
        'Подписка неактивна — но это легко исправить! Выберите подходящий тариф и начните '
        'пользоваться PROXY уже сейчас</blockquote>'
    )


def start_caption(user: dict) -> str:
    subscription = "активирована" if user["subscription_active"] else "неактивна"
    text = (
        f'<tg-emoji emoji-id="5271604874419647061">⭐</tg-emoji> Подписка: {subscription}\n\n'
        f'┌ <tg-emoji emoji-id="5346024704765347251">🆔</tg-emoji> ID: <code>{user["user_id"]}</code>\n'
        f'├ <tg-emoji emoji-id="5258204546391351475">💰</tg-emoji> Баланс: {user["balance"]:.2f} ₽\n'
        f'├ <tg-emoji emoji-id="5345782953941157821">👥</tg-emoji> Реф. баланс: {user["ref_balance"]:.2f} ₽\n'
        f'└ <tg-emoji emoji-id="5258513401784573443">📱</tg-emoji> Доступно устройств: {user["devices_limit"]}\n\n'
        f'{subscription_note(user)}'
    )
    if user["subscription_active"] and user.get("subscription_until"):
        text += (
            f'\n\n<tg-emoji emoji-id="5462969603309201071">⏳</tg-emoji> '
            f'Подписка заканчивается: {user["subscription_until"]}'
        )
    return text


def help_instruction_text() -> str:
    return (
        '<b><tg-emoji emoji-id="5017108172138087141">📶</tg-emoji> Как подключиться</b>\n'
        '1. Открой раздел «Купить» и оформи тариф (или забери бесплатный Trial).\n'
        '2. Нажми на полученную ссылку «Подключить» — Telegram сам предложит включить прокси.\n'
        '3. Либо отсканируй QR-код (Профиль → Моя ссылка → QR-код) с другого устройства.\n\n'
        '<b><tg-emoji emoji-id="5197288647275071607">✅</tg-emoji> Если «не работает»</b>\n'
        '• Удали старый прокси в Telegram → Настройки → Данные и память → Прокси, потом добавь заново.\n'
        '• Проверь, что не превышен лимит устройств твоего тарифа.\n'
        '• Подожди минуту и попробуй снова — иногда нужно переподключение.\n\n'
        '<b><tg-emoji emoji-id="5298737505678407110">📱</tg-emoji> Несколько устройств</b>\n'
        'Одна ссылка работает на нескольких устройствах в пределах лимита тарифа.\n\n'
        '<b><tg-emoji emoji-id="5233231978739835702">🌐</tg-emoji> WEB-прокси</b>\n'
        'В подписке и в пробном доступе две ссылки: обычная и WEB — новый способ подключения в Telegram. '
        'WEB работает на Android и компьютере (нужна свежая версия Telegram), на iPhone пока нет. '
        'Не подключилась обычная — попробуй WEB. Обе ссылки — в «Моя ссылка».\n\n'
        'Не нашли решения? Напишите в поддержку по кнопке ниже:'
    )


def invite_friends_text(user: dict, ref_link: str, invited_count: int) -> str:
    return (
        '<b><tg-emoji emoji-id="5235632410191767985">🔹</tg-emoji> '
        'Делись VPN-сервисом с друзьями и получай:</b>\n'
        '35% от пополнений приглашённых друзей, прямо на реферальный баланс.\n\n'
        '<b><tg-emoji emoji-id="5235831365961820447">✈️</tg-emoji> Реферальная ссылка:</b>\n'
        f'{ref_link}\n\n'
        '<b><tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> СТАТИСТИКА:</b>\n'
        f'<tg-emoji emoji-id="5920052658743283381">👥</tg-emoji> Приглашено друзей: {invited_count}\n'
        f'<tg-emoji emoji-id="5877485980901971030">🪙</tg-emoji> Реферальный баланс: {user["ref_balance"]:.2f}₽\n\n'
        '<b><tg-emoji emoji-id="5875180111744995604">🎁</tg-emoji> Награды:</b>\n'
        '<tg-emoji emoji-id="5877465816030515018">🔄</tg-emoji> 1 друг → 3 день подписки\n\n'
        '<b>Баланс можно потратить на подписку или оформить вывод через поддержку</b>'
    )


def profile_text(user: dict) -> str:
    return (
        f'<tg-emoji emoji-id="5235879203307560141">👤</tg-emoji> Профиль\n\n'
        f'┌ <tg-emoji emoji-id="5346024704765347251">🆔</tg-emoji> ID: <code>{user["user_id"]}</code>\n'
        f'├ <tg-emoji emoji-id="5258204546391351475">💰</tg-emoji> Баланс: {user["balance"]:.2f} ₽\n'
        f'├ <tg-emoji emoji-id="5345782953941157821">👥</tg-emoji> Реф. баланс: {user["ref_balance"]:.2f} ₽\n'
        f'└ <tg-emoji emoji-id="5258513401784573443">📱</tg-emoji> Доступно устройств: {user["devices_limit"]}\n\n'
        f'{subscription_note(user)}'
    )


def _plan_line(key: str, bold: bool = False) -> str:
    p = PLANS[key]
    name = f"<b>{p['title']}</b>" if bold else p["title"]
    return (
        f'<tg-emoji emoji-id="{p["emoji"]}">{p["fallback"]}</tg-emoji> '
        f'{name} — {p["price"]} ₽/мес'
    )


def tariffs_text() -> str:
    blocks = "\n\n".join(f'{_plan_line(k, bold=True)}\n{p["specs"]}' for k, p in PLANS.items())
    return (
        '<tg-emoji emoji-id="5233403420949391533">✅</tg-emoji> <b>Тарифы Raiven PROXY</b>\n\n'
        f'<blockquote>{blocks}</blockquote>\n\n'
        '<tg-emoji emoji-id="5938252359321786473">⤵️</tg-emoji> <b>Выберите подходящий тариф:</b>'
    )


def plan_pay_text(key: str) -> str:
    return f"Оплата подписки: {_plan_line(key)}\n\nВыберите удобный способ оплаты:"


def topup_text() -> str:
    return (
        '<tg-emoji emoji-id="5224257782013769471">💳</tg-emoji> <b>Пополнение баланса</b>\n\n'
        'Выберите удобный способ для пополнения:'
    )


def referral_notice_text() -> str:
    """Уведомление пригласившему, когда по его ссылке пришёл новый друг."""
    return (
        '<tg-emoji emoji-id="5203996991054432397">🎁</tg-emoji> По вашей ссылке пришёл новый друг!\n'
        f'<blockquote>Вам начислено +{REF_BONUS_DAYS} дня подписки. Когда друг оформит подписку, '
        f'вы получите {int(REF_PERCENT * 100)}% от его оплаты на реферальный баланс.</blockquote>'
    )


# =============================================================================
# КЛАВИАТУРЫ
# style ('primary' синий / 'success' зелёный / 'danger' красный) и
# icon_custom_emoji_id доступны с aiogram >= 3.25.0 (Bot API 9.2+).
# icon_custom_emoji_id показывается только если у бота куплены доп. юзернеймы
# на Fragment, либо у владельца бота есть Telegram Premium — иначе Telegram
# просто покажет кнопку без иконки.
# =============================================================================


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="3 дня бесплатно",
                    callback_data="free_trial",
                    icon_custom_emoji_id="5203996991054432397",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Купить прокси",
                    callback_data="buy_proxy",
                    icon_custom_emoji_id="5235552094303332393",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Профиль",
                    callback_data="profile",
                    icon_custom_emoji_id="5235879203307560141",
                ),
                InlineKeyboardButton(
                    text="Управление прокси",
                    callback_data="manage_proxy",
                    icon_custom_emoji_id="5235879203307560141",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пригласить друзей",
                    callback_data="invite_friends",
                    icon_custom_emoji_id="5233194393481029544",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Помощь",
                    callback_data="help",
                    icon_custom_emoji_id="5235614916789971289",
                )
            ],
        ]
    )


def back_button(callback_data: str) -> InlineKeyboardButton:
    """Красная кнопка «Назад» — используется на всех дочерних экранах."""
    return InlineKeyboardButton(
        text="Назад",
        callback_data=callback_data,
        icon_custom_emoji_id="5258236805890710909",
        style="danger",
    )


def buy_proxy_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, p in PLANS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p['title']} — {p['price']} ₽/мес",
                    callback_data=f"buy_plan_{key}",
                    icon_custom_emoji_id=p["emoji"],
                    **({"style": "success"} if key == "max" else {}),
                )
            ]
        )
    rows.append([back_button("back_to_start")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_pay_kb(key: str) -> InlineKeyboardMarkup:
    price = PLANS[key]["price"]
    stars = math.ceil(price / STAR_RUB_RATE)

    def btn(text: str, method: str, icon: str, **extra) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text,
            callback_data=f"plan_pay_{key}_{method}",
            icon_custom_emoji_id=icon,
            **extra,
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(f"Stars ({stars})", "stars", "5438496463044752972", style="success"),
                btn(f"CryptoBot {price}₽", "cryptobot", "5217705010539812022"),
            ],
            [btn(f"Юkassa {price}₽ (СБП)", "yk_sbp", "5425008221330880308")],
            [btn(f"Карта (руб.) {price}₽", "yk_card", "5309851717104326562")],
            [btn(f"xRocket {price}₽", "xrocket", "5258332798409783582")],
            [
                btn(f"Списать с баланса ({price}₽)", "balance", "5258204546391351475"),
                btn(f"Списать с реф. баланса ({price}₽)", "ref", "5258513401784573443"),
            ],
            [back_button("buy_proxy")],
        ]
    )


def manage_proxy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Бесплатный период", callback_data="manage_free_period")],
            [InlineKeyboardButton(text="Купленный прокси", callback_data="manage_bought_proxy")],
            [InlineKeyboardButton(text="Докупить устройства", callback_data="manage_add_devices")],
            [back_button("back_to_start")],
        ]
    )


def topup_balance_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Stars",
                    callback_data="pay_method_stars",
                    icon_custom_emoji_id="5438496463044752972",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="Crypto bot",
                    callback_data="pay_method_cryptobot",
                    icon_custom_emoji_id="5217705010539812022",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Юkassa (СБП)",
                    callback_data="pay_method_yk_sbp",
                    icon_custom_emoji_id="5425008221330880308",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Карта (руб.)",
                    callback_data="pay_method_yk_card",
                    icon_custom_emoji_id="5309851717104326562",
                )
            ],
            [
                InlineKeyboardButton(
                    text="xRocket",
                    callback_data="pay_method_xrocket",
                    icon_custom_emoji_id="5258332798409783582",
                )
            ],
            [back_button("back_to_profile")],
        ]
    )


def topup_amount_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button("topup_balance")]])


def invoice_kb(url: str, invoice_id: int, back_to: str = "back_to_profile") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить", url=url, icon_custom_emoji_id=PAY_ICON, style="success"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверить оплату",
                    callback_data=f"pay_check_{invoice_id}",
                    icon_custom_emoji_id=CHECK_ICON,
                )
            ],
            [back_button(back_to)],
        ]
    )


def stars_invoice_kb(stars: int, back_to: str = "topup_balance") -> InlineKeyboardMarkup:
    """Клавиатура под счётом в звёздах: первая кнопка обязана быть кнопкой оплаты (pay=True)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить {stars} ⭐",
                    pay=True,
                    icon_custom_emoji_id=PAY_ICON,
                    style="success",
                )
            ],
            [back_button(back_to)],
        ]
    )


def simple_back_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button(callback_data)]])


def free_trial_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button("back_to_start")]])


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Инструкция",
                    callback_data="help_instruction",
                    icon_custom_emoji_id="5258254475386167466",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Поддержка",
                    url="https://t.me/layzadXx",
                    icon_custom_emoji_id="5260399854500191689",
                )
            ],
            [back_button("back_to_start")],
        ]
    )


def help_instruction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Написать в поддержку",
                    url="https://t.me/layzadXx",
                    icon_custom_emoji_id="5260535596941582167",
                )
            ],
            [back_button("back_to_help")],
        ]
    )


def activate_promo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button("back_to_profile")]])


def invite_friends_kb() -> InlineKeyboardMarkup:
    # Кнопка открывает выбор чата и вставляет inline-сообщение (см. on_inline_query) —
    # так текст приглашения уходит другу жирным шрифтом.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Поделиться с другом",
                    switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
                        query="",
                        allow_user_chats=True,
                        allow_group_chats=True,
                        allow_channel_chats=False,
                        allow_bot_chats=False,
                    ),
                    icon_custom_emoji_id="5233403420949391533",
                )
            ],
            [back_button("back_to_start")],
        ]
    )


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пополнить баланс",
                    callback_data="topup_balance",
                    icon_custom_emoji_id="5258204546391351475",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="История пополнений",
                    callback_data="topup_history",
                    icon_custom_emoji_id="5258328383183396223",
                ),
                InlineKeyboardButton(
                    text="Реферальная система",
                    callback_data="ref_system",
                    icon_custom_emoji_id="5258362837411045098",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Активировать промокод",
                    callback_data="activate_promo",
                    icon_custom_emoji_id="5994502837327892086",
                ),
                InlineKeyboardButton(
                    text="Вывести реф. баланс",
                    callback_data="withdraw_ref",
                    icon_custom_emoji_id="5258096772776991776",
                ),
            ],
            [back_button("back_to_start")],
        ]
    )


# =============================================================================
# АДМИН-ПАНЕЛЬ: FSM И КЛАВИАТУРЫ
# =============================================================================


class AdminFSM(StatesGroup):
    broadcast_text = State()
    balance_user = State()
    balance_amount = State()
    refbalance_user = State()
    refbalance_amount = State()
    promo_create_code = State()
    promo_create_bonus = State()
    promo_create_uses = State()
    promo_delete_code = State()
    subscription_user = State()
    subscription_days = State()
    role_add_user = State()
    role_remove_user = State()


class UserFSM(StatesGroup):
    promo_activation = State()


class TopupFSM(StatesGroup):
    amount = State()


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="adm_users_0")],
            [InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm_promo_menu")],
            [
                InlineKeyboardButton(text="💰 Начислить баланс", callback_data="adm_add_balance"),
                InlineKeyboardButton(text="🤝 Начислить реф. баланс", callback_data="adm_add_refbalance"),
            ],
            [
                InlineKeyboardButton(text="📤 Экспорт", callback_data="adm_export"),
                InlineKeyboardButton(text="🗄 Бэкап базы", callback_data="adm_backup"),
            ],
            [
                InlineKeyboardButton(text="🧾 История пополнений", callback_data="adm_topup_history"),
                InlineKeyboardButton(text="🛍 История покупок", callback_data="adm_purchase_history"),
            ],
            [InlineKeyboardButton(text="🛡 Управление ролями", callback_data="adm_roles_menu")],
            [InlineKeyboardButton(text="📝 Создать пост", callback_data="adm_broadcast")],
            [InlineKeyboardButton(text="🔧 Управление подпиской", callback_data="adm_subscription")],
        ]
    )


def admin_cancel_kb(back_to: str = "adm_back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=back_to)]]
    )


def admin_promo_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm_promo_create")],
            [InlineKeyboardButton(text="📃 Список промокодов", callback_data="adm_promo_list")],
            [InlineKeyboardButton(text="❌ Удалить промокод", callback_data="adm_promo_delete")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back_main")],
        ]
    )


def admin_roles_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Назначить админа", callback_data="adm_role_add")],
            [InlineKeyboardButton(text="➖ Снять админа", callback_data="adm_role_remove")],
            [InlineKeyboardButton(text="📃 Список админов", callback_data="adm_role_list")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back_main")],
        ]
    )


def admin_users_page_kb(offset: int, has_more: bool) -> InlineKeyboardMarkup:
    nav_row = []
    if offset > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"adm_users_{max(offset - 10, 0)}")
        )
    if has_more:
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"adm_users_{offset + 10}"))
    rows = [nav_row] if nav_row else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_broadcast_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить всем", callback_data="adm_broadcast_send")],
            [InlineKeyboardButton(text="Отмена", callback_data="adm_back_main")],
        ]
    )


def format_users_page(users: list[dict]) -> str:
    if not users:
        return "Пользователей пока нет."
    lines = []
    for u in users:
        sub = "актив." if u["subscription_active"] else "неактив."
        lines.append(
            f'<code>{u["user_id"]}</code> | @{u["username"] or "—"} | '
            f'{u["balance"]:.2f}₽ | подписка: {sub}'
        )
    return "\n".join(lines)


def format_topups(rows: list[dict]) -> str:
    if not rows:
        return "Пополнений пока нет."
    return "\n".join(
        f'<code>{r["user_id"]}</code> | +{r["amount"]:.2f}₽ | {r["method"]} | {r["created_at"]}'
        for r in rows
    )


def format_purchases(rows: list[dict]) -> str:
    if not rows:
        return "Покупок пока нет."
    return "\n".join(
        f'<code>{r["user_id"]}</code> | {r["item"]} | {r["amount"]:.2f}₽ | {r["created_at"]}'
        for r in rows
    )


def format_promos(rows: list[dict]) -> str:
    if not rows:
        return "Промокодов пока нет."
    return "\n".join(
        f'<code>{r["code"]}</code> | +{r["bonus_amount"]:.2f}₽ | '
        f'{r["used_count"]}/{r["max_uses"] if r["max_uses"] else "∞"} использований'
        for r in rows
    )


def format_admins(rows: list[dict]) -> str:
    text = f"<code>{OWNER_ID}</code> | owner (владелец)\n"
    text += "\n".join(f'<code>{r["user_id"]}</code> | {r["role"]}' for r in rows)
    return text


# =============================================================================
# ХЕНДЛЕРЫ
# =============================================================================

router = Router()

# Ссылки на фоновые задачи, чтобы их не удалил сборщик мусора
_background_tasks: set[asyncio.Task] = set()

# Кэш file_id картинок: после первой отправки файл хранится у Telegram, по URL повторно качать не нужно
_photo_cache: dict[str, str] = {}


def _photo(url: str):
    return _photo_cache.get(url) or URLInputFile(url)


def _remember_photo(url: str, result) -> None:
    if isinstance(result, Message) and result.photo:
        _photo_cache[url] = result.photo[-1].file_id


async def show_screen(
    callback: CallbackQuery,
    text: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
    parse_mode: str = "HTML",
) -> int:
    """Показывает экран ВМЕСТО текущего сообщения: старые кнопки заменяются новыми.

    Если тип сообщения меняется (фото <-> текст), старое удаляется и отправляется новое.
    Возвращает message_id итогового сообщения.
    """
    msg = callback.message
    try:
        if photo and msg.photo:
            result = await msg.edit_media(
                InputMediaPhoto(media=_photo(photo), caption=text, parse_mode=parse_mode),
                reply_markup=reply_markup,
            )
            _remember_photo(photo, result)
            return msg.message_id
        if not photo and msg.text:
            await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            return msg.message_id
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return msg.message_id
        logging.warning("Не удалось отредактировать сообщение: %s", exc)

    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    if photo:
        sent = await msg.answer_photo(
            photo=_photo(photo), caption=text, parse_mode=parse_mode, reply_markup=reply_markup
        )
        _remember_photo(photo, sent)
    else:
        sent = await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    return sent.message_id


async def _delete_quietly(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass


async def replace_prompt(
    message: Message, prompt_id: int | None, text: str, kb: InlineKeyboardMarkup
) -> None:
    """Заменяет экран с запросом ввода (фото-сообщение) результатом — старые кнопки пропадают."""
    if prompt_id:
        try:
            await message.bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=prompt_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    is_new = not await user_exists(uid)

    # Реферал засчитывается только новому пользователю и только если пригласивший уже есть в базе
    referrer_id = None
    parts = message.text.split(maxsplit=1)
    if is_new and len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            candidate = int(parts[1].removeprefix("ref_"))
        except ValueError:
            candidate = None
        if candidate and candidate != uid and await user_exists(candidate):
            referrer_id = candidate

    user = await get_or_create_user(uid, message.from_user.username, referrer_id)

    if referrer_id:
        await extend_subscription(referrer_id, REF_BONUS_DAYS)
        try:
            await message.bot.send_message(referrer_id, referral_notice_text())
        except Exception:
            logging.warning("Не удалось уведомить пригласившего %s", referrer_id)

    sent = await message.answer_photo(
        photo=_photo(START_PHOTO_URL),
        caption=start_caption(user),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    _remember_photo(START_PHOTO_URL, sent)


# --- Главное меню -----------------------------------------------------------

@router.callback_query(F.data == "free_trial")
async def cb_free_trial(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, photo=FREE_TRIAL_PHOTO_URL, reply_markup=free_trial_kb())


@router.callback_query(F.data == "buy_proxy")
async def cb_buy_proxy(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, tariffs_text(), buy_proxy_kb(), photo=BUY_PROXY_PHOTO_URL)


@router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(callback: CallbackQuery) -> None:
    key = callback.data.removeprefix("buy_plan_")
    if key not in PLANS:
        return await callback.answer()
    await callback.answer()
    await show_screen(callback, plan_pay_text(key), plan_pay_kb(key), photo=BUY_PROXY_PHOTO_URL)


@router.callback_query(F.data.startswith("plan_pay_"))
async def cb_plan_pay(callback: CallbackQuery) -> None:
    try:
        _, _, key, method = callback.data.split("_", 3)
    except ValueError:
        return await callback.answer()
    plan = PLANS.get(key)
    if not plan or (method not in PAY_TITLES and method not in ("balance", "ref")):
        return await callback.answer()

    price, uid = plan["price"], callback.from_user.id
    await get_or_create_user(uid, callback.from_user.username)

    # --- Списание с баланса / реф. баланса ---
    if method in ("balance", "ref"):
        field = "balance" if method == "balance" else "ref_balance"
        if not await spend_balance(uid, field, price):
            return await callback.answer("Недостаточно средств на балансе", show_alert=True)
        until = await activate_plan(uid, key)
        await add_purchase(uid, f"Тариф {plan['title']}", price)
        await callback.answer()
        return await show_screen(
            callback,
            f"✅ Тариф {plan['title']} активирован до {until}",
            simple_back_kb("back_to_start"),
        )

    await callback.answer()
    chat_id = callback.message.chat.id

    # --- Telegram Stars ---
    if method == "stars":
        stars = math.ceil(price / STAR_RUB_RATE)
        invoice_id = await add_invoice(uid, "stars", "", float(price), plan=key)
        await _delete_quietly(callback.bot, chat_id, callback.message.message_id)
        await callback.bot.send_invoice(
            chat_id=chat_id,
            title=f"Тариф {plan['title']}",
            description=f"Оплата подписки: {plan['title']} — {price} ₽/мес",
            payload=f"topup:{invoice_id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Тариф {plan['title']}", amount=stars)],
            reply_markup=stars_invoice_kb(stars, back_to=f"buy_plan_{key}"),
        )
        return

    # --- Платёжные системы ---
    me = await callback.bot.get_me()
    try:
        ext_id, url = await create_payment(method, float(price), f"https://t.me/{me.username}")
    except PaymentError:
        logging.exception("Не удалось создать счёт (%s)", method)
        return await callback.message.answer(
            "Не удалось создать счёт. Попробуйте другой способ или напишите в поддержку."
        )
    invoice_id = await add_invoice(uid, method, ext_id, float(price), plan=key)
    await show_screen(
        callback,
        f"Счёт на <b>{price} ₽</b> создан ({PAY_TITLES[method]}).\n"
        f"Тариф: {_plan_line(key)}\n"
        "После оплаты подписка активируется автоматически, либо нажмите «Проверить оплату».",
        invoice_kb(url, invoice_id, back_to=f"buy_plan_{key}"),
        photo=BUY_PROXY_PHOTO_URL,
    )
    task = asyncio.create_task(watch_invoice(callback.bot, invoice_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery) -> None:
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.answer()
    await show_screen(callback, profile_text(user), profile_kb(), photo=PROFILE_PHOTO_URL)


# «Пригласить друзей» (главное меню) и «Реферальная система» (профиль) — один и тот же экран
@router.callback_query(F.data.in_({"invite_friends", "ref_system"}))
async def cb_invite_friends(callback: CallbackQuery) -> None:
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    invited_count = await count_referrals(user["user_id"])
    me = await callback.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user['user_id']}"

    await callback.answer()
    await show_screen(callback, invite_friends_text(user, ref_link, invited_count), invite_friends_kb())


@router.inline_query()
async def on_inline_query(query: InlineQuery) -> None:
    """Сообщение-приглашение для кнопки «Поделиться с другом» (нужен включённый inline-режим у бота)."""
    me = await query.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{query.from_user.id}"
    article = InlineQueryResultArticle(
        id="share",
        title="Пригласить друга",
        description=SHARE_TEXT,
        input_message_content=InputTextMessageContent(
            message_text=f"<b>{SHARE_TEXT}</b>\n\n{ref_link}",
            parse_mode="HTML",
        ),
    )
    await query.answer([article], cache_time=0, is_personal=True)


@router.callback_query(F.data == "manage_proxy")
async def cb_manage_proxy(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, photo=MANAGE_PROXY_PHOTO_URL, reply_markup=manage_proxy_kb())


@router.callback_query(F.data == "manage_free_period")
async def cb_manage_free_period(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(
        callback,
        "Здесь будет информация о бесплатном периоде пользователя.",
        simple_back_kb("manage_proxy"),
    )


@router.callback_query(F.data == "manage_bought_proxy")
async def cb_manage_bought_proxy(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(
        callback, "Здесь будет список купленных proxy пользователя.", simple_back_kb("manage_proxy")
    )


@router.callback_query(F.data == "manage_add_devices")
async def cb_manage_add_devices(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(
        callback, "Здесь будет докупка дополнительных устройств.", simple_back_kb("manage_proxy")
    )


@router.callback_query(F.data.in_({"help", "back_to_help"}))
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, photo=HELP_PHOTO_URL, reply_markup=help_kb())


@router.callback_query(F.data == "help_instruction")
async def cb_help_instruction(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, help_instruction_text(), help_instruction_kb())


# --- Экран "Профиль" ---------------------------------------------------------

@router.callback_query(F.data == "topup_balance")
async def cb_topup_balance(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await show_screen(callback, topup_text(), topup_balance_kb(), photo=TOPUP_BALANCE_PHOTO_URL)


@router.callback_query(F.data.startswith("pay_method_"))
async def cb_pay_method(callback: CallbackQuery, state: FSMContext) -> None:
    method = callback.data.removeprefix("pay_method_")
    if method not in PAY_TITLES:
        return await callback.answer("Этот способ пока недоступен", show_alert=True)
    await callback.answer()
    await state.set_state(TopupFSM.amount)
    extra = f"\nКурс: 1 ⭐ = {STAR_RUB_RATE:g} ₽" if method == "stars" else ""
    prompt_id = await show_screen(
        callback,
        f"Способ: <b>{PAY_TITLES[method]}</b>{extra}\n\n"
        f"Введите сумму пополнения в ₽ (минимум {MIN_TOPUP:g} ₽):",
        topup_amount_kb(),
        photo=TOPUP_BALANCE_PHOTO_URL,
    )
    await state.update_data(method=method, prompt_id=prompt_id)


@router.message(TopupFSM.amount, F.text)
async def topup_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = round(float(message.text.replace(",", ".").strip()), 2)
    except ValueError:
        return await message.answer("Введите число, например 100")
    if not math.isfinite(amount):
        return await message.answer("Введите число, например 100")
    if amount < MIN_TOPUP:
        return await message.answer(
            f"Минимальная сумма пополнения — {MIN_TOPUP:g} ₽. Введите сумму ещё раз:"
        )
    if amount > MAX_TOPUP:
        return await message.answer(f"Максимальная сумма — {MAX_TOPUP:g} ₽. Введите сумму ещё раз:")

    data = await state.get_data()
    method, prompt_id = data["method"], data.get("prompt_id")
    uid, chat_id = message.from_user.id, message.chat.id

    # --- Telegram Stars: счёт в звёздах приходит отдельным сообщением ---
    if method == "stars":
        stars = math.ceil(amount / STAR_RUB_RATE)
        if stars > STARS_MAX:
            return await message.answer(
                f"За один раз можно оплатить не больше {STARS_MAX} ⭐. Введите сумму поменьше:"
            )
        credit = round(stars * STAR_RUB_RATE, 2)
        invoice_id = await add_invoice(uid, "stars", "", credit)
        await state.clear()
        await _delete_quietly(message.bot, chat_id, message.message_id)
        await _delete_quietly(message.bot, chat_id, prompt_id)
        await message.bot.send_invoice(
            chat_id=chat_id,
            title="Пополнение баланса",
            description=f"Пополнение баланса на {credit:.2f} ₽",
            payload=f"topup:{invoice_id}",
            currency="XTR",
            prices=[LabeledPrice(label="Пополнение баланса", amount=stars)],
            reply_markup=stars_invoice_kb(stars),
        )
        return

    # --- Остальные способы: счёт у платёжного провайдера ---
    me = await message.bot.get_me()
    try:
        ext_id, url = await create_payment(method, amount, f"https://t.me/{me.username}")
    except PaymentError:
        logging.exception("Не удалось создать счёт (%s)", method)
        return await message.answer(
            "Не удалось создать счёт. Попробуйте другой способ или напишите в поддержку.",
            reply_markup=topup_amount_kb(),
        )

    await state.clear()
    invoice_id = await add_invoice(uid, method, ext_id, amount)
    await _delete_quietly(message.bot, chat_id, message.message_id)
    await replace_prompt(
        message,
        prompt_id,
        f"Счёт на <b>{amount:.2f} ₽</b> создан ({PAY_TITLES[method]}).\n"
        "После оплаты баланс пополнится автоматически, либо нажмите «Проверить оплату».",
        invoice_kb(url, invoice_id),
    )
    task = asyncio.create_task(watch_invoice(message.bot, invoice_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def credit_invoice(bot: Bot, inv: dict) -> None:
    """Обрабатывает оплаченный счёт (вызывать только после claim_invoice):
    пополняет баланс или, если счёт на тариф, активирует подписку."""
    user_id, amount = inv["user_id"], inv["amount"]
    plan_key = inv.get("plan")
    if plan_key in PLANS:
        until = await activate_plan(user_id, plan_key)
        await add_purchase(user_id, f"Тариф {PLANS[plan_key]['title']}", amount)
        text = f"✅ Тариф {PLANS[plan_key]['title']} активирован до {until}"
    else:
        await adjust_balance(user_id, amount)
        await add_topup(user_id, amount, inv["method"])
        text = f"✅ Баланс пополнен на {amount:.2f} ₽"

    # Процент от оплаты — пригласившему (как написано в тексте реферальной системы)
    user = await get_or_create_user(user_id, None)
    if user["referrer_id"]:
        await adjust_ref_balance(user["referrer_id"], round(amount * REF_PERCENT, 2))

    try:
        await bot.send_message(
            user_id,
            text,
            reply_markup=simple_back_kb("back_to_start" if plan_key in PLANS else "back_to_profile"),
        )
    except Exception:
        pass


async def finalize_invoice(bot: Bot, invoice_id: int) -> str:
    """Проверяет счёт и при оплате зачисляет деньги. Возвращает 'paid' | 'already' | 'pending' | 'error'."""
    inv = await get_invoice(invoice_id)
    if not inv:
        return "error"
    if inv["status"] == "paid":
        return "already"
    try:
        paid = await is_paid(inv["method"], inv["ext_id"])
    except PaymentError:
        logging.exception("Ошибка проверки счёта %s", invoice_id)
        return "error"
    if not paid:
        return "pending"
    if not await claim_invoice(invoice_id):
        return "already"
    await credit_invoice(bot, inv)
    return "paid"


async def watch_invoice(bot: Bot, invoice_id: int) -> None:
    """Фоновая проверка оплаты: каждые 20 сек в течение 30 минут."""
    for _ in range(90):
        await asyncio.sleep(20)
        if await finalize_invoice(bot, invoice_id) in ("paid", "already"):
            return


@router.callback_query(F.data.startswith("pay_check_"))
async def cb_pay_check(callback: CallbackQuery) -> None:
    invoice_id = int(callback.data.removeprefix("pay_check_"))
    inv = await get_invoice(invoice_id)
    if not inv or inv["user_id"] != callback.from_user.id:
        return await callback.answer()
    result = await finalize_invoice(callback.bot, invoice_id)
    answers = {
        "paid": "✅ Оплата получена, баланс пополнен",
        "already": "Этот счёт уже зачислен",
        "pending": "Оплата ещё не поступила. Попробуйте через минуту",
        "error": "Не удалось проверить оплату, попробуйте позже",
    }
    await callback.answer(answers[result], show_alert=result in ("pending", "error"))


# --- Оплата звёздами ---------------------------------------------------------

@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    ok = False
    if query.invoice_payload.startswith("topup:"):
        try:
            inv = await get_invoice(int(query.invoice_payload.removeprefix("topup:")))
        except ValueError:
            inv = None
        ok = bool(
            inv
            and inv["method"] == "stars"
            and inv["status"] == "pending"
            and inv["user_id"] == query.from_user.id
        )
    await query.answer(ok=ok, error_message=None if ok else "Счёт недействителен. Создайте новый.")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if not payment.invoice_payload.startswith("topup:"):
        return
    try:
        inv = await get_invoice(int(payment.invoice_payload.removeprefix("topup:")))
    except ValueError:
        return
    if not inv or inv["method"] != "stars" or inv["user_id"] != message.from_user.id:
        return
    if await claim_invoice(inv["id"]):
        await set_invoice_ext(inv["id"], payment.telegram_payment_charge_id)
        await credit_invoice(message.bot, inv)


@router.callback_query(F.data == "topup_history")
async def cb_topup_history(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(callback, "Здесь будет история пополнений.", simple_back_kb("back_to_profile"))


@router.callback_query(F.data == "activate_promo")
async def cb_activate_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(UserFSM.promo_activation)
    prompt_id = await show_screen(
        callback,
        '<tg-emoji emoji-id="5258215635996908355">✏️</tg-emoji> Напишите ваш промокод в чат:',
        activate_promo_kb(),
        photo=PROMO_PHOTO_URL,
    )
    await state.update_data(prompt_id=prompt_id)


@router.message(UserFSM.promo_activation, F.text)
async def user_promo_activation(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get("prompt_id")
    code = message.text.strip().upper()
    promo = await get_promo(code)
    await state.clear()

    if not promo:
        result = "❌ Такой промокод не найден."
    elif promo["max_uses"] and promo["used_count"] >= promo["max_uses"]:
        result = "❌ У этого промокода закончился лимит активаций."
    else:
        await adjust_balance(message.from_user.id, promo["bonus_amount"])
        await increment_promo_use(code)
        await add_topup(message.from_user.id, promo["bonus_amount"], f"promo:{code}")
        result = f'✅ Промокод активирован! Начислено {promo["bonus_amount"]:.2f}₽ на баланс.'

    await _delete_quietly(message.bot, message.chat.id, message.message_id)
    await replace_prompt(message, prompt_id, result, activate_promo_kb())


@router.callback_query(F.data == "withdraw_ref")
async def cb_withdraw_ref(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_screen(
        callback, "Здесь будет вывод реферального баланса.", simple_back_kb("back_to_profile")
    )


@router.callback_query(F.data == "back_to_profile")
async def cb_back_to_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.answer()
    await show_screen(callback, profile_text(user), profile_kb(), photo=PROFILE_PHOTO_URL)


@router.callback_query(F.data == "back_to_start")
async def cb_back_to_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.answer()
    await show_screen(callback, start_caption(user), main_menu_kb(), photo=START_PHOTO_URL)


# --- АДМИН-ПАНЕЛЬ ------------------------------------------------------------

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель:", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_back_main")
async def adm_back_main(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await callback.answer()
    await show_screen(callback, "Админ-панель:", reply_markup=admin_menu_kb())


@router.callback_query(F.data.startswith("adm_users_"))
async def adm_users(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    offset = int(callback.data.removeprefix("adm_users_"))
    users = await list_users(limit=10, offset=offset)
    total = await count_users()
    await callback.answer()
    await show_screen(callback, 
        f"👥 Пользователи ({total} всего):\n\n{format_users_page(users)}",
        parse_mode="HTML",
        reply_markup=admin_users_page_kb(offset, offset + 10 < total),
    )


# --- Промокоды ---

@router.callback_query(F.data == "adm_promo_menu")
async def adm_promo_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await callback.answer()
    await show_screen(callback, "🎟 Промокоды:", reply_markup=admin_promo_menu_kb())


@router.callback_query(F.data == "adm_promo_list")
async def adm_promo_list(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    promos = await list_promos()
    await callback.answer()
    await show_screen(callback, 
        f"📃 Промокоды:\n\n{format_promos(promos)}",
        parse_mode="HTML",
        reply_markup=admin_promo_menu_kb(),
    )


@router.callback_query(F.data == "adm_promo_create")
async def adm_promo_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.promo_create_code)
    await callback.answer()
    await show_screen(callback, 
        "Введите код промокода (например, VPN2026):",
        reply_markup=admin_cancel_kb("adm_promo_menu"),
    )


@router.message(AdminFSM.promo_create_code)
async def adm_promo_create_code(message: Message, state: FSMContext) -> None:
    code = message.text.strip().upper()
    await state.update_data(code=code)
    await state.set_state(AdminFSM.promo_create_bonus)
    await message.answer(
        "Сумма бонуса в ₽ (например, 50):", reply_markup=admin_cancel_kb("adm_promo_menu")
    )


@router.message(AdminFSM.promo_create_bonus)
async def adm_promo_create_bonus(message: Message, state: FSMContext) -> None:
    try:
        bonus = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("Введите число, например 50 или 50.5")
    await state.update_data(bonus=bonus)
    await state.set_state(AdminFSM.promo_create_uses)
    await message.answer(
        "Максимум активаций (0 — без ограничений):",
        reply_markup=admin_cancel_kb("adm_promo_menu"),
    )


@router.message(AdminFSM.promo_create_uses)
async def adm_promo_create_uses(message: Message, state: FSMContext) -> None:
    try:
        max_uses = int(message.text.strip())
    except ValueError:
        return await message.answer("Введите целое число, например 100 или 0")
    data = await state.get_data()
    ok = await create_promo(data["code"], data["bonus"], max_uses)
    await state.clear()
    if ok:
        await message.answer(
            f'✅ Промокод <code>{data["code"]}</code> создан: +{data["bonus"]:.2f}₽, '
            f'лимит: {max_uses if max_uses else "∞"}',
            parse_mode="HTML",
            reply_markup=admin_promo_menu_kb(),
        )
    else:
        await message.answer(
            f'❌ Промокод <code>{data["code"]}</code> уже существует.',
            parse_mode="HTML",
            reply_markup=admin_promo_menu_kb(),
        )


@router.callback_query(F.data == "adm_promo_delete")
async def adm_promo_delete_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.promo_delete_code)
    await callback.answer()
    await show_screen(callback, 
        "Введите код промокода для удаления:", reply_markup=admin_cancel_kb("adm_promo_menu")
    )


@router.message(AdminFSM.promo_delete_code)
async def adm_promo_delete_code(message: Message, state: FSMContext) -> None:
    code = message.text.strip().upper()
    ok = await delete_promo(code)
    await state.clear()
    if ok:
        await message.answer(f"✅ Промокод {code} удалён.", reply_markup=admin_promo_menu_kb())
    else:
        await message.answer(f"❌ Промокод {code} не найден.", reply_markup=admin_promo_menu_kb())


# --- Баланс / реф. баланс ---

@router.callback_query(F.data == "adm_add_balance")
async def adm_add_balance_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.balance_user)
    await callback.answer()
    await show_screen(callback, "Введите ID пользователя:", reply_markup=admin_cancel_kb())


@router.message(AdminFSM.balance_user)
async def adm_add_balance_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        return await message.answer("ID должен быть числом. Попробуйте снова:")
    await state.update_data(user_id=user_id)
    await state.set_state(AdminFSM.balance_amount)
    await message.answer(
        "Сумма в ₽ (можно отрицательную, чтобы списать):", reply_markup=admin_cancel_kb()
    )


@router.message(AdminFSM.balance_amount)
async def adm_add_balance_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("Введите число, например 100 или -50")
    data = await state.get_data()
    user_id = data["user_id"]
    await adjust_balance(user_id, amount)
    if amount > 0:
        await add_topup(user_id, amount, "admin")
    await state.clear()
    await message.answer(
        f"✅ Баланс пользователя <code>{user_id}</code> изменён на {amount:+.2f}₽",
        parse_mode="HTML",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "adm_add_refbalance")
async def adm_add_refbalance_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.refbalance_user)
    await callback.answer()
    await show_screen(callback, "Введите ID пользователя:", reply_markup=admin_cancel_kb())


@router.message(AdminFSM.refbalance_user)
async def adm_add_refbalance_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        return await message.answer("ID должен быть числом. Попробуйте снова:")
    await state.update_data(user_id=user_id)
    await state.set_state(AdminFSM.refbalance_amount)
    await message.answer("Сумма в ₽ (можно отрицательную):", reply_markup=admin_cancel_kb())


@router.message(AdminFSM.refbalance_amount)
async def adm_add_refbalance_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("Введите число, например 100 или -50")
    data = await state.get_data()
    user_id = data["user_id"]
    await adjust_ref_balance(user_id, amount)
    await state.clear()
    await message.answer(
        f"✅ Реф. баланс пользователя <code>{user_id}</code> изменён на {amount:+.2f}₽",
        parse_mode="HTML",
        reply_markup=admin_menu_kb(),
    )


# --- Экспорт / бэкап ---

@router.callback_query(F.data == "adm_export")
async def adm_export(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await callback.answer()
    users = await list_users(limit=1_000_000, offset=0)
    fields = [
        "user_id", "username", "subscription_active", "subscription_until",
        "balance", "ref_balance", "devices_limit", "referrer_id", "created_at",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fields)
    for u in users:
        writer.writerow([u[k] for k in fields])
    data = buf.getvalue().encode("utf-8-sig")
    await callback.message.answer_document(
        BufferedInputFile(data, filename="users_export.csv"),
        caption=f"Экспорт пользователей: {len(users)} записей",
    )


@router.callback_query(F.data == "adm_backup")
async def adm_backup(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await callback.answer()
    await callback.message.answer_document(
        FSInputFile(DB_PATH),
        caption=f"Бэкап базы на {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )


# --- История ---

@router.callback_query(F.data == "adm_topup_history")
async def adm_topup_history(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    rows = await list_topups()
    await callback.answer()
    await show_screen(callback, 
        f"🧾 Последние пополнения:\n\n{format_topups(rows)}",
        parse_mode="HTML",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "adm_purchase_history")
async def adm_purchase_history(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    rows = await list_purchases()
    await callback.answer()
    await show_screen(callback, 
        f"🛍 Последние покупки:\n\n{format_purchases(rows)}",
        parse_mode="HTML",
        reply_markup=admin_menu_kb(),
    )


# --- Роли ---

@router.callback_query(F.data == "adm_roles_menu")
async def adm_roles_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await callback.answer()
    await show_screen(callback, "🛡 Управление ролями:", reply_markup=admin_roles_menu_kb())


@router.callback_query(F.data == "adm_role_list")
async def adm_role_list(callback: CallbackQuery) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    admins = await list_admins()
    await callback.answer()
    await show_screen(callback, 
        f"📃 Админы:\n\n{format_admins(admins)}",
        parse_mode="HTML",
        reply_markup=admin_roles_menu_kb(),
    )


@router.callback_query(F.data == "adm_role_add")
async def adm_role_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.role_add_user)
    await callback.answer()
    await show_screen(callback, 
        "Введите ID пользователя, которого нужно сделать админом:",
        reply_markup=admin_cancel_kb("adm_roles_menu"),
    )


@router.message(AdminFSM.role_add_user)
async def adm_role_add_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        return await message.answer("ID должен быть числом. Попробуйте снова:")
    await add_admin(user_id)
    await state.clear()
    await message.answer(
        f"✅ Пользователь <code>{user_id}</code> назначен админом.",
        parse_mode="HTML",
        reply_markup=admin_roles_menu_kb(),
    )


@router.callback_query(F.data == "adm_role_remove")
async def adm_role_remove_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.role_remove_user)
    await callback.answer()
    await show_screen(callback, 
        "Введите ID админа, которого нужно снять:", reply_markup=admin_cancel_kb("adm_roles_menu")
    )


@router.message(AdminFSM.role_remove_user)
async def adm_role_remove_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        return await message.answer("ID должен быть числом. Попробуйте снова:")
    if user_id == OWNER_ID:
        await state.clear()
        return await message.answer(
            "Нельзя снять владельца бота.", reply_markup=admin_roles_menu_kb()
        )
    await remove_admin(user_id)
    await state.clear()
    await message.answer(
        f"✅ Пользователь <code>{user_id}</code> больше не админ.",
        parse_mode="HTML",
        reply_markup=admin_roles_menu_kb(),
    )


# --- Рассылка (пост) ---

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.broadcast_text)
    await callback.answer()
    await show_screen(callback, 
        "Отправьте текст сообщения для рассылки (поддерживается HTML-разметка):",
        reply_markup=admin_cancel_kb(),
    )


@router.message(AdminFSM.broadcast_text)
async def adm_broadcast_text(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.html_text)
    await message.answer(
        f"Предпросмотр:\n\n{message.html_text}",
        parse_mode="HTML",
        reply_markup=admin_confirm_broadcast_kb(),
    )


@router.callback_query(F.data == "adm_broadcast_send")
async def adm_broadcast_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    data = await state.get_data()
    text = data.get("text")
    await state.clear()
    if not text:
        await callback.answer()
        await show_screen(callback, 
            "Текст рассылки не найден, начните заново.", reply_markup=admin_menu_kb()
        )
        return

    await callback.answer("Рассылка запущена…")
    ids = await all_user_ids()
    sent, failed = 0, 0
    for uid in ids:
        try:
            await callback.bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # не упираемся в лимиты Telegram (~30 сообщений/сек)

    await show_screen(callback, 
        f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}",
        reply_markup=admin_menu_kb(),
    )


# --- Управление подпиской ---

@router.callback_query(F.data == "adm_subscription")
async def adm_subscription_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_admin(callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminFSM.subscription_user)
    await callback.answer()
    await show_screen(callback, "Введите ID пользователя:", reply_markup=admin_cancel_kb())


@router.message(AdminFSM.subscription_user)
async def adm_subscription_user(message: Message, state: FSMContext) -> None:
    try:
        user_id = int(message.text.strip())
    except ValueError:
        return await message.answer("ID должен быть числом. Попробуйте снова:")
    await state.update_data(user_id=user_id)
    await state.set_state(AdminFSM.subscription_days)
    await message.answer(
        "На сколько дней активировать подписку? (0 — деактивировать):",
        reply_markup=admin_cancel_kb(),
    )


@router.message(AdminFSM.subscription_days)
async def adm_subscription_days(message: Message, state: FSMContext) -> None:
    try:
        days = int(message.text.strip())
    except ValueError:
        return await message.answer("Введите целое число дней, например 30 или 0")
    data = await state.get_data()
    user_id = data["user_id"]
    until = await set_subscription(user_id, days)
    await state.clear()
    if days > 0:
        await message.answer(
            f"✅ Подписка пользователя <code>{user_id}</code> активирована до {until}",
            parse_mode="HTML",
            reply_markup=admin_menu_kb(),
        )
    else:
        await message.answer(
            f"✅ Подписка пользователя <code>{user_id}</code> деактивирована",
            parse_mode="HTML",
            reply_markup=admin_menu_kb(),
        )


# =============================================================================
# ЗАПУСК
# =============================================================================


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
