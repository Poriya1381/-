from rubibot import RubiBot, types, updates
import os
import time
import json
import re
import hashlib
import secrets
import requests
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _install_exception_hooks():
    import sys
    import threading

    def _main_excepthook(exc_type, exc_value, exc_tb):
        print("UNCAUGHT:", repr(exc_value))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_excepthook(args):
        print("THREAD ERROR:", repr(args.exc_value))

    sys.excepthook = _main_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_excepthook

# =========================
# CONFIG
# =========================

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

OFFSET_FILE = os.path.join(DATA_DIR, "offset.txt")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
READY_FILE = os.path.join(DATA_DIR, "ready.flag")

TOKEN = os.getenv("TOKEN", "CDABFH0UFMCTTJJSAXMWDOTDORNFGAFFIQYZXZYJLBRVPVFJZTDXGXLSQLYQVMIU").strip()
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is required")
CARD = os.getenv("CARD_NUMBER", "6219861932569709")

SUPPORT = os.getenv("SUPPORT_USERNAME", "@Poriysmeii")
CODE = "@PoriyBot"

# ادمین‌ها فقط از Environment خوانده می‌شوند تا آیدی قدیمی/اشتباه باعث خطای not Access نشود.
# در Render مقدار ADMIN_ID یا ADMIN_IDS را با آیدی واقعی چت ادمین تنظیم کنید.
ADMINS = {"b0KYDRB0BArE0211c204a80a0c67fa47"}

PORT = int(os.getenv("PORT", "10000"))

BASE = "data"
os.makedirs(BASE, exist_ok=True)

OF = f"{BASE}/offset.txt"
DF = f"{BASE}/orders.json"
READY = f"{BASE}/ready.flag"

API = f"https://botapi.rubika.ir/v3/{TOKEN}"

bot = RubiBot(TOKEN)
print("ADMINS:", sorted(ADMINS))


# =========================
# PERFORMANCE / RESILIENCE
# =========================

POLL_LIMIT = 10

# کوتاه برای پاسخ سریع؛ قطع اینترنت هم سریع تشخیص داده می‌شود.
REQUEST_TIMEOUT = (2, 8)
EMPTY_POLL_DELAY = 0.005

# فقط هنگام خطای واقعی شبکه/API استفاده می‌شود.
BACKOFF_MIN = 0.20
BACKOFF_MAX = 5.0

# پردازش پیام‌ها از polling جداست.
WORKERS = 16
SEND_WORKERS = 8

executor = ThreadPoolExecutor(
    max_workers=WORKERS,
    thread_name_prefix="update"
)

send_executor = ThreadPoolExecutor(
    max_workers=SEND_WORKERS,
    thread_name_prefix="send"
)

http = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    import socket
    _adapter = HTTPAdapter(pool_connections=128, pool_maxsize=128, max_retries=0)
    http.mount("http://", _adapter)
    http.mount("https://", _adapter)
except Exception:
    pass
http.headers.update({
    "Content-Type": "application/json",
    "Connection": "keep-alive",
    "User-Agent": "RubikaShopBot/2.0"
})

orders_lock = Lock()
offset_lock = Lock()

# سفارش‌هایی که هنوز رسید نداده‌اند؛ تا قبل از رسید در پنل ادمین دیده نمی‌شوند.
pending_lock = Lock()
PENDING_ORDERS = {}

# مرحله فعلی هر کاربر برای دکمه «بازگشت به مرحله قبل».
USER_STAGE = {}

# آمار کاربران و خریدهای موفق
USERS_FILE = os.path.join(DATA_DIR, "users.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
USERS = set()
STATS = {"successful_purchases": 0}
ADMIN_LOG_FILE = os.path.join(DATA_DIR, "admin_log.json")
ADMIN_NOTES_FILE = os.path.join(DATA_DIR, "admin_notes.json")
ADMIN_LOG = []
ADMIN_NOTES = {}

# کنترل‌های پیشرفته ادمین
ADMIN_SETTINGS_FILE = os.path.join(DATA_DIR, "admin_settings.json")
BLOCKED_FILE = os.path.join(DATA_DIR, "blocked_users.json")
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.json")
REFERRALS_FILE = os.path.join(DATA_DIR, "referrals.json")
NOTIFICATIONS_FILE = os.path.join(DATA_DIR, "notifications.json")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")
POINTS_FILE = os.path.join(DATA_DIR, "points.json")
USER_META_FILE = os.path.join(DATA_DIR, "user_meta.json")
ANNOUNCEMENT_FILE = os.path.join(DATA_DIR, "announcement.txt")
COUPONS_FILE = os.path.join(DATA_DIR, "coupons.json")
ADMIN_SETTINGS = {"maintenance": False, "broadcast_confirm": False, "shop_paused": False}
BLOCKED_USERS = set()
WALLETS = {}
REFERRALS = {}
USER_META = {}
NOTIFICATIONS = {}
FAVORITES = {}
USER_POINTS = {}
ANNOUNCEMENTS = []
COUPONS = {}
ANNOUNCEMENT = ""
WALLET_LOG_FILE = os.path.join(DATA_DIR, "wallet_log.json")
WALLET_LOG = []
REFERRAL_COMMISSION_LOG_FILE = os.path.join(DATA_DIR, "referral_commissions.json")
REFERRAL_COMMISSION_LOG = []
try:
    _wl = json.loads(read(WALLET_LOG_FILE, "[]"))
    if isinstance(_wl, list):
        WALLET_LOG = _wl[-1000:]
except Exception:
    pass

try:
    _settings = json.loads(read(ADMIN_SETTINGS_FILE, "{}"))
    if isinstance(_settings, dict):
        ADMIN_SETTINGS.update(_settings)
except Exception:
    pass
try:
    _blocked = json.loads(read(BLOCKED_FILE, "[]"))
    if isinstance(_blocked, list):
        BLOCKED_USERS = {str(x) for x in _blocked}
except Exception:
    pass

def save_control_data():
    try:
        write(ADMIN_SETTINGS_FILE, json.dumps(ADMIN_SETTINGS, ensure_ascii=False, separators=(",", ":")))
        write(BLOCKED_FILE, json.dumps(sorted(BLOCKED_USERS), ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("SAVE_CONTROL:", repr(e))


def _load_json_file(path, default):
    try:
        x = json.loads(read(path, json.dumps(default, ensure_ascii=False)))
        return x if isinstance(x, type(default)) else default
    except Exception:
        return default

# داده‌های اضافی بعد از تعریف read دوباره بارگذاری می‌شوند.

def save_extra_data():
    try:
        write(WALLETS_FILE, json.dumps(WALLETS, ensure_ascii=False, separators=(",", ":")))
        write(REFERRALS_FILE, json.dumps(REFERRALS, ensure_ascii=False, separators=(",", ":")))
        write(NOTIFICATIONS_FILE, json.dumps(NOTIFICATIONS, ensure_ascii=False, separators=(",", ":")))
        write(FAVORITES_FILE, json.dumps(FAVORITES, ensure_ascii=False, separators=(",", ":")))
        write(POINTS_FILE, json.dumps(USER_POINTS, ensure_ascii=False, separators=(",", ":")))
        write(USER_META_FILE, json.dumps(USER_META, ensure_ascii=False, separators=(",", ":")))
        write(COUPONS_FILE, json.dumps(COUPONS, ensure_ascii=False, separators=(",", ":")))
        write(ANNOUNCEMENT_FILE, ANNOUNCEMENT)
    except Exception as e:
        print("SAVE_EXTRA:", repr(e))

def wallet_log(action, uid, amount, before, after, admin_id=""):
    WALLET_LOG.append({"time": int(time.time()), "action": action, "uid": str(uid), "amount": int(amount), "before": int(before), "after": int(after), "admin_id": str(admin_id)})
    del WALLET_LOG[:-1000]
    try:
        write(WALLET_LOG_FILE, json.dumps(WALLET_LOG, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("SAVE_WALLET_LOG:", repr(e))

def wallet(uid):
    return int(WALLETS.get(str(uid), 0) or 0)

def set_wallet(uid, amount):
    WALLETS[str(uid)] = max(0, int(amount))
    save_extra_data()

def wallet_add(uid, amount):
    set_wallet(uid, wallet(uid) + int(amount))

def wallet_take(uid, amount):
    amount = int(amount)
    if wallet(uid) < amount:
        return False
    set_wallet(uid, wallet(uid) - amount)
    return True

def coupon_apply(code, price):
    c = COUPONS.get(str(code).strip().upper())
    if not isinstance(c, dict) or not c.get("active", True):
        return None
    uses = int(c.get("uses", 0))
    limit = int(c.get("limit", 0))
    if limit and uses >= limit:
        return None
    pct = max(0, min(100, int(c.get("percent", 0))))
    off = int(price) * pct // 100
    return off, c

def mark_coupon_used(code):
    c = COUPONS.get(str(code).strip().upper())
    if isinstance(c, dict):
        c["uses"] = int(c.get("uses", 0)) + 1
        save_extra_data()


try:
    _log = json.loads(read(ADMIN_LOG_FILE, "[]"))
    if isinstance(_log, list):
        ADMIN_LOG = _log[-500:]
except Exception:
    pass

try:
    _notes = json.loads(read(ADMIN_NOTES_FILE, "{}"))
    if isinstance(_notes, dict):
        ADMIN_NOTES = _notes
except Exception:
    pass

def save_admin_data():
    try:
        write(ADMIN_LOG_FILE, json.dumps(ADMIN_LOG[-500:], ensure_ascii=False, separators=(",", ":")))
        write(ADMIN_NOTES_FILE, json.dumps(ADMIN_NOTES, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("SAVE_ADMIN_DATA:", repr(e))

def admin_log(action, order_id="", admin_id=""):
    ADMIN_LOG.append({
        "time": int(time.time()),
        "action": str(action),
        "order_id": str(order_id),
        "admin_id": str(admin_id)
    })
    if len(ADMIN_LOG) > 500:
        del ADMIN_LOG[:-500]
    save_admin_data()


try:
    _users = json.loads(read(USERS_FILE, "[]"))
    if isinstance(_users, list):
        USERS = {str(x) for x in _users}
except Exception:
    USERS = set()

try:
    _stats = json.loads(read(STATS_FILE, "{}"))
    if isinstance(_stats, dict):
        STATS["successful_purchases"] = int(_stats.get("successful_purchases", 0))
except Exception:
    pass

state_lock = Lock()
last_poll_ok = time.monotonic()
last_message_at = 0.0


# =========================
# FILE
# =========================

def read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
            return value if value else default
    except Exception:
        return default


def write(path, value):
    tmp = path + ".tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(value))

        os.replace(tmp, path)
        return True

    except Exception as e:
        print("WRITE:", repr(e))
        return False


def valid_offset(x):
    return bool(
        x
        and isinstance(x, str)
        and len(x) < 500
    )


# فایل‌های مدیریتی بعد از آماده‌شدن read/write دوباره بارگذاری می‌شوند.
try:
    _log = json.loads(read(ADMIN_LOG_FILE, "[]"))
    if isinstance(_log, list):
        ADMIN_LOG = _log[-500:]
except Exception:
    pass
try:
    _notes = json.loads(read(ADMIN_NOTES_FILE, "{}"))
    if isinstance(_notes, dict):
        ADMIN_NOTES = _notes
except Exception:
    pass
try:
    _rcl = json.loads(read(REFERRAL_COMMISSION_LOG_FILE, "[]"))
    if isinstance(_rcl, list):
        REFERRAL_COMMISSION_LOG = _rcl[-2000:]
except Exception:
    pass

try:
    _settings = json.loads(read(ADMIN_SETTINGS_FILE, "{}"))
    if isinstance(_settings, dict):
        ADMIN_SETTINGS.update(_settings)
except Exception:
    pass
try:
    _blocked = json.loads(read(BLOCKED_FILE, "[]"))
    if isinstance(_blocked, list):
        BLOCKED_USERS = {str(x) for x in _blocked}
except Exception:
    pass

WALLETS = _load_json_file(WALLETS_FILE, {})
REFERRALS = _load_json_file(REFERRALS_FILE, {})
try:
    _rcl = json.loads(read(REFERRAL_COMMISSION_LOG_FILE, "[]"))
    if isinstance(_rcl, list):
        REFERRAL_COMMISSION_LOG = _rcl[-2000:]
except Exception:
    REFERRAL_COMMISSION_LOG = []
USER_META = _load_json_file(USER_META_FILE, {})
NOTIFICATIONS = _load_json_file(NOTIFICATIONS_FILE, {})
FAVORITES = _load_json_file(FAVORITES_FILE, {})
USER_POINTS = _load_json_file(POINTS_FILE, {})
COUPONS = _load_json_file(COUPONS_FILE, {})
ANNOUNCEMENT = read(ANNOUNCEMENT_FILE, "")


# =========================
# ORDERS
# =========================

try:
    ORDERS = json.loads(
        read(DF, "{}")
    )

    if not isinstance(ORDERS, dict):
        ORDERS = {}

except Exception:
    ORDERS = {}


def save_orders():
    with orders_lock:
        tmp = DF + ".tmp"

        try:
            with open(
                tmp,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    ORDERS,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":")
                )

            os.replace(tmp, DF)

        except Exception as e:
            print("SAVE:", repr(e))


# =========================
# KEYBOARD
# =========================

def kb(rows):

    k = types.ChatKeypad(
        resize_keyboard=True
    )

    for row in rows:

        r = types.KeypadRow()

        for text, data in row:

            r.add(
                types.KeypadSimpleButton(
                    text,
                    data
                )
            )

        k.add(r)

    return k


MAIN = kb([
    [("🛍 خرید", "services")],
    [("📦 سفارش‌ها", "orders"), ("💰 کیف پول", "wallet")],
    [("👤 حساب کاربری", "profile"), ("👥 دعوت دوستان", "referral")],
    [("🎟 ثبت کد تخفیف", "referral_code"), ("🔔 اعلان‌ها / پیام‌ها", "notifications")],
    [("📚 آموزش ربات", "docs"), ("🆘 پشتیبانی", "support")],
    [("ℹ️ درباره فروشگاه", "about")]
])

SERVICES_KB = kb([
    [("📣 کانال", "channel_prices"), ("👥 گروه", "group_prices")],
    [("⭐ روبینو", "followers_prices")],
    [("🏠 بازگشت به اصلی", "home")]
])

# سازگاری نام‌های قدیمی که در بعضی مسیرهای برگشت استفاده شده بودند.
SERV = SERVICES_KB

CUSTOMER_ORDER_KB = kb([
    [("🧾 همه سفارش‌ها", "orders"), ("📦 سفارش‌های فعال", "track")],
    [("📌 آخرین سفارش", "last_order"), ("🔁 سفارش مجدد", "reorder")],
    [("🛍 خرید جدید", "services"), ("🔙 بازگشت", "back")],
    [("🏠 اصلی", "home")]
])

CUSTOMER_ACCOUNT_KB = kb([
    [("👤 پروفایل من", "profile"), ("📊 آمار من", "mystats")],
    [("🧾 سفارش‌های من", "orders"), ("📦 پیگیری سفارش", "track")],
    [("📌 آخرین سفارش", "last_order"), ("🔁 سفارش مجدد", "reorder")],
    [("💰 کیف پول", "wallet"), ("🏠 اصلی", "home")]
])

CUSTOMER_ORDERS_KB = kb([
    [("🧾 همه سفارش‌ها", "orders"), ("📦 سفارش‌های فعال", "track")],
    [("📌 آخرین سفارش", "last_order"), ("🔁 سفارش مجدد", "reorder")],
    [("🛍 خرید جدید", "services"), ("🏠 اصلی", "home")]
])

CUSTOMER_WALLET_KB = kb([
    [("💰 موجودی کیف پول", "wallet"), ("💳 گردش کیف پول", "wallet_history")],
    [("🛍 استفاده برای خرید", "services"), ("🏠 اصلی", "home")]
])

CUSTOMER_REFERRAL_KB = kb([
    [("👥 دعوت دوستان", "referral"), ("🎟 ثبت کد تخفیف", "referral_code")],
    [("🏠 اصلی", "home")]
])

CUSTOMER_NOTIFICATIONS_KB = kb([
    [("🔔 اعلان‌های من", "notifications")],
    [("🛍 خرید", "services"), ("🏠 اصلی", "home")]
])

CUSTOMER_MORE_KB = kb([
    [("📚 آموزش ربات", "docs"), ("📜 قوانین", "rules")],
    [("🏠 اصلی", "home")]
])

ADMIN_KB = kb([
    [("📦 سفارش‌ها", "admin_orders"), ("👥 کاربران", "admin_users")],
    [("💰 حسابداری", "admin_finance"), ("💳 کیف پول", "wallet_admin")],
    [("📣 بازاریابی", "admin_marketing"), ("📊 گزارش‌ها", "admin_reports")],
    [("🔎 جستجو", "admin_search"), ("🛠 سیستم", "admin_system")],
    [("💾 پشتیبان و خروجی", "admin_backup"), ("📚 آموزش ادمین", "admin_docs")],
    [("🏠 اصلی", "home")]
])

ADMIN_ORDERS_KB = kb([
    [("📋 جدید", "new"), ("🔵 در حال انجام", "work")],
    [("🟢 تکمیل‌شده", "done"), ("🔴 لغوشده", "cancelled")],
    [("🕘 آخرین سفارش‌ها", "latest")],
    [("⚡ عملیات سریع", "bulk"), ("📊 آمار سفارش", "stats")],
    [("🔙 پنل مدیریت", "admin")]
])

ADMIN_USERS_KB = kb([
    [("👥 فهرست کاربران", "users")],
    [("👤 پرونده کاربر", "user_profile_admin"), ("✉️ پیام کاربر", "message_user")],
    [("🚫 مسدود / رفع مسدودی", "block_user")],
    [("🔙 پنل مدیریت", "admin")]
])

# حسابداری فقط ابزارهای حسابداری/گزارش مالی را دارد؛ مدیریت کیف پول در بخش مستقل کیف پول است.
ADMIN_FINANCE_KB = kb([
    [("📊 حسابداری کلی", "finance"), ("📅 امروز", "today")],
    [("📆 این هفته", "finance_week"), ("📆 این ماه", "finance_month")],
    [("💎 کمیسیون معرفی", "referral_admin")],
    [("🔙 پنل مدیریت", "admin")]
])

ADMIN_MARKETING_KB = kb([
    [("🎟 کدهای تخفیف", "discount_admin"), ("📣 اطلاعیه", "announce_admin")],
    [("📢 ارسال همگانی", "broadcast"), ("✉️ پیام مستقیم", "message_user")],
    [("🔙 پنل مدیریت", "admin")]
])

ADMIN_SYSTEM_KB = kb([
    [("🛠 وضعیت سیستم", "system"), ("⚙️ تنظیمات فروشگاه", "settings")],
    [("🧪 تست سلامت", "ultra_test"), ("🔄 بروزرسانی", "admin_refresh")],
    [("🔐 امنیت", "security"), ("🕓 فعالیت ادمین", "activity")],
    [("💾 پشتیبان", "backup"), ("📤 خروجی سفارش‌ها", "export")],
    [("🧹 پاکسازی", "cleanup")],
    [("🔙 پنل مدیریت", "admin")]
])


# =========================
# PRICES
# =========================

CHANNEL = [
    "100 — 24,000",
    "500 — 72,000",
    "1,000 — 132,000",
    "5,000 — 600,000",
    "10,000 — 1,140,000",
    "15,000 — 1,600,000"
]
GROUP = [
    "100 — 24,000",
    "500 — 72,000",
    "1,000 — 132,000",
    "5,000 — 600,000",
    "10,000 — 1,140,000",
    "2,000 — 260,000"
]

FOLLOWERS = [
    "1,000 — 18,000",
    "10,000 — 120,000",
    "50,000 — 540,000",
    "100,000 — 960,000",
    "150,000 — 1,200,000"
]

# قیمت‌های بالا قیمت نهایی فروش هستند؛ افزایش حدود ۲۰٪ نسبت به تعرفه قبلی
PRICE_MARKUP_PERCENT = 0

# سیستم معرفی حرفه‌ای:
# - کد معرف فقط قبل از اولین خرید قابل ثبت است و بعد از ثبت قابل تغییر نیست.
# - پس از تکمیل هر خرید موفق فرد دعوت‌شده: ۱۰٪ مبلغ خرید به کیف پول خریدار و ۱۰٪ به کیف پول معرف اضافه می‌شود.
# - ثبت کد جعلی/نامعتبر ممکن نیست؛ کد فقط از بین کدهای واقعی تولیدشده توسط ربات پذیرفته می‌شود.
REFERRAL_DISCOUNT_PERCENT = 15
REFERRAL_COMMISSION_PERCENT = 10
REFERRAL_COMMISSION_MIN = 0
REFERRAL_COMMISSION_MAX = 0  # 0 = بدون سقف
REFERRAL_CODE_BYTES = 24       # 192 بیت آنتروپی؛ کد بسیار طولانی و غیرقابل حدس
REFERRAL_FIRST_PURCHASE_ONLY = True
REFERRAL_BLOCK_COUPON_STACKING = True

def marked_price_text(item):
    m = re.search(r"^(.*?)(?:\s*[—\-–]\s*)([\d\.,]+)$", str(item))
    if not m:
        return str(item)
    amount = int(re.sub(r"[^0-9]", "", m.group(2)) or 0)
    final = amount * (100 + PRICE_MARKUP_PERCENT) // 100
    return f"{m.group(1)} — {final:,}"



# =========================
# SEND
# =========================

def _mark_poll_ok():
    global last_poll_ok
    with state_lock:
        last_poll_ok = time.monotonic()


def _api_send_ok(result):
    """PyRubikaBotAPI may return an error dict instead of raising an exception."""
    if result is None:
        return True
    if isinstance(result, dict):
        status = str(result.get("status", "")).strip().lower()
        if status in ("", "ok", "success"):
            return True
        print("SEND_RESULT:", repr(result)[:1200])
        return False
    status = str(getattr(result, "status", "") or "").strip().lower()
    if status and status not in ("ok", "success"):
        print("SEND_RESULT:", repr(result)[:1200])
        return False
    return True


def send(uid, text, key=MAIN):
    """ارسال مطمئن؛ اگر کیبورد باعث خطای API شود، متن بدون کیبورد حتماً ارسال می‌شود."""
    uid = str(uid)
    text = str(text)

    # اول ارسال معمولی با کیبورد. بعضی نسخه‌های API خطا را return می‌کنند نه exception.
    try:
        result = bot.send_message(uid, text, chat_keypad=key)
        if _api_send_ok(result):
            return True
    except Exception as e:
        print("SEND_KEYPAD:", uid, repr(e))

    # fallback: پاسخ متنی بدون keypad تا ربات در هیچ حالت ساکت نماند.
    for attempt, delay in enumerate((0.0, 0.15, 0.40), 1):
        if delay:
            time.sleep(delay)
        try:
            result = bot.send_message(uid, text)
            if _api_send_ok(result):
                print("SEND_FALLBACK_OK:", uid)
                return True
        except Exception as e:
            print("SEND_PLAIN:", uid, "attempt=", attempt, repr(e))
            low = str(e).lower()
            if "not access" in low or ("access" in low and "not" in low):
                return False
    return False


def queue_send(uid, text, key=MAIN):
    """ارسال فوری؛ از صف دوم استفاده نمی‌کند تا پیام کاربر معطل نشود."""
    try:
        return send(uid, text, key)
    except Exception as e:
        print("SEND_QUEUE:", repr(e))
        return False


def admin_send(text, key=ADMIN_KB):
    for admin in ADMINS:
        queue_send(admin, text, key)


# =========================
# USERS
# =========================

def is_admin(m):

    uid = str(
        getattr(m, "chat_id", "") or ""
    )

    sid = str(
        getattr(m, "sender_id", "") or ""
    )

    return (
        uid in ADMINS
        or sid in ADMINS
    )


def set_stage(uid, stage):
    with state_lock:
        USER_STAGE[str(uid)] = stage


def save_users():
    try:
        write(USERS_FILE, json.dumps(sorted(USERS), ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("SAVE_USERS:", repr(e))


def save_stats():
    try:
        write(STATS_FILE, json.dumps(STATS, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("SAVE_STATS:", repr(e))


def referral_code_for(uid):
    """کد دعوت طولانی، تصادفی و غیرقابل حدس."""
    uid = str(uid)
    ref = REFERRALS.get(uid) if isinstance(REFERRALS.get(uid), dict) else {}
    code = str(ref.get("code") or "").strip().upper()

    # کدهای قدیمی کوتاه را مهاجرت می‌کنیم تا کد جدید واقعاً طولانی باشد.
    if len(code) < 32:
        used = {
            str(x.get("code", "")).upper()
            for x in REFERRALS.values()
            if isinstance(x, dict)
        }
        for _ in range(10):
            candidate = "PORI" + secrets.token_hex(REFERRAL_CODE_BYTES).upper()
            if candidate not in used:
                code = candidate
                break
        ref["code"] = code
        ref.setdefault("count", 0)
        ref.setdefault("invited", [])
        REFERRALS[uid] = ref
        save_extra_data()
    return code


def add_notification(uid, title, body=None):
    uid = str(uid)
    if body is None:
        body = str(title)
        title = "اعلان فروشگاه"
    arr = NOTIFICATIONS.setdefault(uid, [])
    arr.append({"time": int(time.time()), "title": str(title), "body": str(body), "read": False})
    del arr[:-50]
    save_extra_data()


def user_notifications_text(uid):
    arr = list(NOTIFICATIONS.get(str(uid), []))
    if not arr:
        return "🔔 اعلان‌های من\n\n📭 هیچ اعلان جدیدی برای حساب شما ثبت نشده است."
    lines = ["🔔 اعلان‌های من\n"]
    for x in reversed(arr[-20:]):
        tm = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(x.get("time", 0))))
        lines.append(f"📌 {x.get('title', 'اعلان')}\n🕒 {tm}\n{x.get('body', '')}")
    return "\n\n".join(lines)


def favorites(uid):
    return list(FAVORITES.get(str(uid), [])) if isinstance(FAVORITES.get(str(uid), []), list) else []


def wallet_balance(uid):
    return wallet(uid)


def apply_referral(uid, code):
    """ثبت امن کد معرف؛ فقط قبل از اولین خرید واقعی و فقط یک‌بار."""
    uid = str(uid)
    code = str(code or "").strip().upper().lstrip("#")
    if not code:
        return False, "❌ کد معرف خالی است."

    my_ref = REFERRALS.get(uid) if isinstance(REFERRALS.get(uid), dict) else {}

    # بعد از ثبت معرف دیگر امکان تغییر/ثبت معرف دوم وجود ندارد.
    if str(my_ref.get("invited_by") or "").strip():
        return False, "ℹ️ شما قبلاً کد معرف خود را ثبت کرده‌اید."

    # بعد از اولین خرید تکمیل‌شده، ثبت کد معرف کاملاً بسته است.
    if has_paid_order(uid):
        return False, "❌ ثبت کد معرف فقط قبل از اولین خرید امکان‌پذیر است."

    owner = ""
    for owner_id, data in REFERRALS.items():
        if not isinstance(data, dict):
            continue
        saved_code = str(data.get("code", "")).strip().upper()
        if saved_code and secrets.compare_digest(saved_code, code):
            owner = str(owner_id)
            break

    if not owner:
        return False, "❌ کد معرف نامعتبر است."
    if owner == uid:
        return False, "❌ نمی‌توانید کد معرف خودتان را ثبت کنید."

    owner_ref = REFERRALS.setdefault(owner, {})
    owner_ref.setdefault("count", 0)
    owner_ref.setdefault("invited", [])
    invited = [str(x) for x in owner_ref.get("invited", [])]

    if uid in invited:
        return False, "ℹ️ این دعوت قبلاً ثبت شده است."

    # ثبت اتصال معرف به‌صورت دائمی؛ دیگر قابل جایگزینی نیست.
    my_ref["code"] = referral_code_for(uid)
    my_ref["invited_by"] = owner
    my_ref["joined_at"] = int(time.time())
    my_ref["bound_at"] = int(time.time())
    my_ref["referral_discount_used"] = False
    my_ref["referral_discount_targets"] = []
    my_ref["cashback_orders"] = int(my_ref.get("cashback_orders", 0) or 0)
    my_ref["cashback_total"] = int(my_ref.get("cashback_total", 0) or 0)
    REFERRALS[uid] = my_ref

    owner_ref["count"] = int(owner_ref.get("count", 0)) + 1
    owner_ref["invited"] = invited + [uid]
    REFERRALS[owner] = owner_ref
    save_extra_data()

    # کاربر دعوت‌شده و صاحب کد هر دو اعلان می‌گیرند.
    add_notification(
        uid,
        "🎟 کد معرف ثبت شد",
        "کد معرف شما با موفقیت ثبت شد. پس از تکمیل هر خرید موفق، ۱۰٪ مبلغ خرید به کیف پول شما و ۱۰٪ به کیف پول معرفتان اضافه می‌شود."
    )
    add_notification(
        owner,
        "👥 دعوت موفق",
        f"🎉 موفق! یک نفر با کد معرف شما دعوت شد.\n\n"
        f"👥 تعداد دعوت‌های شما: {int(owner_ref.get('count', 0)):,}\n"
        "💎 پس از تکمیل هر خرید موفق این دعوت‌شده، ۱۰٪ مبلغ خرید به کیف پول شما و ۱۰٪ به کیف پول خودش اضافه می‌شود."
    )
    queue_send(
        owner,
        f"🎉 موفق! یک نفر با کد معرف شما دعوت شد.\n\n"
        f"👥 تعداد دعوت‌های شما: {int(owner_ref.get('count', 0)):,}\n"
        "💎 بعد از تکمیل هر خرید موفق او، ۱۰٪ مبلغ خرید به کیف پول شما شارژ می‌شود."
    )

    return True, (
        "✅ کد معرف با موفقیت ثبت شد.\n\n"
        "🔒 این کد فقط قبل از اولین خرید قابل ثبت است و دیگر قابل تغییر نیست.\n"
        "💰 بعد از تکمیل هر خرید موفق: ۱۰٪ مبلغ خرید به کیف پول شما و ۱۰٪ به کیف پول معرفتان اضافه می‌شود."
    )


def referral_commission_log(order_id, referrer, buyer, amount, base_amount):
    REFERRAL_COMMISSION_LOG.append({
        "time": int(time.time()),
        "order_id": str(order_id),
        "referrer": str(referrer),
        "buyer": str(buyer),
        "amount": int(amount),
        "base_amount": int(base_amount),
        "percent": int(REFERRAL_COMMISSION_PERCENT),
    })
    del REFERRAL_COMMISSION_LOG[:-2000]
    try:
        write(REFERRAL_COMMISSION_LOG_FILE, json.dumps(
            REFERRAL_COMMISSION_LOG, ensure_ascii=False, separators=(",", ":")
        ))
    except Exception as e:
        print("SAVE_REFERRAL_COMMISSION:", repr(e))


def referral_owner(uid):
    ref = REFERRALS.get(str(uid), {})
    if isinstance(ref, dict):
        owner = str(ref.get("invited_by") or "").strip()
        if owner and owner != str(uid):
            return owner
    return ""


def referral_commission_already_paid(order_id):
    oid_s = str(order_id)
    return any(
        str(x.get("order_id")) == oid_s
        for x in REFERRAL_COMMISSION_LOG
        if isinstance(x, dict)
    )


def calculate_referral_commission(order):
    """محاسبه کش‌بک معرفی برای هر سفارش موفق؛ ۱۵٪ برای هر دو طرف."""
    if not isinstance(order, dict):
        return 0, "", "invalid"
    if order.get("wallet_refunded"):
        return 0, "", "refunded"
    if order.get("referral_cashback_paid"):
        return 0, "", "already_paid"

    referrer = referral_owner(order.get("chat_id"))
    if not referrer:
        return 0, "", "no_referrer"

    # صاحب کد نباید خودش باشد و کد باید واقعاً در سیستم ثبت شده باشد.
    if str(referrer) == str(order.get("chat_id")):
        return 0, "", "self_referral"

    ref = REFERRALS.get(str(order.get("chat_id")), {})
    if not isinstance(ref, dict) or str(ref.get("invited_by") or "") != str(referrer):
        return 0, "", "invalid_referral"

    paid_amount = num(order.get("final", 0))
    if paid_amount <= 0:
        return 0, "", "zero_amount"

    amount = paid_amount * REFERRAL_COMMISSION_PERCENT // 100
    if REFERRAL_COMMISSION_MIN:
        amount = max(amount, int(REFERRAL_COMMISSION_MIN))
    if REFERRAL_COMMISSION_MAX:
        amount = min(amount, int(REFERRAL_COMMISSION_MAX))
    return max(0, amount), referrer, "eligible"


def process_referral_commission(order, admin_id):
    """پرداخت کش‌بک معرفی به هر دو نفر: معرف و خریدار، فقط یک‌بار برای هر سفارش."""
    if not isinstance(order, dict):
        return False, "invalid", 0

    order_id = str(order.get("id") or "")
    if not order_id:
        return False, "invalid_order", 0

    # جلوگیری قطعی از پرداخت دوباره یک سفارش
    if order.get("referral_cashback_paid") or referral_commission_already_paid(order_id):
        order["referral_cashback_paid"] = 1
        return False, "already_paid", 0

    amount, referrer, reason = calculate_referral_commission(order)
    if amount <= 0 or not referrer:
        return False, reason, 0

    buyer = str(order.get("chat_id") or "")
    referrer = str(referrer)

    if not buyer or buyer == referrer:
        return False, "invalid_buyer", 0

    # کش‌بک باید به هر دو نفر و به یک مبلغ یکسان پرداخت شود.
    ref_before = wallet(referrer)
    buyer_before = wallet(buyer)

    WALLETS[referrer] = ref_before + amount
    WALLETS[buyer] = buyer_before + amount

    ref_after = wallet(referrer)
    buyer_after = wallet(buyer)

    wallet_log("referral_commission", referrer, amount, ref_before, ref_after, "system")
    wallet_log("referral_cashback_buyer", buyer, amount, buyer_before, buyer_after, "system")

    referral_commission_log(
        order_id,
        referrer,
        buyer,
        amount,
        num(order.get("final", 0))
    )

    ref = REFERRALS.setdefault(referrer, {})
    ref["commission_count"] = int(ref.get("commission_count", 0) or 0) + 1
    ref["commission_total"] = int(ref.get("commission_total", 0) or 0) + amount
    ref["commission_paid_order"] = order_id

    buyer_ref = REFERRALS.setdefault(buyer, {})
    buyer_ref["cashback_orders"] = int(buyer_ref.get("cashback_orders", 0) or 0) + 1
    buyer_ref["cashback_total"] = int(buyer_ref.get("cashback_total", 0) or 0) + amount

    # پرچم پرداخت روی خود سفارش؛ برای جلوگیری از تکرار بعد از تغییر وضعیت.
    order["referral_cashback_paid"] = amount
    order["referral_cashback_referrer"] = referrer
    order["referral_cashback_buyer"] = buyer

    save_extra_data()

    add_notification(
        referrer,
        f"💎 پاداش معرفی سفارش #{order_id}",
        f"سفارش دوست دعوت‌شده شما تکمیل شد.\\n\\n"
        f"💰 پاداش {REFERRAL_COMMISSION_PERCENT}%: {money(amount)} تومان\\n"
        f"💳 موجودی جدید: {money(ref_after)} تومان"
    )
    queue_send(
        referrer,
        f"🎉 پاداش معرفی دریافت شد!\\n\\n"
        f"📦 سفارش دعوت‌شده: #{order_id}\\n"
        f"💎 شارژ کیف پول شما: {money(amount)} تومان\\n"
        f"💳 موجودی جدید: {money(ref_after)} تومان"
    )

    add_notification(
        buyer,
        f"🎁 کش‌بک خرید سفارش #{order_id}",
        f"خرید شما با کد معرف تکمیل شد.\\n\\n"
        f"💰 کش‌بک {REFERRAL_COMMISSION_PERCENT}%: {money(amount)} تومان\\n"
        f"💳 موجودی جدید: {money(buyer_after)} تومان"
    )
    queue_send(
        buyer,
        f"🎉 کش‌بک خرید دریافت شد!\\n\\n"
        f"📦 سفارش #{order_id}\\n"
        f"💎 شارژ کیف پول شما: {money(amount)} تومان\\n"
        f"💳 موجودی جدید: {money(buyer_after)} تومان"
    )

    admin_log(
        f"referral_cashback:{buyer}:{referrer}:{amount}",
        order_id,
        admin_id
    )
    return True, "paid", amount

def register_start(uid, referral_code=None):
    uid = str(uid)
    is_new = uid not in USERS
    if is_new:
        USERS.add(uid)
        save_users()
    referral_code_for(uid)
    if referral_code and is_new:
        ok, msg = apply_referral(uid, referral_code)
        if ok:
            # پیام اصلی بعد از ثبت است تا کاربر جایزه را واضح ببیند.
            return msg
    return None


def current_order(uid):
    uid = str(uid)
    with pending_lock:
        p = PENDING_ORDERS.get(uid)
        if p:
            return p
    return last_order(uid)


def start(m, referral_code=None):
    try:
        remember_user_message(m)
    except Exception:
        pass
    uid = str(m.chat_id)
    reward_msg = register_start(uid, referral_code)
    set_stage(uid, "main")
    text = "🛍 فروشگاه روبیکا\n\n✨ به فروشگاه خوش آمدید\n👇 پنل موردنظر را انتخاب کنید:"
    if reward_msg:
        text += "\n\n" + reward_msg
    send(uid, text, MAIN)


def back_previous(uid):
    uid = str(uid)
    stage = USER_STAGE.get(uid, "main")
    p = PENDING_ORDERS.get(uid)

    if stage == "services_desc":
        set_stage(uid, "main")
        send(uid, "🏠 پنل اصلی:", MAIN)
        return

    if stage == "services":
        set_stage(uid, "services_desc")
        send(
            uid,
            "ℹ️ توضیحات خدمات\n\nتمامی ممبر های ما دارای ضمانت هستند و هیچ گونه ریزش ندارند.",
            kb([[("ℹ️ توضیحات خدمات", "desc")], [("🏠 اصلی", "home")]])
        )
        return

    if stage in ("channel_prices", "group_prices", "followers_prices"):
        set_stage(uid, "services")
        send(uid, "🛍 خدمات", SERV)
        return

    if p and stage == "target":
        # از مرحله انتخاب سرویس برگرد
        set_stage(uid, "services")
        with pending_lock:
            PENDING_ORDERS.pop(uid, None)
        send(uid, "🛍 خدمات", SERV)
        return

    if p and stage == "discount":
        p["waiting"] = 1
        p["discount_wait"] = 0
        set_stage(uid, "target")
        send(
            uid,
            "📌 یوزرنیم مقصد را ارسال کنید.",
            kb([[("🔙 بازگشت به مرحله قبل", "back")], [("❌ خروج", "cancel")]])
        )
        return

    if p and stage == "payment":
        p["discount_wait"] = 1
        set_stage(uid, "discount")
        send(
            uid,
            "🎁 کد تخفیف دارید؟",
            kb([[("❌ ندارم", "no_discount")], [("🔙 بازگشت به مرحله قبل", "back")], [("❌ خروج", "cancel")]])
        )
        return

    set_stage(uid, "main")
    send(uid, "🏠 پنل اصلی:", MAIN)


# =========================
# HELPERS
# =========================

def oid(o):

    try:
        return int(
            o.get("id", 0)
        )

    except Exception:
        return 0


def num(x):

    try:

        return int(
            str(x)
            .replace(",", "")
            .replace(".", "")
            .replace(" تومان", "")
            .strip()
        )

    except Exception:
        return 0


def money(x):
    return f"{num(x):,}"


def get_user_orders(uid):

    uid = str(uid)

    with orders_lock:

        return [
            o for o in ORDERS.values()
            if str(o.get("chat_id")) == uid
        ]


def last_order(uid):
    with pending_lock:
        p = PENDING_ORDERS.get(str(uid))
        if p:
            return p

    orders = get_user_orders(uid)
    if not orders:
        return None

    return max(orders, key=oid)


# =========================
# USERNAME
# =========================

USERNAME_CACHE = {}

def get_username(m):
    uid = str(getattr(m, "chat_id", "") or "")
    cached = USERNAME_CACHE.get(uid)
    if cached:
        return cached

    # Prefer username already present on the update to avoid an extra API request.
    for obj in (m, getattr(m, "sender", None), getattr(m, "from_user", None)):
        try:
            u = getattr(obj, "username", None)
            if u:
                value = "@" + str(u).lstrip("@")
                USERNAME_CACHE[uid] = value
                return value
        except Exception:
            pass

    try:
        c = bot.get_chat(uid)
        u = getattr(c, "username", None)
        if u:
            value = "@" + str(u).lstrip("@")
            USERNAME_CACHE[uid] = value
            return value
    except Exception:
        pass

    return "ندارد"


def normalize_username(text):

    text = text.strip()

    if re.fullmatch(
        r"@[A-Za-z0-9_]{3,64}",
        text
    ):
        return text

    m = re.fullmatch(
        r"https?://(?:www\.)?"
        r"(?:rubika\.ir|web\.rubika\.ir)/"
        r"([A-Za-z0-9_]{3,64})/?",
        text,
        re.I
    )

    if m:
        return "@" + m.group(1)

    return None


# =========================
# PRICES
# =========================

def show_prices(
    uid,
    items,
    prefix,
    title
):

    rows = [
        [(prefix + marked_price_text(x), "price")]
        for x in items
    ]

    rows.append([
        ("🔙 بازگشت به مرحله قبل", "back"),
        ("🏠 اصلی", "home")
    ])

    if "فالور" in title:
        set_stage(uid, "followers_prices")
    elif "گروه" in title:
        set_stage(uid, "group_prices")
    else:
        set_stage(uid, "channel_prices")

    send(uid, title, kb(rows))


def extract_price(text):

    m = re.search(
        r"(\d[\d,\.]*)\s*[—\-–]\s*([\d,\.]+)",
        text
    )

    if not m:
        return None

    count = m.group(1)
    price = m.group(2)

    if text.startswith("📣"):
        typ = "کانال"

    elif text.startswith("👥"):
        typ = "گروه"

    elif text.startswith("⭐"):
        typ = "روبینو"

    else:
        return None

    return typ, count, price


# =========================
# USER CENTER / WALLET / HISTORY
# =========================
def user_orders_summary(uid):
    orders = get_user_orders(uid)
    total = sum(num(o.get("final", 0)) for o in orders)
    done = sum(o.get("status") == "تکمیل شد" for o in orders)
    active = sum(o.get("status") == "در حال انجام" for o in orders)
    waiting = sum(o.get("status") == "در انتظار بررسی" for o in orders)
    cancelled = sum(o.get("status") == "لغو شد" for o in orders)
    return orders, total, done, active, waiting, cancelled

def user_profile(uid):
    orders,total,done,active,waiting,cancelled=user_orders_summary(uid)
    return (f"👤 حساب من\n\n🆔 {uid}\n📦 کل سفارش‌ها: {len(orders):,}\n"
            f"🟢 موفق: {done:,}\n🔵 فعال: {active:,}\n📋 در انتظار: {waiting:,}\n"
            f"🔴 لغوشده: {cancelled:,}\n💵 مجموع خرید: {money(total)} تومان\n💰 کیف پول: {money(wallet(uid))} تومان")

def user_stats(uid):
    orders,total,done,active,waiting,cancelled=user_orders_summary(uid)
    return (f"📊 آمار من\n\n📦 سفارش: {len(orders):,}\n🟢 موفق: {done:,}\n"
            f"🔵 فعال: {active:,}\n📋 منتظر بررسی: {waiting:,}\n🔴 لغو: {cancelled:,}\n"
            f"💰 مجموع پرداخت: {money(total)} تومان\n💳 موجودی کیف پول: {money(wallet(uid))} تومان")

def user_orders(uid):
    orders=sorted(get_user_orders(uid), key=oid, reverse=True)[:30]
    if not orders:
        return "🧾 هنوز سفارشی ندارید."
    return "🧾 سفارش‌های من\n\n"+"\n".join(f"#{o['id']} | {o.get('type')} | {money(o.get('final',0))} تومان | {o.get('status')}" for o in orders)

def user_track(uid):
    orders=sorted([o for o in get_user_orders(uid) if o.get('status') in ("در انتظار بررسی","در حال انجام")], key=oid, reverse=True)[:20]
    if not orders:
        return "📦 سفارش فعالی ندارید."
    return "📦 پیگیری سفارش\n\n"+"\n".join(f"#{o['id']} | {o.get('type')} | {o.get('status')} | {o.get('target','---')}" for o in orders)

def user_last(uid):
    o=last_order(uid)
    if not o: return "📌 هنوز سفارشی ندارید."
    return (f"📌 آخرین سفارش\n\n🆔 #{o.get('id')}\n🛍 {o.get('service')}\n📌 {o.get('type')}\n"
            f"🔗 {o.get('target','---')}\n💰 {money(o.get('final',0))} تومان\n📊 {o.get('status')}")


def reorder_last(uid):
    o = last_order(uid)
    if not o:
        send(uid, "🔁 سفارش مجدد\n\n📭 شما هنوز سفارشی ثبت نکرده‌اید.\nابتدا یک سفارش ثبت کنید.", CUSTOMER_ORDERS_KB)
        return
    send(uid,
         f"🔁 سفارش مجدد\n\n🛍 سرویس: {o.get('service')}\n"
         f"📌 نوع: {o.get('type')}\n🔗 مقصد: {o.get('target') or '---'}\n\n"
         "برای ثبت سفارش جدید، از بخش خرید همان سرویس را دوباره انتخاب کنید.",
         kb([[("🛍 خرید", "services")], [("🏠 اصلی", "home")]]))

# =========================
# ORDER
# =========================

def create_order(m, service, price, typ):
    uid = str(m.chat_id)
    username = get_username(m)

    with orders_lock:
        ids = [oid(o) for o in ORDERS.values()]
        n = max(ids + [1000]) + 1

    # مهم: اینجا هنوز سفارش ثبت‌شده نیست؛ فقط پیش‌سفارش در حافظه است.
    o = {
        "id": n,
        "chat_id": uid,
        "sender_id": str(getattr(m, "sender_id", "") or uid),
        "username": username,
        "service": service,
        "type": typ,
        "price": price,
        "final": num(price),
        "discount": 0,
        "coupon_code": "",
        "referral_discount_applied": False,
        "referral_discount_percent": 0,
        "referral_base_price": num(price),
        "referral_target_key": "",
        "payment_method": "card_receipt",
        "target": "",
        "status": "در انتظار بررسی",
        "waiting": 1,
        "discount_wait": 0,
        "receipt": 0,
        "created": int(time.time())
    }

    with pending_lock:
        PENDING_ORDERS[uid] = o

    set_stage(uid, "target")

    send(
        uid,
        "📌 یوزرنیم مقصد را ارسال کنید.\n\n"
        "@username\n\n"
        "یا لینک روبیکا را ارسال کنید.",
        kb([
            [("🔙 بازگشت به مرحله قبل", "back")],
            [("❌ خروج", "cancel")]
        ])
    )

def payment(uid, o):
    set_stage(uid, "payment")
    discount_note = ""
    if o.get("coupon_code"):
        discount_note = (
            "🎟 کد تخفیف اعمال شد\n"
            "💸 مبلغ ۲۰ درصد از کل سفارش شما کم شد.\n"
            f"💰 تخفیف: {money(o.get('discount', 0))} تومان\n\n"
        )
    text = (
        f"💳 پرداخت سفارش #{o['id']}\n\n"
        f"🛍 {o['service']}\n"
        f"📌 {o['type']}\n"
        f"🔗 {o['target']}\n\n"
        + discount_note
        + f"💰 مبلغ نهایی: {money(o['final'])} تومان\n\n"
        f"💳 کارت:\n{CARD}\n\n"
        "📸 رسید را به صورت عکس ارسال کنید."
    )
    send(
        uid,
        text,
        kb([
            [("💰 پرداخت با کیف پول", "wallet_pay"), ("💰 موجودی کیف پول", "wallet")],
            [("🔙 بازگشت به مرحله قبل", "back")],
            [("❌ خروج", "cancel")]
        ])
    )


# =========================
# WALLET PAYMENT
# =========================
def pay_wallet(uid):
    uid = str(uid)
    with pending_lock:
        o = PENDING_ORDERS.get(uid)
        if not o:
            send(uid, "❌ سفارش در حال پرداختی ندارید.", MAIN)
            return
        if o.get("receipt") or o.get("wallet_paid"):
            send(uid, "⚠️ این سفارش قبلاً برای پرداخت ثبت شده است.", MAIN)
            return
        amount = num(o.get("final", 0))
        if amount <= 0:
            send(uid, "❌ مبلغ سفارش نامعتبر است.", MAIN)
            return

    old = wallet(uid)
    if old < amount:
        send(
            uid,
            f"❌ موجودی کیف پول کافی نیست.\n\n"
            f"💵 موجودی: {money(old)} تومان\n"
            f"💳 مبلغ سفارش: {money(amount)} تومان\n"
            f"➖ کسری: {money(amount-old)} تومان",
            kb([[('💰 کیف پول','wallet')],[('💳 پرداخت کارت','card_payment')],[('❌ خروج','cancel')]])
        )
        return

    # قفل مرحله پرداخت تا دوباره‌پرداخت همزمان رخ ندهد.
    with pending_lock:
        o = PENDING_ORDERS.get(uid)
        if not o or o.get("receipt") or o.get("wallet_paid"):
            return
        amount = num(o.get("final", 0))
        wallet_before = wallet(uid)
        if wallet_before < amount:
            send(uid, "❌ موجودی دیگر کافی نیست.", MAIN)
            return
        wallet_take(uid, amount)
        new_balance = wallet(uid)
        o["wallet_before"] = wallet_before
        o["wallet_after"] = new_balance
        o["wallet_paid"] = 1
        o["payment_method"] = "wallet"
        o["receipt"] = 0
        o["status"] = "در انتظار بررسی"
        o["waiting"] = 0
        o["discount_wait"] = 0
        ORDERS[str(o["id"])] = dict(o)
        PENDING_ORDERS.pop(uid, None)

    wallet_log("order_payment", uid, -amount, old, new_balance, "system")
    save_extra_data()
    save_orders()
    set_stage(uid, "main")

    # برای سفارش کیف پول، پرداخت مستقیم است و رسید لازم نیست.
    admin_caption = (
        f"💰 سفارش کیف پولی جدید #{o['id']}\n"
        f"🛍 {o.get('service')}\n"
        f"📌 {o.get('type')}\n"
        f"🔗 {o.get('target')}\n"
        f"💵 مبلغ پرداخت‌شده: {money(amount)} تومان\n"
        f"👤 کاربر: {uid}\n"
        "💳 روش پرداخت: کیف پول\n"
        "⚠️ این سفارش کمیسیون معرفی ایجاد نمی‌کند."
    )
    admin_send(admin_caption, admin_buttons(o))
    send(
        uid,
        f"✅ پرداخت با کیف پول موفق بود.\n\n"
        f"🆔 سفارش: #{o['id']}\n"
        f"💵 مبلغ: {money(amount)} تومان\n"
        f"💰 موجودی جدید: {money(new_balance)} تومان\n\n"
        "⏳ سفارش برای مدیریت ارسال شد.",
        MAIN
    )


def wallet_refund_for_order(o, admin_id):
    if not isinstance(o, dict) or not o.get("wallet_paid") or o.get("wallet_refunded"):
        return False
    uid = str(o.get("chat_id", ""))
    amount = num(o.get("final", 0))
    if not uid or amount <= 0:
        return False
    before = wallet(uid)
    wallet_add(uid, amount)
    after = wallet(uid)
    o["wallet_refunded"] = 1
    wallet_log("order_refund", uid, amount, before, after, admin_id)
    add_notification(uid, f"↩️ مبلغ {money(amount)} تومان سفارش #{o.get('id')} به کیف پول شما برگشت داده شد.\n\nموجودی جدید: {money(after)} تومان")
    save_extra_data()
    return True


# =========================
# TARGET
# =========================

def has_paid_order(uid):
    """آیا کاربر قبلاً یک خرید واقعیِ تکمیل‌شده داشته است؟"""
    uid = str(uid)
    with orders_lock:
        return any(
            str(o.get("chat_id")) == uid
            and o.get("status") == "تکمیل شد"
            and (int(o.get("receipt", 0) or 0) == 1 or int(o.get("wallet_paid", 0) or 0) == 1)
            for o in ORDERS.values()
            if isinstance(o, dict)
        )

def referral_target_key(target):
    return re.sub(r"\\s+", "", str(target or "").strip().lower())


def referral_discount_eligibility(uid, target):
    """سازگاری با ساختار قبلی؛ طرح جدید تخفیف ندارد و فقط کش‌بک هنگام تکمیل سفارش انجام می‌شود."""
    uid = str(uid)
    ref = REFERRALS.get(uid, {})
    if isinstance(ref, dict) and ref.get("invited_by"):
        return False, "referral_cashback_only"
    return False, "no_referrer"


def set_target(m, text):
    uid = str(m.chat_id)
    with pending_lock:
        o = PENDING_ORDERS.get(uid)

    if not o or not o.get("waiting"):
        return

    username = normalize_username(text)

    if not username:
        set_stage(uid, "target")
        send(
            uid,
            "❌ یوزرنیم نامعتبر است.\n\nمثال:\n@Poriysmeii\n\nدوباره یوزرنیم را ارسال کنید.",
            kb([
                [("🔙 بازگشت به مرحله قبل", "back")],
                [("❌ خروج", "cancel")]
            ])
        )
        return

    with pending_lock:
        o["target"] = username
        o["waiting"] = 0
        o["discount_wait"] = 0

        # کد معرف هیچ تخفیفی روی قیمت ایجاد نمی‌کند؛
        # کش‌بک فقط بعد از تکمیل موفق سفارش توسط ادمین انجام می‌شود.
        o["final"] = num(o.get("price", 0))
        o["discount"] = 0
        o["referral_discount_applied"] = False
        o["referral_discount_percent"] = 0
        o["referral_base_price"] = num(o.get("price", 0))
        o["referral_target_key"] = referral_target_key(username)
        o["discount_reason"] = "referral_cashback"

    # مرحله کد تخفیف عادی همچنان بدون تغییر کار می‌کند.
    with pending_lock:
        o["discount_wait"] = 1
    set_stage(uid, "discount")
    send(
        uid,
        f"✅ مقصد ثبت شد:\n{username}\n\n🎁 کد تخفیف دارید؟",
        kb([
            [("❌ ندارم", "no_discount")],
            [("🔙 بازگشت به مرحله قبل", "back")],
            [("❌ خروج", "cancel")]
        ])
    )


# =========================
# DISCOUNT
# =========================

def discount(m, text):
    uid = str(m.chat_id)
    with pending_lock:
        o = PENDING_ORDERS.get(uid)

    if not o:
        return

    entered = text.strip()
    if entered.lower() != CODE.lower() and not coupon_apply(entered, num(o.get("price", 0))):
        set_stage(uid, "discount")
        send(
            uid,
            "❌ کد تخفیف نامعتبر است.",
            kb([
                [("❌ ندارم", "no_discount")],
                [("🔙 بازگشت به مرحله قبل", "back")],
                [("❌ خروج", "cancel")]
            ])
        )
        return

    price = num(o["price"])
    if entered.lower() == CODE.lower():
        off = price * 20 // 100
        with pending_lock:
            o["coupon_code"] = CODE
    else:
        result = coupon_apply(entered, price)
        if not result:
            set_stage(uid, "discount")
            send(uid, "❌ کد تخفیف نامعتبر است.", MAIN)
            return
        off = result[0]
        with pending_lock:
            o["coupon_code"] = entered.upper()
        mark_coupon_used(entered)

    with pending_lock:
        o["discount"] = off
        o["final"] = price - off
        o["discount_wait"] = 0

    payment(uid, o)

# =========================
# MEDIA
# =========================

def is_media(m):

    return bool(
        getattr(m, "file", None)
        or getattr(m, "photo", None)
        or getattr(m, "image", None)
    )


# =========================
# RECEIPT
# =========================

def receipt(m):
    uid = str(getattr(m, "chat_id", "") or "")
    message_id = str(getattr(m, "message_id", "") or getattr(m, "id", "") or "")
    with pending_lock:
        o = PENDING_ORDERS.get(uid)
    if not o:
        send(uid, "❌ سفارش در حال پرداختی ندارید.", MAIN)
        return

    with pending_lock:
        if o.get("receipt_processing"):
            send(uid, "⏳ رسید شما در حال بررسی است.")
            return
        o["receipt_processing"] = 1

    path = f"{BASE}/receipt_{o['id']}_{uid}.jpg"
    try:
        # استخراج file id از همه ساختارهای رایج پیام
        fid = None
        for attr in ("file", "photo", "image", "sticker"):
            obj = getattr(m, attr, None)
            if obj is None:
                continue
            if isinstance(obj, (list, tuple)):
                for x in reversed(obj):
                    fid = getattr(x, "id", None) or getattr(x, "file_id", None)
                    if fid:
                        break
            elif isinstance(obj, dict):
                fid = obj.get("id") or obj.get("file_id")
            else:
                fid = getattr(obj, "id", None) or getattr(obj, "file_id", None)
            if fid:
                break
        if not fid:
            raise Exception("FILE_ID_NOT_FOUND")

        file_url = bot.get_file(str(fid))
        if not file_url:
            raise Exception("GET_FILE_FAILED")
        data = bot.download_file(file_url)
        if isinstance(data, str) and os.path.isfile(data):
            with open(data, "rb") as src:
                data = src.read()
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise Exception("DOWNLOAD_FAILED")
        with open(path, "wb") as fp:
            fp.write(data)

        check = receipt_security_check(uid, o, bytes(data))
        fp_hash = check["fingerprint"]

        if check["duplicate"]:
            admin_send(admin_receipt_alert(o, check), ADMIN_KB)
            with pending_lock:
                o["receipt_processing"] = 0
            send(uid, receipt_duplicate_message(uid, check), MAIN)
            risk_level(uid)
            audit_event("duplicate_receipt", "system", uid, o.get("id"), note="; ".join(check.get("reason") or []))
            return

        RECEIPT_RECORDS[fp_hash] = {
            "uid": uid, "order_id": o.get("id"), "time": int(time.time()),
            "amount": num(o.get("final", 0)), "size": len(data),
            "duplicate": False, "other_user": False,
            "suspicious": bool(check.get("suspicious")), "fingerprint": fp_hash,
            "file_path": path
        }
        _ultra_save(RECEIPTS_FILE, RECEIPT_RECORDS)

        with pending_lock:
            o["receipt_fingerprint"] = fp_hash
            o["receipt_size"] = len(data)
            o["receipt_received_at"] = int(time.time())
            o["receipt_suspicious"] = bool(check.get("suspicious"))
            o["wallet_before"] = wallet(uid)
            o["wallet_after"] = wallet(uid)

        caption = (
            f"🛒 خرید جدید #{o['id']}\n"
            f"👤 آیدی کاربر: {uid}\n"
            f"👤 یوزرنیم: {o.get('username','ندارد')}\n"
            f"🛍 {o['service']} | 📌 {o['type']}\n"
            f"🔗 {o['target']}\n\n"
            f"💰 قیمت پایه: {money(o.get('price',0))} تومان\n"
            f"🎁 تخفیف کل: {money(o.get('discount',0))} تومان\n"
            f"🎟 کد تخفیف: {o.get('coupon_code') or 'ندارد'}\n"
            f"👥 معرفی: {'بله' if o.get('referral_discount_applied') else 'خیر'}\n"
            f"💵 مبلغ نهایی/پرداخت: {money(o.get('final',0))} تومان\n"
            f"💳 روش: کارت + رسید\n"
            f"🧬 شناسه رسید: {fp_hash[:24]}...\n"
            f"⚠️ بررسی خودکار: {'نیازمند بررسی بیشتر' if check.get('suspicious') else 'بدون هشدار ظاهری'}"
        )

        # PyRubikaBotAPI طبق نمونه رسمی، فایل را به صورت BufferedReader می‌پذیرد.
        # اگر آپلود رسانه برای یک مقصد خطا بدهد، خود پیام رسید را forward می‌کنیم.
        delivered = []
        failed = []
        for admin in sorted(ADMINS):
            ok = False
            errors = []

            # روش اصلی: مسیر واقعی فایل. طبق مستندات رسمی کتابخانه، send_photo
            # مسیر فایل را می‌پذیرد و از باز کردن دستی فایل مطمئن‌تر است.
            try:
                result = bot.send_photo(str(admin), path, text=caption)
                if _api_send_ok(result):
                    ok = True
                    print("RECEIPT_PHOTO_PATH_OK:", admin)
                else:
                    errors.append("PHOTO_PATH_RESULT=" + repr(result))
            except Exception as e:
                errors.append("PHOTO_PATH=" + repr(e))

            # fallback 1: BufferedReader
            if not ok:
                try:
                    with open(path, "rb") as media:
                        result = bot.send_photo(str(admin), media, text=caption)
                    if _api_send_ok(result):
                        ok = True
                        print("RECEIPT_PHOTO_FILE_OK:", admin)
                    else:
                        errors.append("PHOTO_FILE_RESULT=" + repr(result))
                except Exception as e:
                    errors.append("PHOTO_FILE=" + repr(e))

            # fallback 2: send_file با مسیر واقعی
            if not ok:
                try:
                    result = bot.send_file(str(admin), path, text=caption)
                    if _api_send_ok(result):
                        ok = True
                        print("RECEIPT_SEND_FILE_PATH_OK:", admin)
                    else:
                        errors.append("SEND_FILE_PATH_RESULT=" + repr(result))
                except Exception as e:
                    errors.append("SEND_FILE_PATH=" + repr(e))

            # fallback 3: send_file با BufferedReader
            if not ok:
                try:
                    with open(path, "rb") as media:
                        result = bot.send_file(str(admin), media, text=caption)
                    if _api_send_ok(result):
                        ok = True
                        print("RECEIPT_SEND_FILE_OK:", admin)
                    else:
                        errors.append("SEND_FILE_RESULT=" + repr(result))
                except Exception as e:
                    errors.append("SEND_FILE=" + repr(e))

            if not ok and message_id:
                try:
                    result = bot.forward(uid, str(admin), message_id)
                    if _api_send_ok(result):
                        ok = True
                        print("RECEIPT_FORWARD_OK:", admin)
                except Exception as e:
                    errors.append("FORWARD=" + repr(e))

            if ok:
                delivered.append(str(admin))
            else:
                failed.append(str(admin))
                print("RECEIPT_ADMIN_FAIL:", admin, " | ".join(errors))

        # حتی اگر یک ادمین دسترسی رسانه نداشته باشد، سفارش نباید از بین برود.
        # ادمین‌های موفق رسید را دریافت کرده‌اند و ادمین ناموفق حداقل اطلاعات سفارش را می‌گیرد.
        if failed:
            for admin in failed:
                send(str(admin), "⚠️ تحویل عکس رسید به این حساب ناموفق بود؛ سفارش ذخیره شد.\n\n" + caption, ADMIN_KB)

        if not delivered and not failed:
            raise Exception("NO_ADMIN_CONFIGURED")
        if not delivered:
            # برای کاربر خطای فنی نشان نده؛ سفارش ذخیره می‌شود و مدیریت از لاگ/رسید ذخیره‌شده قابل پیگیری است.
            raise Exception("SEND_RECEIPT_FAILED")

        with orders_lock:
            ids = [oid(x) for x in ORDERS.values()]
            if any(oid(x) == o["id"] for x in ORDERS.values()):
                o["id"] = max(ids + [1000]) + 1
            o["receipt"] = 1
            o["receipt_processing"] = 0
            o["waiting"] = 0
            o["discount_wait"] = 0
            o["status"] = "در انتظار بررسی"
            o["receipt_admins_delivered"] = delivered
            o["receipt_admins_failed"] = failed
            ORDERS[str(o["id"])] = dict(o)
        save_orders()

        # در طرح جدید هیچ تخفیف معرفی هنگام ثبت رسید اعمال نمی‌شود.
        # شارژ ۱۵٪ هر دو طرف فقط بعد از «تکمیل شد» توسط ادمین انجام می‌شود.

        with pending_lock:
            PENDING_ORDERS.pop(uid, None)
        set_stage(uid, "main")
        audit_event(
            "receipt_received", "system", uid, o.get("id"),
            after={"amount": num(o.get("final", 0)), "fingerprint": fp_hash[:24]},
            note="رسید برای حداقل یک ادمین ارسال شد"
        )
        if check.get("suspicious"):
            admin_send(admin_receipt_alert(o, check), ADMIN_KB)
        else:
            admin_send("🟢 رسید جدید دریافت شد. برای حساب کامل: «حساب سفارش #%s»" % o.get("id"), ADMIN_KB)
        send(
            uid,
            f"✅ رسید دریافت شد.\n\n🆔 سفارش: #{o['id']}\n💵 مبلغ پرداختی: {money(o['final'])} تومان\n"
            f"🎁 تخفیف: {money(o.get('discount',0))} تومان\n\n⏳ سفارش برای بررسی مدیریت ارسال شد.",
            MAIN
        )

    except Exception as e:
        print("RECEIPT:", repr(e))
        with pending_lock:
            if uid in PENDING_ORDERS:
                PENDING_ORDERS[uid]["receipt_processing"] = 0
        send(uid, "❌ ارسال رسید ناموفق بود.\nلطفاً عکس را دوباره ارسال کنید.")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# =========================
# ADMIN
# =========================

def admin_summary():
    with orders_lock:
        counts = {
            "در انتظار بررسی": 0,
            "در حال انجام": 0,
            "تکمیل شد": 0,
            "لغو شد": 0
        }
        for o in ORDERS.values():
            s = o.get("status")
            if s in counts:
                counts[s] += 1

    return counts


def admin_stats_text():
    c = admin_summary()
    return (
        "📊 آمار ربات\n\n"
        f"👤 افراد استارت‌زده: {len(USERS):,}\n"
        f"🛒 خریدهای موفق: {STATS.get('successful_purchases', 0):,}\n"
        f"📋 جدید: {c['در انتظار بررسی']:,}\n"
        f"🔵 درحال انجام: {c['در حال انجام']:,}\n"
        f"🟢 تکمیل‌شده: {c['تکمیل شد']:,}\n"
        f"🔴 لغوشده: {c['لغو شد']:,}\n"
        f"📦 کل سفارش‌های ثبت‌شده: {sum(c.values()):,}"
    )


def _admin_order_text(o):
    return (
        f"#{o['id']} | {o['service']} | {o['type']}\n"
        f"🔗 {o.get('target') or '---'}\n"
        f"💰 {money(o.get('final', 0))} تومان | 👤 {o.get('username', 'ندارد')}\n"
        f"📊 {o.get('status', '---')}"
    )


def admin_list(status, admin_id):
    with orders_lock:
        orders = sorted(
            [o for o in ORDERS.values() if o.get("status") == status],
            key=oid, reverse=True
        )

    if not orders:
        send(admin_id, f"📦 سفارش‌ها\n\n📭 فعلاً سفارشی با وضعیت «{status}» ندارید.\nهر زمان سفارشی ثبت شود، اینجا نمایش داده می‌شود.", ADMIN_ORDERS_KB)
        return

    # عمداً فقط ۸ سفارش آخر با دکمه نمایش داده می‌شوند؛ ساخت ده‌ها ردیف
    # ReplyKeyboard باعث سنگینی پنل و پرش/اسکرول ناخواسته می‌شود.
    visible = orders[:8]
    lines = [f"📦 سفارش‌های «{status}»", f"📊 کل: {len(orders):,} | نمایش: {len(visible):,}", ""]
    for o in visible:
        lines.append(_admin_order_text(o))

    rows = []
    for o in visible:
        n = o.get("id")
        if status == "در انتظار بررسی":
            rows.append([(f"🔎 #{n}", f"order_detail:{n}"), (f"🔵 شروع #{n}", f"order_start:{n}")])
        elif status == "در حال انجام":
            rows.append([(f"🔎 #{n}", f"order_detail:{n}"), (f"🟢 تکمیل #{n}", f"order_done:{n}")])
        else:
            rows.append([(f"🔎 #{n}", f"order_detail:{n}")])
    rows.append([("🔄 بروزرسانی", "admin_refresh")])
    rows.append([("🔙 پنل مدیریت", "admin")])
    send(admin_id, "\n\n".join(lines)[:7000], kb(rows))

def change_status(order_id, status, admin_id):
    with orders_lock:
        o = next(
            (x for x in ORDERS.values() if str(x.get("id")) == str(order_id)),
            None
        )

        if not o:
            send(admin_id, f"❌ سفارش #{order_id} پیدا نشد.", ADMIN_KB)
            return

        old_status = o.get("status")
        o["status"] = status
        o["waiting"] = 0
        o["discount_wait"] = 0

        if status == "تکمیل شد" and old_status != "تکمیل شد":
            STATS["successful_purchases"] = int(STATS.get("successful_purchases", 0)) + 1
            save_stats()

        # سفارش کیف پولی در صورت لغو، فقط یک‌بار به‌صورت خودکار برگشت می‌خورد.
        if status == "لغو شد" and old_status != "لغو شد" and o.get("wallet_paid"):
            wallet_refund_for_order(o, admin_id)

    save_orders()

    commission_paid = False
    commission_amount = 0
    commission_reason = ""
    if status == "تکمیل شد" and old_status != "تکمیل شد":
        commission_paid, commission_reason, commission_amount = process_referral_commission(o, admin_id)
        if commission_paid:
            o["referral_commission_paid"] = commission_amount

    admin_log(f"status:{status}", order_id, admin_id)

    queue_send(
        o["chat_id"],
        f"📦 سفارش #{order_id}\n📊 وضعیت: {status}"
    )

    add_notification(o["chat_id"], f"وضعیت سفارش #{order_id}", f"وضعیت سفارش شما به «{status}» تغییر کرد.")

    commission_note = (
        f"\n💎 کش‌بک معرفی: {money(commission_amount)} تومان به هر دو طرف شارژ شد."
        if commission_paid else ""
    )
    send(admin_id, f"✅ سفارش #{order_id} → {status}{commission_note}", ADMIN_KB)

def admin_panel_text():
    c = admin_summary()
    return (
        "⚙️ پنل مدیریت پیشرفته\n\n"
        f"📋 جدید: {c['در انتظار بررسی']:,}\n"
        f"🔵 درحال انجام: {c['در حال انجام']:,}\n"
        f"🟢 تکمیل: {c['تکمیل شد']:,}\n"
        f"🔴 لغوشده: {c['لغو شد']:,}\n"
        f"👤 کاربران: {len(USERS):,}\n"
        f"🛒 خرید موفق: {STATS.get('successful_purchases', 0):,}\n\n"
        f"💳 موجودی کیف پول کاربران: {money(sum(wallet(u) for u in USERS))} تومان\n"
        f"👛 کاربران دارای موجودی: {sum(1 for u in USERS if wallet(u) > 0):,} نفر\n"
        f"🎟 کدهای تخفیف فعال: {sum(1 for c in COUPONS.values() if c.get('active', True)):,}\n\n"
        "از دکمه‌های زیر بخش موردنظر را انتخاب کنید."
    )


def admin_finance_text(today_only=False):
    now = time.time()
    total = 0
    count = 0
    completed = 0
    with orders_lock:
        values = list(ORDERS.values())
    for o in values:
        if today_only and now - int(o.get("created", 0)) > 86400:
            continue
        count += 1
        try:
            total += num(o.get("final", 0))
        except Exception:
            pass
        if o.get("status") == "تکمیل شد":
            completed += 1
    return (
        ("📈 گزارش امروز" if today_only else "💰 گزارش مالی") + "\n\n"
        f"📦 تعداد سفارش: {count:,}\n"
        f"💵 مجموع مبلغ سفارش‌ها: {money(total)} تومان\n"
        f"🟢 تکمیل‌شده: {completed:,}\n"
        f"💵 مبلغ سفارش‌های تکمیل‌شده تقریبی: "
        f"{money(sum(num(o.get('final', 0)) for o in values if o.get('status') == 'تکمیل شد'))} تومان"
    )


def admin_cleanup(admin_id):
    cutoff = int(time.time()) - 30 * 86400
    with orders_lock:
        old_cancelled = [
            k for k, o in ORDERS.items()
            if o.get("status") == "لغو شد"
            and int(o.get("created", 0)) < cutoff
        ]
        for k in old_cancelled:
            ORDERS.pop(k, None)
    save_orders()
    admin_log("cleanup", "", admin_id)
    send(
        admin_id,
        f"🧹 پاکسازی انجام شد.\n"
        f"🗑 سفارش‌های لغوشده قدیمی حذف‌شده: {len(old_cancelled):,}",
        ADMIN_KB
    )


def admin_search_order(admin_id, query):
    query = str(query).strip().lstrip("#")
    with orders_lock:
        found = [
            o for o in ORDERS.values()
            if query in str(o.get("id", ""))
            or query.lower() in str(o.get("target", "")).lower()
            or query.lower() in str(o.get("username", "")).lower()
        ]
    found = sorted(found, key=oid, reverse=True)[:20]
    if not found:
        send(admin_id, "🔎 موردی پیدا نشد.", ADMIN_KB)
        return
    msg = "🔎 نتیجه جستجو\n\n" + "\n\n".join(_admin_order_text(o) for o in found)
    send(admin_id, msg[:7000], ADMIN_KB)


def admin_search_user(admin_id, query):
    q = str(query).strip().lower()
    with orders_lock:
        found = [
            o for o in ORDERS.values()
            if q in str(o.get("chat_id", "")).lower()
            or q in str(o.get("username", "")).lower()
            or q in str(o.get("target", "")).lower()
        ]
    found = sorted(found, key=oid, reverse=True)[:30]
    if not found:
        send(admin_id, "👤 کاربری با این مشخصات پیدا نشد.", ADMIN_KB)
        return
    msg = (
        f"👤 نتیجه کاربر\n"
        f"📦 تعداد سفارش پیدا‌شده: {len(found):,}\n\n"
        + "\n\n".join(_admin_order_text(o) for o in found)
    )
    send(admin_id, msg[:7000], ADMIN_KB)


def admin_activity_text():
    if not ADMIN_LOG:
        return "🕓 هنوز فعالیتی ثبت نشده."
    lines = ["🕓 آخرین فعالیت‌های پنل\n"]
    for x in reversed(ADMIN_LOG[-50:]):
        tm = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(x.get("time", 0))))
        lines.append(
            f"{tm} | {x.get('action','-')} | "
            f"سفارش #{x.get('order_id','-')} | ادمین {x.get('admin_id','-')}"
        )
    return "\n".join(lines)


def admin_notes_text():
    if not ADMIN_NOTES:
        return "📝 یادداشتی ثبت نشده."
    lines = ["📝 یادداشت‌های سفارش\n"]
    for k, v in list(ADMIN_NOTES.items())[-50:]:
        lines.append(f"#{k}: {v}")
    return "\n".join(lines)


def admin_order_detail(admin_id, order_id):
    with orders_lock:
        o = next((x for x in ORDERS.values() if str(x.get("id")) == str(order_id)), None)
    if not o:
        send(admin_id, f"❌ سفارش #{order_id} پیدا نشد.", ADMIN_KB)
        return
    note = ADMIN_NOTES.get(str(order_id), "ندارد")
    msg = (
        "🔎 جزئیات سفارش\n\n"
        f"🆔 #{o.get('id')}\n"
        f"🛍 سرویس: {o.get('service')}\n"
        f"📌 نوع: {o.get('type')}\n"
        f"🔗 مقصد: {o.get('target')}\n"
        f"👤 کاربر: {o.get('username')}\n"
        f"💰 مبلغ: {money(o.get('final', 0))} تومان\n"
        f"📊 وضعیت: {o.get('status')}\n"
        f"🕐 زمان: {time.strftime('%Y-%m-%d %H:%M', time.localtime(int(o.get('created', 0))))}\n"
        f"📝 یادداشت: {note}"
    )
    send(admin_id, msg, admin_buttons(o))


def admin_note(admin_id, order_id, note):
    ADMIN_NOTES[str(order_id)] = str(note)[:500]
    save_admin_data()
    admin_log("note", order_id, admin_id)
    send(admin_id, f"✅ یادداشت سفارش #{order_id} ذخیره شد.", ADMIN_KB)


# =========================
# ADVANCED ADMIN HELPERS
# =========================

def admin_system_text():
    with state_lock:
        poll_age = time.monotonic() - last_poll_ok
        msg_age = time.monotonic() - last_message_at if last_message_at else 0
    return (
        "🛠 وضعیت سیستم\n\n"
        f"🟢 Poll آخر: {poll_age:.1f} ثانیه قبل\n"
        f"💬 آخرین پیام: {msg_age:.1f} ثانیه قبل\n"
        f"👥 کاربران ثبت‌شده: {len(USERS):,}\n"
        f"🚫 کاربران مسدود: {len(BLOCKED_USERS):,}\n"
        f"📦 سفارش‌ها: {len(ORDERS):,}\n"
        f"⚙️ حالت تعمیرات: {'روشن' if ADMIN_SETTINGS.get('maintenance') else 'خاموش'}\n"
        f"🧵 پردازشگر: {WORKERS} | ارسال: {SEND_WORKERS}\n"
        f"⚡ Poll delay: {EMPTY_POLL_DELAY}"
    )


def admin_users_text():
    recent = list(USERS)[-30:]
    return (
        "👥 کاربران\n\n"
        f"تعداد کل: {len(USERS):,}\n"
        f"مسدود: {len(BLOCKED_USERS):,}\n\n"
        + "\n".join(("🚫 " if x in BLOCKED_USERS else "👤 ") + str(x) for x in recent)
    )


def admin_backup(admin_id):
    try:
        backup = os.path.join(DATA_DIR, f"backup_{int(time.time())}.json")
        payload = {
            "orders": ORDERS,
            "users": sorted(USERS),
            "stats": STATS,
            "admin_log": ADMIN_LOG,
            "admin_notes": ADMIN_NOTES,
            "blocked_users": sorted(BLOCKED_USERS),
            "settings": ADMIN_SETTINGS,
            "wallets": WALLETS,
            "wallet_log": WALLET_LOG,
            "referral_commissions": REFERRAL_COMMISSION_LOG,
            "referrals": REFERRALS,
            "user_meta": USER_META,
            "notifications": NOTIFICATIONS,
            "favorites": FAVORITES,
            "points": USER_POINTS,
            "coupons": COUPONS,
            "announcement": ANNOUNCEMENT,
            "created": int(time.time())
        }
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            bot.send_file(str(admin_id), backup, text="💾 پشتیبان کامل ربات")
        except Exception:
            send(admin_id, f"💾 فایل پشتیبان ساخته شد:\n{backup}", ADMIN_KB)
        admin_log("backup", "", admin_id)
    except Exception as e:
        send(admin_id, f"❌ ساخت پشتیبان ناموفق بود:\n{e}", ADMIN_KB)


def admin_export(admin_id):
    try:
        path = os.path.join(DATA_DIR, f"orders_export_{int(time.time())}.txt")
        with orders_lock:
            values = sorted(ORDERS.values(), key=oid, reverse=True)
        with open(path, "w", encoding="utf-8") as f:
            for o in values:
                f.write(
                    f"#{o.get('id')} | {o.get('status')} | {o.get('service')} | "
                    f"{o.get('type')} | {o.get('target')} | {o.get('username')} | "
                    f"{money(o.get('final',0))} تومان | {o.get('chat_id')}\n"
                )
        try:
            bot.send_file(str(admin_id), path, text="📤 خروجی سفارش‌ها")
        except Exception:
            send(admin_id, f"📤 خروجی ساخته شد:\n{path}", ADMIN_KB)
        admin_log("export", "", admin_id)
    except Exception as e:
        send(admin_id, f"❌ خروجی ناموفق بود:\n{e}", ADMIN_KB)


def admin_bulk(admin_id, action):
    if action == "done_all":
        target = "در حال انجام"
        new_status = "تکمیل شد"
    elif action == "cancel_all":
        target = "در انتظار بررسی"
        new_status = "لغو شد"
    else:
        send(admin_id, "⚡ عملیات سریع نامعتبر است.", ADMIN_KB)
        return
    with orders_lock:
        ids = [str(o.get("id")) for o in ORDERS.values() if o.get("status") == target]
    for oid_ in ids:
        change_status(oid_, new_status, admin_id)
    admin_log(f"bulk:{new_status}", "", admin_id)
    send(admin_id, f"⚡ عملیات انجام شد. تعداد: {len(ids):,}", ADMIN_KB)


def toggle_block(admin_id, user_id):
    user_id = str(user_id).strip()
    if not user_id:
        send(admin_id, "🚫 آیدی کاربر را ارسال کنید.", ADMIN_KB)
        return
    if user_id in BLOCKED_USERS:
        BLOCKED_USERS.remove(user_id)
        result = "رفع مسدودی شد"
    else:
        BLOCKED_USERS.add(user_id)
        result = "مسدود شد"
    save_control_data()
    admin_log(f"block:{result}", "", admin_id)
    send(admin_id, f"🚫 کاربر {user_id} → {result}", ADMIN_KB)


def resolve_user_id(value):
    value = str(value or "").strip()
    raw = value.lstrip("@#").strip()
    if not raw:
        return ""
    if raw in USERS or raw in USER_META:
        return raw
    for uid, meta in USER_META.items():
        if isinstance(meta, dict) and str(meta.get("username") or "").lstrip("@").lower() == raw.lower():
            return str(uid)
    for o in ORDERS.values():
        if str(o.get("chat_id")) == raw:
            return raw
        if str(o.get("username") or "").lstrip("@").lower() == raw.lower():
            return str(o.get("chat_id"))
    return ""

def admin_message_user(admin_id, user_id, message):
    target = resolve_user_id(user_id)
    if not target or not message:
        send(admin_id, "✉️ ارسال پیام به کاربر\n\nآیدی یا یوزرنیم کاربر را وارد کنید.\nمثال:\nپیام 123456789 متن پیام\nیا:\nپیام @username متن پیام", ADMIN_USERS_KB)
        return
    ok = queue_send(target, "📩 پیام مدیریت\n\n" + message, MAIN)
    admin_log("message_user", "", admin_id)
    send(admin_id, ("✅ پیام با موفقیت ارسال شد." if ok else "⚠️ ارسال پیام ناموفق بود.") + f"\n\n👤 آیدی کاربر: {target}", ADMIN_USERS_KB)


# =========================
# ULTRA ADMIN CENTER
# =========================

def admin_user_profile(admin_id, user_id):
    uid = str(user_id).strip().lstrip("#")
    orders = get_user_orders(uid)
    total = sum(num(o.get("final", 0)) for o in orders)
    completed = sum(1 for o in orders if o.get("status") == "تکمیل شد")
    blocked = uid in BLOCKED_USERS
    last = last_order(uid)
    msg = (
        "👤 پروفایل کامل کاربر\n\n"
        f"🆔 آیدی: {uid}\n"
        f"💳 کیف پول: {money(wallet(uid))} تومان\n"
        f"⭐ امتیاز: {int(USER_POINTS.get(uid, 0) or 0):,}\n"
        f"📦 تعداد سفارش: {len(orders):,}\n"
        f"🟢 تکمیل‌شده: {completed:,}\n"
        f"💵 مجموع سفارش‌ها: {money(total)} تومان\n"
        f"🚫 وضعیت دسترسی: {'مسدود' if blocked else 'فعال'}\n"
        f"📌 آخرین سفارش: #{last.get('id')}" if last else "📌 آخرین سفارش: ندارد"
    )
    send(admin_id, msg, kb([[('💳 مدیریت کیف پول','wallet_admin'),('✉️ پیام کاربر','message_user')],[('🚫 مسدود/رفع','block_user'),('🔙 کاربران','users')],[('🏠 پنل مدیریت','admin')]]))

def admin_wallet_history(admin_id, user_id=None):
    rows = [x for x in WALLET_LOG if user_id is None or str(x.get('uid')) == str(user_id)]
    rows = list(reversed(rows[-80:]))
    if not rows:
        send(admin_id, "🧾 گردش کیف پولی ثبت نشده.", wallet_admin_kb()); return
    lines=["🧾 گردش کیف پول\n"]
    for x in rows:
        tm=time.strftime('%Y-%m-%d %H:%M', time.localtime(int(x.get('time',0))))
        sign='➕' if x.get('amount',0)>=0 else '➖'
        lines.append(f"{tm}\n👤 {x.get('uid')} | {sign}{money(abs(int(x.get('amount',0))))}\n💵 {money(x.get('before',0))} → {money(x.get('after',0))} | ادمین: {x.get('admin_id','-')}")
    send(admin_id,"\n\n".join(lines)[:7000],wallet_admin_kb())

def admin_referral_text():
    total = sum(int(x.get("amount", 0) or 0) for x in REFERRAL_COMMISSION_LOG if isinstance(x, dict))
    count = len(REFERRAL_COMMISSION_LOG)
    refs = sum(int(v.get("count", 0) or 0) for v in REFERRALS.values() if isinstance(v, dict))
    lines = [
        "💎 گزارش کمیسیون معرفی",
        "",
        f"👥 دعوت‌های ثبت‌شده: {refs:,}",
        f"💎 کمیسیون‌های پرداخت‌شده: {count:,}",
        f"💰 مجموع کمیسیون پرداختی: {money(total)} تومان",
        f"🎁 تخفیف خرید اول: {REFERRAL_DISCOUNT_PERCENT}%",
        f"📈 کمیسیون معرف: {REFERRAL_COMMISSION_PERCENT}%",
        "",
        "شرایط پرداخت:",
        "✅ کد معرف قبل از خرید ثبت شده باشد",
        "✅ سفارش، اولین خرید واقعی کاربر باشد",
        "✅ تخفیف معرفی روی همان سفارش ثبت شده باشد",
        "✅ پرداخت مستقیم کارت + رسید معتبر باشد",
        "✅ رسید توسط ادمین تأیید و سفارش تکمیل شود",
        "🚫 کد تخفیف معمولی قابل ترکیب نیست",
        "🚫 شارژ کیف پول، پاداش، کسر/شارژ دستی و پرداخت کیف پول کمیسیون نمی‌سازند",
        "🔒 هر خرید فقط یک‌بار کمیسیون می‌دهد.",
    ]
    return "\n".join(lines)


def admin_reports_text():
    now=time.time(); day=now-86400; week=now-7*86400
    with orders_lock: vals=list(ORDERS.values())
    def calc(since=0):
        arr=[o for o in vals if int(o.get('created',0))>=since]
        return len(arr),sum(num(o.get('final',0)) for o in arr),sum(1 for o in arr if o.get('status')=='تکمیل شد')
    d=calc(day); w=calc(week); allx=calc(0)
    return ("📊 گزارش مدیریتی\n\n"
            f"📅 امروز: {d[0]:,} سفارش | {money(d[1])} تومان | 🟢 {d[2]:,}\n"
            f"📆 ۷ روز اخیر: {w[0]:,} سفارش | {money(w[1])} تومان | 🟢 {w[2]:,}\n"
            f"📦 کل: {allx[0]:,} سفارش | {money(allx[1])} تومان | 🟢 {allx[2]:,}\n\n"
            f"👥 کاربران: {len(USERS):,}\n💳 مجموع اعتبار کیف پول: {money(sum(wallet(u) for u in USERS))} تومان\n"
            f"🎟 کد تخفیف: {len(COUPONS):,}\n🚫 مسدود: {len(BLOCKED_USERS):,}")

def admin_settings_text():
    return ("⚙️ تنظیمات فروشگاه\n\n"
            f"🛠 تعمیرات: {'روشن' if ADMIN_SETTINGS.get('maintenance') else 'خاموش'}\n"
            f"🛍 توقف خرید: {'روشن' if ADMIN_SETTINGS.get('shop_paused') else 'خاموش'}\n"
            f"📢 تأیید همگانی: {'روشن' if ADMIN_SETTINGS.get('broadcast_confirm') else 'خاموش'}\n\n"
            "برای تغییر هرکدام یکی از فرمان‌های زیر را ارسال کنید:\n"
            "تعمیرات روشن / تعمیرات خاموش\n"
            "خرید روشن / خرید خاموش\n"
            "تایید همگانی روشن / تایید همگانی خاموش")

def admin_toggle_setting(admin_id,text):
    t=str(text).strip()
    m=re.match(r'^تعمیرات\s+(روشن|خاموش)$',t)
    if m: ADMIN_SETTINGS['maintenance']=m.group(1)=='روشن'
    else:
        m=re.match(r'^خرید\s+(روشن|خاموش)$',t)
        if m: ADMIN_SETTINGS['shop_paused']=m.group(1)=='خاموش'
        else:
            m=re.match(r'^تایید همگانی\s+(روشن|خاموش)$',t)
            if not m: return False
            ADMIN_SETTINGS['broadcast_confirm']=m.group(1)=='روشن'
    save_control_data(); admin_log('setting', '', admin_id); send(admin_id,admin_settings_text(),ADMIN_SYSTEM_KB); return True

# =========================
# ADMIN WALLET / COUPON / ANNOUNCEMENT COMPATIBILITY
# =========================

def wallet_admin_kb():
    # تمام عملیات کیف پول فقط همین‌جا قرار دارند تا با حسابداری تکراری نشوند.
    return kb([
        [("👤 مشاهده موجودی", "wallet_lookup"), ("💰 دارای موجودی", "wallet_rich")],
        [("➕ شارژ حساب", "wallet_credit"), ("➖ کسر حساب", "wallet_debit")],
        [("✏️ تعیین موجودی", "wallet_set"), ("🧹 صفر کردن", "wallet_zero")],
        [("🧾 گردش کیف پول", "wallet_history")],
        [("🔙 پنل مدیریت", "admin")]
    ])

def _wallet_target_prompt(admin_id, mode):
    prompts = {
        "lookup": "👤 آیدی یا یوزرنیم کاربر را بفرستید تا موجودی و مشخصات کیف پول نمایش داده شود.",
        "credit": "➕ آیدی/یوزرنیم و مبلغ را بفرستید.\nمثال: @username 50000",
        "debit": "➖ آیدی/یوزرنیم و مبلغ را بفرستید.\nمثال: @username 50000",
        "set": "✏️ آیدی/یوزرنیم و موجودی جدید را بفرستید.\nمثال: @username 150000",
        "zero": "🧹 آیدی یا یوزرنیم کاربر را بفرستید تا کیف پول صفر شود."
    }
    set_stage(admin_id, f"wallet_{mode}")
    send(admin_id, prompts[mode], wallet_admin_kb())

def _wallet_user_card(uid):
    uid = str(uid)
    ref = REFERRALS.get(uid, {}) if isinstance(REFERRALS.get(uid), dict) else {}
    meta = USER_META.get(uid, {}) if isinstance(USER_META.get(uid), dict) else {}
    username = meta.get("username") or "ندارد"
    logs = [x for x in WALLET_LOG if str(x.get("uid")) == uid]
    total_in = sum(max(0, int(x.get("amount",0) or 0)) for x in logs)
    total_out = sum(max(0, -int(x.get("amount",0) or 0)) for x in logs)
    return ("💳 پرونده کیف پول\n\n"
            f"🆔 آیدی: {uid}\n"
            f"👤 یوزرنیم: @{str(username).lstrip('@') if username != 'ندارد' else 'ندارد'}\n"
            f"💰 موجودی: {money(wallet(uid))} تومان\n"
            f"➕ مجموع شارژ: {money(total_in)} تومان\n"
            f"➖ مجموع کسر/خرید: {money(total_out)} تومان\n"
            f"👥 معرف: {ref.get('invited_by') or 'ندارد'}\n"
            f"💎 کمیسیون دریافت‌شده: {money(ref.get('commission_total',0))} تومان")

def admin_wallets(admin_id):
    rows = sorted(((str(uid), int(amount or 0)) for uid, amount in WALLETS.items() if int(amount or 0) > 0), key=lambda x: x[1], reverse=True)
    if not rows:
        send(admin_id, "💳 کیف پول کاربران\n\n📭 هیچ کاربری موجودی مثبت ندارد.", wallet_admin_kb())
        return
    lines = ["💰 کاربران دارای موجودی\n", f"👥 تعداد: {len(rows):,}", ""]
    lines += [f"{i}. 👤 {uid} — 💰 {money(amount)} تومان" for i,(uid,amount) in enumerate(rows[:80],1)]
    send(admin_id, "\n".join(lines)[:7000], wallet_admin_kb())

def admin_wallet_command(admin_id, text):
    t = str(text or "").strip()
    if t in ("💳 کیف پول", "💳 مدیریت کیف پول", "💳 کیف پول‌ها", "💳 کیف پول کاربران", "wallet_admin"):
        send(admin_id, "💳 مدیریت کامل کیف پول\n\nاز دکمه‌ها استفاده کنید یا دستورات زیر را بفرستید:\n\n"
             "👤 مشاهده: موجودی #آیدی یا @username\n"
             "➕ شارژ: شارژ #آیدی مبلغ\n"
             "➖ کسر: کسر #آیدی مبلغ\n"
             "✏️ تعیین: موجودی #آیدی مبلغ\n"
             "🧹 صفر: صفر #آیدی", wallet_admin_kb())
        return True
    if t in ("💰 دارای موجودی", "wallet_rich"):
        admin_wallets(admin_id); return True
    if t in ("🧾 گردش کیف پول", "wallet_history"):
        admin_wallet_history(admin_id); return True
    # direct lookup
    m = re.match(r"^(?:موجودی|مشاهده موجودی)\s+#?([^\s]+)$", t, re.I)
    if m:
        target = resolve_user_id(m.group(1)) or m.group(1).lstrip('@#')
        send(admin_id, _wallet_user_card(target), wallet_admin_kb()); return True
    # interactive stages
    stage = USER_STAGE.get(str(admin_id), "")
    if stage in ("wallet_lookup", "wallet_credit", "wallet_debit", "wallet_set", "wallet_zero"):
        if t in ("❌ لغو", "لغو", "🔙 پنل مدیریت"):
            set_stage(admin_id, "main"); send(admin_id, "✅ عملیات کیف پول لغو شد.", ADMIN_KB); return True
        if stage == "wallet_lookup":
            target = resolve_user_id(t) or t.lstrip('@#')
            set_stage(admin_id, "main")
            send(admin_id, _wallet_user_card(target), wallet_admin_kb()); return True
        if stage == "wallet_zero":
            target = resolve_user_id(t) or t.lstrip('@#')
            before = wallet(target)
            if before <= 0:
                set_stage(admin_id,"main"); send(admin_id,f"ℹ️ کیف پول {target} از قبل صفر است.",wallet_admin_kb()); return True
            WALLETS[target]=0; wallet_log("admin_zero",target,-before,before,0,admin_id); save_extra_data(); audit_event("wallet_zero",admin_id,target)
            add_notification(target,"💳 کیف پول صفر شد", "موجودی کیف پول شما توسط مدیریت به صفر رسید.")
            set_stage(admin_id,"main"); send(admin_id,f"✅ کیف پول صفر شد.\n\n👤 {target}\n💰 0 تومان",wallet_admin_kb()); return True
        m = re.match(r"^#?([^\s]+)\s+(\d[\d,]*)$", t)
        if not m:
            send(admin_id,"❌ فرمت صحیح نیست.\nمثال: @username 50000",wallet_admin_kb()); return True
        target = resolve_user_id(m.group(1)) or m.group(1).lstrip('@#'); amount=num(m.group(2))
        if amount <= 0:
            send(admin_id,"❌ مبلغ باید بیشتر از صفر باشد.",wallet_admin_kb()); return True
        before=wallet(target)
        if stage=="wallet_credit":
            after=before+amount; action="admin_credit"; delta=amount; title="💰 کیف پول شارژ شد"; body=f"مبلغ {money(amount)} تومان به کیف پول شما اضافه شد.\nموجودی جدید: {money(after)} تومان"
        elif stage=="wallet_debit":
            if before < amount:
                send(admin_id,f"❌ موجودی کافی نیست.\n💰 موجودی فعلی: {money(before)} تومان",wallet_admin_kb()); return True
            after=before-amount; action="admin_debit"; delta=-amount; title="💳 از کیف پول کسر شد"; body=f"مبلغ {money(amount)} تومان از کیف پول شما کسر شد.\nموجودی جدید: {money(after)} تومان"
        else:
            after=amount; action="admin_set"; delta=after-before; title="💰 موجودی کیف پول تغییر کرد"; body=f"موجودی کیف پول شما توسط مدیریت به {money(after)} تومان تغییر کرد."
        WALLETS[target]=after; wallet_log(action,target,delta,before,after,admin_id); save_extra_data(); audit_event(action,admin_id,target,note=str(amount)); add_notification(target,title,body)
        set_stage(admin_id,"main")
        send(admin_id,f"✅ عملیات کیف پول انجام شد.\n\n👤 {target}\n💵 قبل: {money(before)} تومان\n💵 تغییر: {money(delta)} تومان\n💰 بعد: {money(after)} تومان",wallet_admin_kb())
        return True
    # legacy commands
    m = re.match(r"^شارژ\s+#?([^\s]+)\s+(\d[\d,]*)$", t)
    if m:
        return admin_wallet_command(admin_id, "➕ شارژ") if False else _wallet_legacy(admin_id, "credit", m.group(1), m.group(2))
    m = re.match(r"^کسر\s+#?([^\s]+)\s+(\d[\d,]*)$", t)
    if m: return _wallet_legacy(admin_id,"debit",m.group(1),m.group(2))
    m = re.match(r"^صفر\s+#?([^\s]+)$", t)
    if m: return _wallet_legacy(admin_id,"zero",m.group(1),0)
    m = re.match(r"^موجودی\s+#?([^\s]+)\s+(\d[\d,]*)$", t)
    if m: return _wallet_legacy(admin_id,"set",m.group(1),m.group(2))
    return False

def _wallet_legacy(admin_id, mode, value, amount):
    target=resolve_user_id(value) or str(value).lstrip('@#')
    before=wallet(target)
    if mode=="zero": after=0; delta=-before; action="admin_zero"
    elif mode=="credit":
        amount=num(amount)
        if amount<=0: send(admin_id,"❌ مبلغ نامعتبر است.",wallet_admin_kb()); return True
        after=before+amount; delta=amount; action="admin_credit"
    elif mode=="debit":
        amount=num(amount)
        if amount<=0 or before<amount: send(admin_id,f"❌ موجودی کافی نیست.\n💰 موجودی: {money(before)} تومان",wallet_admin_kb()); return True
        after=before-amount; delta=-amount; action="admin_debit"
    else:
        after=max(0,num(amount)); delta=after-before; action="admin_set"
    WALLETS[target]=after; wallet_log(action,target,delta,before,after,admin_id); save_extra_data(); audit_event(action,admin_id,target,note=str(amount))
    add_notification(target,"💳 کیف پول به‌روزرسانی شد",f"موجودی شما: {money(after)} تومان")
    send(admin_id,f"✅ انجام شد.\n👤 {target}\n💵 قبل: {money(before)}\n💵 بعد: {money(after)} تومان",wallet_admin_kb())
    return True

def admin_coupons(admin_id):
    if not COUPONS:
        send(admin_id, "🎟 کدهای تخفیف\n\n📭 هنوز کدی ساخته نشده است.\n\nفرمت ساخت:\nکدتخفیف CODE درصد 20 [تعداد]", ADMIN_MARKETING_KB)
        return
    lines=["🎟 کدهای تخفیف\n"]
    for code, c in COUPONS.items():
        if not isinstance(c, dict): continue
        lines.append(f"🎟 {code}\n📉 {int(c.get('percent',0))}% | استفاده: {int(c.get('uses',0)):,}/{int(c.get('limit',0)) or '∞'} | {'فعال' if c.get('active',True) else 'خاموش'}")
    lines.append("\n➕ ساخت: کدتخفیف CODE درصد 20 [limit]\n🟢 فعال: فعال CODE\n🔴 خاموش: خاموش CODE")
    send(admin_id, "\n\n".join(lines)[:7000], ADMIN_MARKETING_KB)


def admin_coupon_command(admin_id, text):
    t=str(text or "").strip()
    if t in ("🎟 کدهای تخفیف", "discount_admin"):
        admin_coupons(admin_id); return True
    m=re.match(r"^کدتخفیف\s+([A-Za-z0-9_-]{2,64})\s+درصد\s+(\d{1,3})(?:\s+(\d+))?$", t, re.I)
    if m:
        code=m.group(1).upper(); pct=max(0,min(100,int(m.group(2)))); limit=int(m.group(3) or 0)
        COUPONS[code]={"percent":pct,"limit":limit,"uses":0,"active":True,"created":int(time.time())}; save_extra_data()
        send(admin_id,f"✅ کد {code} ساخته شد.\n📉 تخفیف: {pct}%\n🔢 سقف استفاده: {limit or 'بدون سقف'}",ADMIN_MARKETING_KB); return True
    m=re.match(r"^(فعال|خاموش)\s+([A-Za-z0-9_-]{2,64})$",t,re.I)
    if m:
        code=m.group(2).upper()
        if code not in COUPONS: send(admin_id,"❌ کد پیدا نشد.",ADMIN_MARKETING_KB); return True
        COUPONS[code]["active"]=(m.group(1)=="فعال"); save_extra_data(); send(admin_id,f"✅ وضعیت {code} تغییر کرد.",ADMIN_MARKETING_KB); return True
    return False


def admin_announce(admin_id, message=None):
    global ANNOUNCEMENT
    if message is None:
        set_stage(admin_id, "admin_announcement")
        send(admin_id, "📣 اطلاعیه\n\nمتن اطلاعیه را ارسال کنید.\nبرای لغو: لغو اطلاعیه", ADMIN_MARKETING_KB)
        return
    text=str(message).strip()
    if not text:
        send(admin_id,"❌ متن اطلاعیه خالی است.",ADMIN_MARKETING_KB); return
    ANNOUNCEMENTS.append({"time":int(time.time()),"title":"اطلاعیه مدیریت","body":text})
    del ANNOUNCEMENTS[:-50]
    ANNOUNCEMENT=text
    save_extra_data(); admin_log("announcement", "", admin_id)
    send(admin_id,"✅ اطلاعیه ثبت شد.",ADMIN_MARKETING_KB)


# =========================
# ADMIN COMMANDS
# =========================

def admin_command(
    text,
    admin_id
):
    # کیف پول مدیریت: این دستورات فقط برای ادمین‌ها در این تابع پردازش می‌شوند.
    if admin_toggle_setting(admin_id, text):
        return True

    # مسیرهای اصلی جدید پنل ادمین؛ مستقل از callback قدیمی
    if text in ("👑 داشبورد", "📊 داشبورد مدیریتی", "👑 مرکز مدیریت"):
        send(admin_id, admin_dashboard_ultra(), ADMIN_KB)
        return True
    if text == "🛠 سیستم":
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB)
        return True
    if text == "🧪 تست سلامت":
        with state_lock:
            poll_age = time.monotonic() - last_poll_ok
        checks = [
            ("Polling", poll_age < 180),
            ("Orders", isinstance(ORDERS, dict)),
            ("Wallet", isinstance(WALLETS, dict)),
            ("معرفی", isinstance(REFERRALS, dict)),
            ("Receipts", isinstance(RECEIPT_RECORDS, dict)),
            ("گزارش فعالیت", isinstance(AUDIT_LOG, list)),
            ("Backup", os.path.isdir(DATA_DIR)),
        ]
        send(admin_id, "🧪 تست سلامت سیستم\n\n" + "\n".join(("🟢 " if ok else "🔴 ") + name for name, ok in checks), ADMIN_SYSTEM_KB)
        return True
    if text in ("🔄 بروزرسانی", "🔄 بروزرسانی پنل"):
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB)
        return True

    # کیف پول ادمین: مسیر اختصاصی و قبل از هر مسیر حسابداری/قدیمی.
    if text in ("💳 کیف پول", "💳 مدیریت کیف پول", "💳 کیف پول‌ها", "💳 کیف پول کاربران", "wallet_admin"):
        send(admin_id, "💳 مدیریت کامل کیف پول\n\nاز دکمه‌های زیر استفاده کنید.", wallet_admin_kb())
        return True
    if text in ("👤 مشاهده موجودی", "wallet_lookup"):
        _wallet_target_prompt(admin_id, "lookup"); return True
    if text in ("💰 دارای موجودی", "wallet_rich"):
        admin_wallets(admin_id); return True
    if text in ("➕ شارژ حساب", "➕ شارژ", "💰 شارژ حساب شخصی", "wallet_credit"):
        _wallet_target_prompt(admin_id, "credit"); return True
    if text in ("➖ کسر حساب", "➖ کسر", "wallet_debit"):
        _wallet_target_prompt(admin_id, "debit"); return True
    if text in ("✏️ تعیین موجودی", "wallet_set"):
        _wallet_target_prompt(admin_id, "set"); return True
    if text in ("🧹 صفر کردن", "wallet_zero"):
        _wallet_target_prompt(admin_id, "zero"); return True
    if text in ("🧾 گردش کیف پول", "🧾 گردش کلی", "wallet_history"):
        admin_wallet_history(admin_id); return True

    # ===== ADMIN MAIN/SECTION BUTTONS =====
    # Reply-keyboard clicks arrive as normal text messages.  The visible labels
    # must therefore be routed explicitly; callback ids alone are not enough.
    if text in ("👑 داشبورد", "👑 مرکز مدیریت", "📊 داشبورد مدیریتی"):
        send(admin_id, admin_dashboard_ultra(), ADMIN_KB); return True
    if text in ("📦 سفارش‌ها", "📦 سفارش ها", "📦 سفارشها", "admin_orders"):
        # The orders button opens NEW orders first, so a fresh paid receipt is visible immediately.
        admin_list("در انتظار بررسی", admin_id); return True
    if text in ("👥 用户", "👥 کاربران", "👥 خریداران", "👥 تمام خریداران", "ultra_buyers"):
        send(admin_id, admin_buyers_text(0), ADMIN_USERS_KB); return True
    if text in ("💰 حسابداری", "💰 حسابداری شفاف", "🧮 حسابداری", "ultra_finance"):
        send(admin_id, admin_finance_ultra(), ADMIN_FINANCE_KB); return True
    if text in ("🚨 هشدارها", "⚠️ هشدارها", "🚨 هشدارهای امنیتی", "ultra_alerts"):
        alerts=[]
        for r in RECEIPT_RECORDS.values():
            if isinstance(r,dict) and (r.get("duplicate") or r.get("suspicious")):
                alerts.append(r)
        if not alerts:
            send(admin_id, "🟢 هشدار فعالی ثبت نشده است.", ADMIN_KB)
        else:
            lines=["🚨 هشدارهای رسید\n"]
            for r in alerts[-50:][::-1]:
                kind = "مشترک" if r.get("other_user") else ("تکراری" if r.get("duplicate") else "مشکوک")
                lines.append(f"👤 {r.get('uid')} | 📦 #{r.get('order_id')} | {kind}")
            send(admin_id, "\n".join(lines)[:7000], ADMIN_KB)
        return True
    if text in ("📣 بازاریابی", "admin_marketing"):
        send(admin_id, "📣 مرکز بازاریابی\n\nمدیریت کد تخفیف، اطلاعیه، پیام مستقیم و ارسال همگانی.", ADMIN_MARKETING_KB); return True
    if text in ("🛠 سیستم", "admin_system"):
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB); return True
    if text in ("💾 پشتیبان", "💾 پشتیبان و خروجی", "admin_backup"):
        send(admin_id, "💾 مرکز پشتیبان و خروجی\n\nنسخه پشتیبان کامل یا خروجی سفارش‌ها را انتخاب کنید.", ADMIN_SYSTEM_KB); return True
    if text in ("🏠 اصلی",):
        set_stage(admin_id, "main")
        send(admin_id, "🏠 منوی اصلی", MAIN); return True

    # Legacy/secondary admin labels
    if text in ("👥 فهرست کاربران", "users", "admin_users"):
        send(admin_id, admin_users_text()[:7000], ADMIN_USERS_KB); return True
    if text in ("👤 پرونده کاربر", "👤 پروفایل کاربر", "user_profile_admin"):
        set_stage(admin_id, "admin_user_profile")
        send(admin_id, "👤 آیدی کاربر را ارسال کنید.", ADMIN_USERS_KB); return True
    if text in ("✉️ پیام کاربر", "message_user"):
        set_stage(admin_id, "admin_message_user")
        send(admin_id, "✉️ فرمت: پیام #USER متن پیام", ADMIN_MARKETING_KB); return True
    if text in ("🚫 مسدود / رفع مسدودی", "🚫 مسدود/رفع", "block_user"):
        set_stage(admin_id, "admin_block_user")
        send(admin_id, "🚫 آیدی کاربر را ارسال کنید.", ADMIN_USERS_KB); return True
    if text in ("📊 حسابداری کلی", "finance"):
        send(admin_id, admin_finance_ultra(), ADMIN_FINANCE_KB); return True
    if text in ("📅 حسابداری امروز", "today"):
        send(admin_id, admin_finance_ultra("today"), ADMIN_FINANCE_KB); return True
    if text in ("📆 حسابداری هفته", "finance_week"):
        send(admin_id, admin_finance_ultra("week"), ADMIN_FINANCE_KB); return True
    if text in ("📆 حسابداری ماه", "finance_month"):
        send(admin_id, admin_finance_ultra("month"), ADMIN_FINANCE_KB); return True
    if text in ("🎟 کدهای تخفیف", "discount_admin"):
        admin_coupons(admin_id); return True
    if text in ("💎 کمیسیون معرفی", "referral_admin"):
        send(admin_id, admin_referral_text(), ADMIN_FINANCE_KB); return True
    if text in ("📣 اطلاعیه", "announce_admin"):
        admin_announce(admin_id); return True
    if text in ("📢 ارسال همگانی", "broadcast"):
        set_stage(admin_id, "admin_broadcast")
        send(admin_id, "📢 متن پیام همگانی را ارسال کنید.\nبرای لغو: لغو همگانی", ADMIN_MARKETING_KB); return True
    if text in ("🔎 جستجو", "admin_search"):
        send(admin_id, "🔎 جستجو\n\nبرای سفارش: 🔎 جستجوی سفارش\nبرای کاربر: 👤 جستجوی کاربر", ADMIN_KB); return True
    if text in ("🔎 جستجوی سفارش", "order_search"):
        set_stage(admin_id, "admin_search_order")
        send(admin_id, "🔎 شماره سفارش، یوزرنیم یا مقصد را ارسال کنید.", ADMIN_KB); return True
    if text in ("👤 جستجوی کاربر", "user_search"):
        set_stage(admin_id, "admin_search_user")
        send(admin_id, "👤 آیدی، یوزرنیم یا مقصد کاربر را ارسال کنید.", ADMIN_USERS_KB); return True
    if text in ("📚 آموزش ادمین", "📚 توضیحات پنل", "admin_docs"):
        send(admin_id, admin_docs_text(), ADMIN_KB); return True
    if text in ("📊 گزارش‌ها", "admin_reports"):
        send(admin_id, admin_reports_text(), ADMIN_KB); return True
    if text in ("⚙️ تنظیمات فروشگاه", "settings"):
        send(admin_id, admin_settings_text(), ADMIN_SYSTEM_KB); return True
    if text in ("🛠 وضعیت سیستم", "system"):
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB); return True
    if text in ("🧪 تست سلامت", "🧪 تست کامل سیستم", "ultra_test"):
        with state_lock: poll_age=time.monotonic()-last_poll_ok
        checks=[("Polling",poll_age<180),("Orders",isinstance(ORDERS,dict)),("Wallet",isinstance(WALLETS,dict)),("Receipts",isinstance(RECEIPT_RECORDS,dict)),("Backup",os.path.isdir(DATA_DIR))]
        send(admin_id,"🧪 تست سلامت سیستم\n\n"+"\n".join(("🟢 " if ok else "🔴 ")+name for name,ok in checks),ADMIN_SYSTEM_KB); return True
    if text in ("🔐 امنیت", "security"):
        send(admin_id, "🔐 امنیت\n\nاز مسدود/رفع برای کنترل دسترسی و از فعالیت ادمین برای بررسی عملیات استفاده کنید.", ADMIN_SYSTEM_KB); return True
    if text in ("🕓 فعالیت ادمین", "activity"):
        send(admin_id, admin_activity_text()[:7000], ADMIN_SYSTEM_KB); return True
    if text in ("💾 پشتیبان", "backup"):
        admin_backup(admin_id); return True
    if text in ("📤 خروجی سفارش‌ها", "export"):
        admin_export(admin_id); return True
    if text in ("🧹 پاکسازی", "cleanup"):
        admin_cleanup(admin_id); return True


    if admin_wallet_command(admin_id, text): return True
    if admin_coupon_command(admin_id, text): return True
    m = re.match(r"^اطلاعیه\s+(.+)$", text, re.S)
    if m:
        admin_announce(admin_id, m.group(1)); return True

    # عملیات مدیریتی پیشرفته با ورودی متنی
    m = re.match(r"^🚫 (?:مسدود|رفع)\s+(.+)$", text)
    if m:
        toggle_block(admin_id, m.group(1))
        return True
    m = re.match(r"^✉️ پیام\s+#?([^\s]+)\s+(.+)$", text, re.S)
    if m:
        admin_message_user(admin_id, m.group(1), m.group(2))
        return True
    if text == "⚡ تکمیل همه درحال انجام":
        admin_bulk(admin_id, "done_all")
        return True
    if text == "⚡ لغو همه جدید":
        admin_bulk(admin_id, "cancel_all")
        return True
    if text == "🔧 روشن/خاموش تعمیرات":
        ADMIN_SETTINGS["maintenance"] = not bool(ADMIN_SETTINGS.get("maintenance"))
        save_control_data()
        admin_log(f"maintenance:{ADMIN_SETTINGS['maintenance']}", "", admin_id)
        send(admin_id, admin_system_text(), ADMIN_KB)
        return True

    if text in (
        "/admin",
        "admin",
        "پنل مدیریت",
        "🔙 پنل مدیریت",
        "⚙️ پنل مدیریت"
    ):

        send(
            admin_id,
            "⚙️ پنل مدیریت",
            ADMIN_KB
        )

        return True

    if text in ("📊 آمار", "📈 آمار"):
        send(admin_id, admin_stats_text(), ADMIN_KB)
        return True

    m = re.match(r"^🔎 #?(\d+)$", text)
    if m:
        admin_order_detail(admin_id, m.group(1)); return True
    m = re.match(r"^🔵 شروع #?(\d+)$", text)
    if m:
        change_status(m.group(1), "در حال انجام", admin_id); return True
    m = re.match(r"^🟢 تکمیل #?(\d+)$", text)
    if m:
        change_status(m.group(1), "تکمیل شد", admin_id); return True

    if text == "🕘 آخرین سفارش‌ها":
        with orders_lock:
            latest = sorted(ORDERS.values(), key=oid, reverse=True)[:15]
        if not latest:
            send(admin_id, "📭 هنوز سفارشی ثبت نشده.", ADMIN_KB)
        else:
            msg = "🕘 آخرین سفارش‌ها\n\n" + "\n\n".join(_admin_order_text(o) for o in latest)
            send(admin_id, msg[:7000], ADMIN_KB)
        return True

    if text == "🔄 بروزرسانی":
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB)
        return True

    if text == "📋 جدید":

        admin_list(
            "در انتظار بررسی",
            admin_id
        )

        return True

    if text in ("🔵 درحال انجام", "🔵 در حال انجام"):

        admin_list(
            "در حال انجام",
            admin_id
        )

        return True

    if text in ("🟢 تکمیل", "🟢 تکمیل‌شده", "🟢 تکمیل شده"):

        admin_list(
            "تکمیل شد",
            admin_id
        )

        return True

    if text in ("🔴 لغوشده", "🔴 لغو شده"):

        admin_list(
            "لغو شد",
            admin_id
        )

        return True

    if text == "🗑 حذف لغوشده":

        with orders_lock:

            keys = [
                k for k, o in ORDERS.items()
                if o.get("status") == "لغو شد"
            ]

            for k in keys:
                del ORDERS[k]

        save_orders()

        send(
            admin_id,
            f"🗑 {len(keys)} سفارش حذف شد.",
            ADMIN_KB
        )

        return True

    m = re.match(r"^🔎 سفارش\s*#(\d+)$", text)
    if m:
        admin_order_detail(admin_id, m.group(1))
        return True

    m = re.match(
        r"^🔵 شروع\s*#(\d+)$",
        text
    )

    if m:

        change_status(
            m.group(1),
            "در حال انجام",
            admin_id
        )

        return True

    m = re.match(
        r"^🟢 تکمیل\s*#(\d+)$",
        text
    )

    if m:

        change_status(
            m.group(1),
            "تکمیل شد",
            admin_id
        )

        return True

    m = re.match(
        r"^🔴 لغو\s*#(\d+)$",
        text
    )

    if m:

        change_status(
            m.group(1),
            "لغو شد",
            admin_id
        )

        return True

    if text == "⚙️ پنل مدیریت":
        send(admin_id, admin_panel_text(), ADMIN_KB)
        return True

    if text in ("📊 آمار", "📈 آمار"):
        send(admin_id, admin_stats_text(), ADMIN_KB)
        return True

    if text == "💰 گزارش مالی":
        send(admin_id, admin_finance_text(False), ADMIN_KB)
        return True

    if text == "📈 گزارش امروز":
        send(admin_id, admin_finance_text(True), ADMIN_KB)
        return True

    if text == "🕓 فعالیت ادمین":
        send(admin_id, admin_activity_text()[:7000], ADMIN_KB)
        return True

    if text == "📝 یادداشت‌ها":
        send(admin_id, admin_notes_text()[:7000], ADMIN_KB)
        return True

    if text == "👥 کاربران":
        send(admin_id, admin_users_text()[:7000], ADMIN_KB)
        return True

    if text == "🛠 وضعیت سیستم":
        send(admin_id, admin_system_text(), ADMIN_KB)
        return True

    if text == "📚 توضیحات پنل":
        send(admin_id, admin_docs_text(), ADMIN_KB)
        return True

    if text == "💾 پشتیبان":
        admin_backup(admin_id)
        return True

    if text == "📤 خروجی سفارش‌ها":
        admin_export(admin_id)
        return True

    if text == "🧾 گردش کیف پول":
        admin_wallet_history(admin_id); return True
    if text == "👤 پروفایل کاربر":
        set_stage(admin_id, "admin_user_profile")
        send(admin_id, "👤 آیدی کاربر را ارسال کنید.", ADMIN_USERS_KB); return True
    if text == "🔐 امنیت":
        send(admin_id, "🔐 امنیت\n\nاز مسدود/رفع برای کنترل دسترسی و از فعالیت ادمین برای بررسی عملیات استفاده کنید.", ADMIN_SYSTEM_KB); return True
    if text == "📊 آمار سفارش":
        send(admin_id, admin_stats_text(), ADMIN_ORDERS_KB); return True
    if text in ("💳 کیف پول‌ها", "💳 کیف پول کاربران"):
        admin_wallets(admin_id); return True
    if text == "🎟 کدهای تخفیف":
        admin_coupons(admin_id); return True
    if text == "📣 اطلاعیه":
        admin_announce(admin_id); return True
    if text == "🔧 تعمیرات":
        ADMIN_SETTINGS["maintenance"] = not bool(ADMIN_SETTINGS.get("maintenance")); save_control_data(); send(admin_id, admin_system_text(), ADMIN_KB); return True

    if text == "📢 ارسال همگانی":
        set_stage(admin_id, "admin_broadcast")
        send(admin_id, "📢 متن پیام همگانی را ارسال کنید.\nبرای لغو: لغو همگانی", ADMIN_KB)
        return True

    if text == "✉️ پیام کاربر":
        set_stage(admin_id, "admin_message_user")
        send(admin_id, "✉️ فرمت:\nپیام #USER متن پیام", ADMIN_KB)
        return True

    if text == "🚫 مسدود/رفع":
        set_stage(admin_id, "admin_block_user")
        send(admin_id, "🚫 آیدی کاربر را ارسال کنید. اگر مسدود است رفع می‌شود و برعکس.", ADMIN_KB)
        return True

    if text == "⚡ عملیات سریع":
        send(admin_id, "⚡ عملیات سریع\n\n⚠️ این عملیات گروهی است.\n\nبرای اجرا دقیقاً یکی از این‌ها را بفرست:\n⚡ تکمیل همه درحال انجام\n⚡ لغو همه جدید", ADMIN_KB)
        return True

    if text == "🧹 پاکسازی":
        admin_cleanup(admin_id)
        return True

    if text in ("🔄 بروزرسانی", "🔄 بروزرسانی پنل"):
        send(admin_id, admin_system_text(), ADMIN_SYSTEM_KB)
        return True

    m = re.match(r"^🔎 سفارش\s*#?(\d+)$", text)
    if m:
        admin_order_detail(admin_id, m.group(1))
        return True

    m = re.match(r"^📝 یادداشت\s*#?(\d+)\s+(.+)$", text, re.S)
    if m:
        admin_note(admin_id, m.group(1), m.group(2))
        return True

    if text.startswith("🔎 جستجوی سفارش"):
        send(admin_id, "🔎 شماره سفارش، یوزرنیم یا مقصد را ارسال کنید.\nمثال: 1001 یا @username")
        with state_lock:
            USER_STAGE[str(admin_id)] = "admin_search_order"
        return True

    if text.startswith("👤 جستجوی کاربر"):
        send(admin_id, "👤 آیدی، یوزرنیم یا مقصد کاربر را ارسال کنید.")
        with state_lock:
            USER_STAGE[str(admin_id)] = "admin_search_user"
        return True

    if text.startswith("📝 یادداشت"):
        send(admin_id, "📝 برای ثبت یادداشت این فرمت را بفرستید:\nیادداشت #1001 متن یادداشت")
        return True

    if text in ("💳 کیف پول", "💳 کیف پول‌ها"):
        send(admin_id, "💳 مدیریت کیف پول\n\n"
                 "برای شارژ دستی:\n"
                 "شارژ #آیدی مبلغ\n"
                 "مثال: شارژ #123456789 50000\n\n"
                 "برای صفر کردن:\n"
                 "صفر #آیدی\n"
                 "مثال: صفر #123456789\n\n"
                 "برای تعیین موجودی دقیق:\n"
                 "موجودی #آیدی مبلغ\n"
                 "مثال: موجودی #123456789 150000\n\n"
                 "برای کسر مبلغ:\n"
                 "کسر #آیدی مبلغ\n"
                 "مثال: کسر #123456789 20000\n\n"
                 "👤 آیدی می‌تواند شناسه عددی/متنی کاربر باشد.", ADMIN_KB)
        return True
    if text == "🎟 کدهای تخفیف": admin_coupons(admin_id); return True
    if text == "📣 اطلاعیه": admin_announce(admin_id); return True
    if text == "🔧 تعمیرات":
        ADMIN_SETTINGS["maintenance"] = not bool(ADMIN_SETTINGS.get("maintenance")); save_control_data(); send(admin_id, admin_system_text(), ADMIN_KB); return True

    return False



def user_profile_text(uid):
    orders = get_user_orders(uid)
    completed = sum(1 for o in orders if o.get("status") == "تکمیل شد")
    active = sum(1 for o in orders if o.get("status") == "در حال انجام")
    waiting = sum(1 for o in orders if o.get("status") == "در انتظار بررسی")
    return (
        "👤 حساب من\n\n"
        f"🆔 آیدی: {uid}\n"
        f"📦 کل سفارش‌ها: {len(orders):,}\n"
        f"🟢 تکمیل‌شده: {completed:,}\n"
        f"🔵 فعال: {active:,}\n"
        f"🕐 در انتظار: {waiting:,}\n"
        f"💰 کیف پول: {money(wallet_balance(uid))} تومان"
    )

def user_stats_text(uid):
    orders = get_user_orders(uid)
    total = sum(num(o.get("final", 0)) for o in orders)
    completed = sum(1 for o in orders if o.get("status") == "تکمیل شد")
    return (
        "📊 آمار من\n\n"
        f"📦 سفارش‌ها: {len(orders):,}\n"
        f"🟢 موفق: {completed:,}\n"
        f"💵 مجموع مبلغ سفارش‌ها: {money(total)} تومان\n"
        f"💰 موجودی کیف پول: {money(wallet_balance(uid))} تومان"
    )

def user_orders_text(uid):
    orders = sorted(get_user_orders(uid), key=oid, reverse=True)[:30]
    if not orders:
        return "🧾 سفارش‌های من\n\n📭 هنوز سفارشی ندارید."
    lines = ["🧾 سفارش‌های من\n"]
    for o in orders:
        lines.append(
            f"#{o.get('id')} | {o.get('service')} | {o.get('type')}\n"
            f"📊 {o.get('status')} | 💰 {money(o.get('final',0))} تومان\n"
            f"🔗 {o.get('target','---')}"
        )
    return "\n\n".join(lines)

def announcements_text():
    if not ANNOUNCEMENTS:
        return "📣 اطلاعیه‌ها\n\n📭 اطلاعیه‌ای منتشر نشده است."
    lines = ["📣 اطلاعیه‌های فروشگاه\n"]
    for a in reversed(ANNOUNCEMENTS[-20:]):
        tm = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(a.get("time",0))))
        lines.append(f"📌 {a.get('title','اطلاعیه')}\n{tm}\n{a.get('body','')}")
    return "\n\n".join(lines)

def favorites_text(uid):
    arr = favorites(uid)
    if not arr:
        return "📌 علاقه‌مندی‌ها\n\nهنوز مقصدی ذخیره نکرده‌اید."
    return "📌 علاقه‌مندی‌ها\n\n" + "\n".join(f"{i+1}. {x}" for i,x in enumerate(arr))

def wallet_history_text(uid):
    uid = str(uid)
    rows = [x for x in WALLET_LOG if str(x.get("uid")) == uid]
    rows = sorted(rows, key=lambda x: int(x.get("time", 0)), reverse=True)[:20]
    if not rows:
        return "💳 گردش کیف پول\n\n📭 هنوز تراکنشی ثبت نشده است."
    lines = ["💳 گردش کیف پول\n"]
    for x in rows:
        tm = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(x.get("time", 0))))
        action = x.get("action", "تغییر")
        lines.append(
            f"🕒 {tm}\n"
            f"🔹 {action} | 💵 {money(x.get('amount',0))} تومان\n"
            f"💰 {money(x.get('before',0))} ← {money(x.get('after',0))} تومان"
        )
    return "\n\n".join(lines)

def customer_points_text(uid):
    pts = int(USER_POINTS.get(str(uid), 0) or 0)
    if pts < 100:
        level, next_level = "🥉 تازه‌وارد", 100
    elif pts < 500:
        level, next_level = "🥈 فعال", 500
    elif pts < 1500:
        level, next_level = "🥇 حرفه‌ای", 1500
    else:
        level, next_level = "💎 ویژه", pts
    remain = max(0, next_level - pts)
    return (
        "⭐ امتیاز و سطح من\n\n"
        f"⭐ امتیاز: {pts:,}\n"
        f"🏆 سطح: {level}\n"
        + (f"📈 تا سطح بعدی: {remain:,} امتیاز" if remain else "🔥 شما در بالاترین سطح فعلی هستید.")
        + "\n\nℹ️ امتیازها در سیستم داخلی فروشگاه ثبت می‌شوند."
    )

def customer_referral_text(uid):
    uid = str(uid)
    code = referral_code_for(uid)
    ref = REFERRALS.get(uid, {}) if isinstance(REFERRALS, dict) else {}
    if not isinstance(ref, dict):
        ref = {}
    invited = int(ref.get("count", 0) or 0)
    commission_count = int(ref.get("commission_count", 0) or 0)
    commission_total = int(ref.get("commission_total", 0) or 0)
    owner = str(ref.get("invited_by") or "").strip()
    cashback_orders = int(ref.get("cashback_orders", 0) or 0)
    cashback_total = int(ref.get("cashback_total", 0) or 0)
    return (
        "👥 دعوت دوستان\n\n"
        f"🎟 کد معرف شما:\n{code}\n\n"
        f"👥 تعداد افراد دعوت‌شده: {invited:,} نفر\n"
        f"🛒 خریدهای موفق دعوت‌شده‌ها: {commission_count:,}\n"
        f"💎 درآمد معرفی شما: {money(commission_total)} تومان\n"
        f"🎁 کش‌بک خریدهای خودتان از طرح معرفی: {cashback_orders:,} سفارش\n"
        f"💰 مجموع کش‌بک خودتان: {money(cashback_total)} تومان\n\n"
        f"👤 معرف شما: {'هنوز ثبت نشده است' if not owner else owner}\n\n"
        "📌 کد معرف را فقط قبل از اولین خرید می‌توان ثبت کرد و بعد از ثبت، قابل تغییر نیست.\n"
        f"💎 بعد از تکمیل هر خرید موفقِ فرد دعوت‌شده، {REFERRAL_COMMISSION_PERCENT}% مبلغ خرید به کیف پول شما شارژ می‌شود.\n"
        f"🎁 هم‌زمان {REFERRAL_COMMISSION_PERCENT}% همان خرید به کیف پول خود فرد دعوت‌شده هم اضافه می‌شود.\n\n"
        "🛡 ضدتقلب: فقط کدهای واقعی تولیدشده توسط ربات معتبرند؛ کد جعلی ثبت نمی‌شود و هر حساب فقط یک معرف دارد."
    )

def customer_notifications_text(uid):
    return user_notifications_text(uid)

def customer_about_text():
    return (
        "ℹ️ درباره فروشگاه\n\n"
        "🛍 فروشگاه خدمات روبیکا\n"
        "⚡ ثبت سفارش سریع\n"
        "📦 پیگیری وضعیت سفارش\n"
        "💰 کیف پول داخلی\n"
        "🎁 استفاده از تخفیف‌ها\n"
        "🔔 اطلاع‌رسانی وضعیت سفارش\n\n"
        f"🆘 پشتیبانی: {SUPPORT}"
    )

def admin_docs_text():
    return """📚 راهنمای پنل مدیریت

📦 سفارش‌ها
همه سفارش‌ها بر اساس وضعیت در یک بخش هستند:
📋 جدید، 🔵 در حال انجام، 🟢 تکمیل‌شده و 🔴 لغوشده.
با انتخاب سفارش، جزئیات آن و دکمه‌های مدیریت نمایش داده می‌شود.

👥 کاربران
فهرست کاربران، پرونده کاربر، مسدود/رفع مسدودی و ارسال پیام مستقیم در این بخش است.

✉️ پیام کاربر
لازم نیست یوزرنیم را از جای دیگری پیدا کنید؛ آیدی یا یوزرنیم ثبت‌شده کاربر را وارد کنید.
مثال:
پیام 123456789 سلام، سفارش شما بررسی شد
یا:
پیام @username سلام، سفارش شما بررسی شد

💰 حسابداری
گزارش مالی کلی، امروز، هفته و ماه در همین بخش قرار دارد.

💳 کیف پول
شارژ: شارژ #آیدی مبلغ
مثال: شارژ #123456789 50000

تعیین موجودی: موجودی #آیدی مبلغ
کسر: کسر #آیدی مبلغ
صفر کردن: صفر #آیدی

📣 بازاریابی
کدهای تخفیف، اطلاعیه، ارسال همگانی و پیام مستقیم.

🛠 سیستم
وضعیت سیستم، تنظیمات، امنیت، فعالیت ادمین، پشتیبان، خروجی و تست سلامت.

💾 پشتیبان و خروجی
پشتیبان کامل اطلاعات ساخته می‌شود و خروجی سفارش‌ها به صورت فایل ارسال می‌شود.

⚠️ عملیات حساس مثل حذف، لغو گروهی و تغییر کیف پول را با دقت انجام دهید."""


# =========================
# CUSTOMER HELP / SERVICE INFO
# =========================

def docs_text():
    return """📚 آموزش کامل ربات

🏠 1) شروع کار
با /start یا دکمه «🏠 اصلی» وارد منوی اصلی شوید. همه امکانات از همین منو در دسترس است.

🛍 2) خرید و ثبت سفارش
1️⃣ روی «🛍 خرید» بزنید.
2️⃣ سرویس را انتخاب کنید: کانال، گروه یا روبینو.
3️⃣ توضیحات همان سرویس را بخوانید و تعرفه را انتخاب کنید.
4️⃣ یوزرنیم یا لینک مقصد را دقیق ارسال کنید.
5️⃣ مبلغ نهایی را بررسی کنید.
6️⃣ در صورت داشتن کد تخفیف، آن را ثبت کنید.
7️⃣ با کارت یا کیف پول پرداخت کنید.
8️⃣ اگر کارت را انتخاب کردید، رسید را به صورت عکس ارسال کنید.
9️⃣ شماره سفارش را نگه دارید و وضعیت را از «📦 پیگیری سفارش» ببینید.

📣 3) افزایش کانال
«🛍 خرید» ← «📣 افزایش کانال» ← انتخاب تعرفه ← ارسال @username یا لینک کانال ← پرداخت.

👥 4) افزایش گروه
«🛍 خرید» ← «👥 افزایش گروه» ← انتخاب تعرفه ← ارسال @username یا لینک گروه ← پرداخت.

⭐ 5) افزایش روبینو
«🛍 خرید» ← «⭐ افزایش روبینو» ← انتخاب تعرفه ← ارسال یوزرنیم/لینک صفحه ← پرداخت.

🎟 6) کد دعوت چطور کار می‌کند؟
• برای دیدن کد شخصی خودتان: «👥 دعوت دوستان».
• کد را برای دوستتان ارسال کنید.
• دوستتان از «🎟 ثبت کد دعوت» کد را وارد می‌کند.
• کد دعوت باید قبل از خرید ثبت شود تا شرایط طرح بررسی شود.
• طبق تنظیم فعلی، خرید اولِ واجدشرایط شامل ۱۵٪ تخفیف معرفی و ۱۵٪ کمیسیون برای معرف است.
• ثبت کد دعوت به‌تنهایی شارژ نقدی ایجاد نمی‌کند.

🎁 7) کد تخفیف\اگر کد تخفیف دارید، هنگام خرید در مرحله «کد تخفیف دارید؟» آن را وارد کنید. هر کد می‌تواند درصد، سقف استفاده یا وضعیت فعال/غیرفعال داشته باشد.

💰 8) کیف پول\از «💰 کیف پول» موجودی و گردش حساب را ببینید. موجودی قابل استفاده برای پرداخت سفارش است. شارژ کیف پول در نسخه فعلی توسط مدیریت انجام می‌شود.

📦 9) سفارش‌ها\«🧾 سفارش‌های من» همه سفارش‌ها را نشان می‌دهد و «📦 سفارش‌های فعال» برای پیگیری سفارش‌های در انتظار یا در حال انجام است.

🔔 10) اعلان‌ها\تغییر وضعیت سفارش و پیام‌های مهم حساب در «🔔 اعلان‌های من» نمایش داده می‌شود.

🆘 11) پشتیبانی\اگر در ثبت سفارش، پرداخت یا پیگیری مشکل داشتید، از «🆘 پشتیبانی» با مدیریت در ارتباط شوید.

📜 12) قوانین مهم\• مقصد را قبل از پرداخت بررسی کنید.
• رسید پرداخت باید مربوط به همان سفارش باشد.
• رسید تکراری یا مشکوک ممکن است برای بررسی دستی علامت‌گذاری شود.
• وضعیت نهایی سفارش توسط مدیریت ثبت می‌شود.

❓ اگر هر مرحله برایتان واضح نیست، از «❓ راهنمای خرید» استفاده کنید."""

def customer_service_info_text():
    return """🛍 راهنمای کامل خرید و خدمات

قبل از ثبت سفارش این راهنما را بخوانید. هر سرویس روند و توضیحات مخصوص خودش را دارد.

📣 افزایش کانال
• مناسب برای افزایش عددی کانال
• ابتدا تعرفه را انتخاب کنید.
• سپس @username یا لینک کانال را ارسال کنید.
• مبلغ نهایی قبل از پرداخت نمایش داده می‌شود.

👥 افزایش گروه
• مناسب برای افزایش عددی گروه
• تعرفه موردنظر را انتخاب کنید.
• سپس @username یا لینک گروه را ارسال کنید.
• قبل از پرداخت، مقصد و مبلغ را دوباره بررسی کنید.

⭐ افزایش روبینو
• مناسب برای افزایش فالور صفحه روبینو
• تعرفه موردنظر را انتخاب کنید.
• سپس یوزرنیم/لینک صفحه را ارسال کنید.
• مبلغ نهایی و روش پرداخت قبل از ارسال رسید نمایش داده می‌شود.

💳 روش‌های پرداخت
1️⃣ کارت: مبلغ را به کارت اعلام‌شده واریز کنید و رسید را به صورت عکس بفرستید.
2️⃣ کیف پول: اگر موجودی کافی باشد، مبلغ از کیف پول کسر و سفارش ثبت می‌شود.

🎁 تخفیف و کد دعوت
• اگر کد تخفیف دارید، در مرحله مربوط به تخفیف وارد کنید.
• اگر با کد معرف وارد شده باشید و شرایط طرح را داشته باشید، تخفیف معرفی به صورت خودکار محاسبه می‌شود.
• کد دعوت را از «👥 دعوت دوستان» بگیرید و برای دوستتان ارسال کنید؛ دوستتان آن را از «🎟 ثبت کد دعوت» ثبت می‌کند.

📦 بعد از ثبت سفارش
سفارش وارد مدیریت می‌شود و وضعیت آن از بخش «📦 سفارش‌ها» و «📦 پیگیری سفارش» قابل مشاهده است.

⚠️ نکته مهم
قبل از پرداخت، یوزرنیم یا لینک مقصد را دقیق بررسی کنید. در صورت ارسال مقصد اشتباه، مسئولیت ثبت مقصد با سفارش‌دهنده است."""

def customer_help_text():
    return """❓ راهنمای خرید

1️⃣ سرویس موردنظر را انتخاب کنید.
2️⃣ مقدار سفارش را انتخاب کنید.
3️⃣ یوزرنیم یا لینک مقصد را وارد کنید.
4️⃣ مبلغ نهایی را بررسی کنید.
5️⃣ با کیف پول یا کارت پرداخت کنید.
6️⃣ در پرداخت کارت، رسید را به صورت عکس ارسال کنید.
7️⃣ وضعیت سفارش را از بخش پیگیری سفارش ببینید.

⚠️ قبل از پرداخت، مقصد را دوباره بررسی کنید."""

# =========================
# ULTRA MAX FINANCE / CRM / RECEIPT SECURITY
# =========================
RECEIPTS_FILE = os.path.join(DATA_DIR, "receipts.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_log.json")
USER_TAGS_FILE = os.path.join(DATA_DIR, "user_tags.json")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
CAMPAIGNS_FILE = os.path.join(DATA_DIR, "campaigns.json")
RISK_FILE = os.path.join(DATA_DIR, "risk_flags.json")

RECEIPT_RECORDS = _load_json_file(RECEIPTS_FILE, {})
AUDIT_LOG = _load_json_file(AUDIT_FILE, [])
USER_TAGS = _load_json_file(USER_TAGS_FILE, {})
TICKETS = _load_json_file(TICKETS_FILE, {})
CAMPAIGNS = _load_json_file(CAMPAIGNS_FILE, {})
RISK_FLAGS = _load_json_file(RISK_FILE, {})

_ultra_lock = Lock()


def _ultra_save(path, value):
    try:
        write(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print("ULTRA_SAVE:", repr(e))


def audit_event(action, admin_id="", uid="", order_id="", before=None, after=None, note=""):
    row = {
        "time": int(time.time()), "action": str(action), "admin_id": str(admin_id),
        "uid": str(uid), "order_id": str(order_id), "before": before,
        "after": after, "note": str(note)[:1000]
    }
    with _ultra_lock:
        AUDIT_LOG.append(row)
        del AUDIT_LOG[:-5000]
    _ultra_save(AUDIT_FILE, AUDIT_LOG)


def remember_user_message(m):
    uid = str(getattr(m, "chat_id", "") or "")
    if not uid:
        return
    meta = USER_META.setdefault(uid, {})
    meta.setdefault("first_seen", int(time.time()))
    meta["last_seen"] = int(time.time())
    sid = str(getattr(m, "sender_id", "") or "")
    if sid:
        meta["sender_id"] = sid
    for attr, key in (("first_name", "first_name"), ("last_name", "last_name"),
                      ("name", "name"), ("username", "username"), ("phone", "phone")):
        val = getattr(m, attr, None)
        if val:
            meta[key] = str(val)
    # اطلاعات سبک و کم‌هزینه؛ از API اضافی در هر پیام استفاده نمی‌شود.
    _ultra_save(USER_META_FILE, USER_META)


def user_referral_info(uid):
    r = REFERRALS.get(str(uid), {})
    if not isinstance(r, dict):
        return {}, "ندارد"
    owner = str(r.get("invited_by") or "")
    return r, owner or "ندارد"


def user_financial_totals(uid):
    orders = get_user_orders(uid)
    base = sum(num(o.get("price", 0)) for o in orders)
    discounts = sum(num(o.get("discount", 0)) for o in orders)
    final = sum(num(o.get("final", 0)) for o in orders)
    wallet_paid = sum(num(o.get("final", 0)) for o in orders if o.get("wallet_paid"))
    card_paid = sum(num(o.get("final", 0)) for o in orders if o.get("payment_method") == "card_receipt" and o.get("receipt"))
    refund = sum(num(o.get("final", 0)) for o in orders if o.get("wallet_refunded"))
    referral_comm = sum(num(x.get("amount", 0)) for x in REFERRAL_COMMISSION_LOG if str(x.get("referrer")) == str(uid))
    return base, discounts, final, wallet_paid, card_paid, refund, referral_comm


def customer_full_profile(uid):
    uid = str(uid)
    orders = sorted(get_user_orders(uid), key=oid, reverse=True)
    meta = USER_META.get(uid, {}) if isinstance(USER_META.get(uid, {}), dict) else {}
    ref, owner = user_referral_info(uid)
    base, discounts, final, wallet_paid, card_paid, refund, referral_comm = user_financial_totals(uid)
    completed = sum(1 for o in orders if o.get("status") == "تکمیل شد")
    active = sum(1 for o in orders if o.get("status") == "در حال انجام")
    waiting = sum(1 for o in orders if o.get("status") == "در انتظار بررسی")
    cancelled = sum(1 for o in orders if o.get("status") == "لغو شد")
    tags = USER_TAGS.get(uid, []) if isinstance(USER_TAGS.get(uid, []), list) else []
    risk = RISK_FLAGS.get(uid, {}) if isinstance(RISK_FLAGS.get(uid, {}), dict) else {}
    code = str(ref.get("code") or referral_code_for(uid))
    first = meta.get("first_seen") or 0
    last = meta.get("last_seen") or first
    fs = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(first))) if first else "---"
    ls = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(last))) if last else "---"
    name = " ".join(str(meta.get(k, "")).strip() for k in ("first_name", "last_name") if meta.get(k)).strip() or str(meta.get("name") or "---")
    username = str(meta.get("username") or "---")
    return (
        "👤 پرونده کامل خریدار\n\n"
        f"🆔 آیدی کاربر: {uid}\n👤 یوزرنیم: {username}\n📛 نام: {name}\n"
        f"🕐 اولین مشاهده: {fs}\n🕐 آخرین فعالیت: {ls}\n"
        f"🚫 وضعیت: {'مسدود' if uid in BLOCKED_USERS else 'فعال'}\n"
        f"🏷 تگ‌ها: {', '.join(tags) if tags else 'ندارد'}\n"
        f"⚠️ سطح هشدار: {str(risk.get('level', 'LOW'))}\n\n"
        "📦 سفارش‌ها\n"
        f"کل: {len(orders):,} | تکمیل: {completed:,} | فعال: {active:,}\n"
        f"در انتظار: {waiting:,} | لغو: {cancelled:,}\n\n"
        "💰 حساب مالی\n"
        f"قیمت پایه سفارش‌ها: {money(base)} تومان\n"
        f"مجموع تخفیف: {money(discounts)} تومان\n"
        f"مجموع نهایی سفارش‌ها: {money(final)} تومان\n"
        f"پرداخت با کیف پول: {money(wallet_paid)} تومان\n"
        f"پرداخت کارت/رسید: {money(card_paid)} تومان\n"
        f"برگشت وجه کیف پول: {money(refund)} تومان\n"
        f"کمیسیون معرفی دریافتی: {money(referral_comm)} تومان\n"
        f"💳 موجودی فعلی: {money(wallet(uid))} تومان\n\n"
        "👥 معرفی\n"
        f"کد معرف: {code}\nمعرف من: {owner}\n"
        f"دعوت‌شده‌ها: {int(ref.get('count', 0) or 0):,}\n"
        f"کمیسیون کل: {money(ref.get('commission_total', 0))} تومان\n\n"
        f"⭐ امتیاز: {int(USER_POINTS.get(uid, 0) or 0):,}\n"
        f"🎫 تیکت: {sum(1 for t in TICKETS.values() if isinstance(t, dict) and str(t.get('uid')) == uid):,}\n"
        f"📦 آخرین سفارش: #{orders[0].get('id') if orders else '---'}"
    )


def financial_order_text(o):
    base = num(o.get("price", 0))
    discount = num(o.get("discount", 0))
    final = num(o.get("final", 0))
    referral = num(o.get("referral_base_price", 0)) if o.get("referral_discount_applied") else 0
    referral_off = base * num(o.get("referral_discount_percent", 0)) // 100 if referral else 0
    coupon = o.get("coupon_code") or "ندارد"
    method = o.get("payment_method") or "card_receipt"
    wallet_before = num(o.get("wallet_before", 0))
    wallet_after = num(o.get("wallet_after", 0))
    receipt_status = "دریافت شده" if o.get("receipt") else ("پرداخت کیف پول" if o.get("wallet_paid") else "ندارد")
    return (
        "🧮 پرونده مالی سفارش\n\n"
        f"🆔 سفارش: #{o.get('id')}\n"
        f"👤 کاربر: {o.get('chat_id')}\n"
        f"👤 یوزرنیم: {o.get('username', 'ندارد')}\n"
        f"🛍 سرویس: {o.get('service')}\n"
        f"📌 مقدار/نوع: {o.get('type')}\n"
        f"🔗 مقصد: {o.get('target', '---')}\n\n"
        "💰 محاسبه مبلغ\n"
        f"قیمت پایه: {money(base)} تومان\n"
        f"مجموع تخفیف ثبت‌شده: {money(discount)} تومان\n"
        f"تخفیف معرفی: {money(referral_off)} تومان\n"
        f"کد تخفیف: {coupon}\n"
        f"💵 مبلغ نهایی: {money(final)} تومان\n\n"
        "💳 پرداخت\n"
        f"روش: {method}\n"
        f"مبلغ پرداخت‌شده/ثبت‌شده: {money(final)} تومان\n"
        f"رسید: {receipt_status}\n"
        f"کیف پول قبل: {money(wallet_before)} تومان\n"
        f"کسر از کیف پول: {money(final) if method == 'wallet' else 0} تومان\n"
        f"کیف پول بعد: {money(wallet_after)} تومان\n"
        f"↩️ برگشت وجه شده: {'بله' if o.get('wallet_refunded') else 'خیر'}\n\n"
        "👥 معرفی\n"
        f"فعال: {'بله' if o.get('referral_discount_applied') else 'خیر'}\n"
        f"درصد تخفیف معرفی: {num(o.get('referral_discount_percent', 0))}%\n"
        f"کمیسیون پرداخت‌شده: {money(o.get('referral_commission_paid', 0))} تومان\n\n"
        f"📊 وضعیت: {o.get('status')}\n"
        f"🕐 زمان: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(o.get('created', 0))))}"
    )


def admin_buyers_text(page=0):
    users = set(USERS)
    with orders_lock:
        users.update(str(o.get("chat_id")) for o in ORDERS.values() if o.get("chat_id"))
    buyers = []
    for uid in users:
        orders = get_user_orders(uid)
        if not orders:
            continue
        total = sum(num(o.get("final", 0)) for o in orders)
        done = sum(1 for o in orders if o.get("status") == "تکمیل شد")
        buyers.append((uid, total, len(orders), done))
    buyers.sort(key=lambda x: (x[1], x[2]), reverse=True)
    start = max(0, int(page)) * 30
    chunk = buyers[start:start+30]
    if not chunk:
        return "👥 خریداران\n\n📭 خرینده‌ای در این صفحه نیست."
    lines = [f"👥 تمام خریداران | {len(buyers):,} نفر\n", f"صفحه {page+1} | نمایش {start+1} تا {start+len(chunk)}\n"]
    for i, (uid, total, cnt, done) in enumerate(chunk, start=start+1):
        meta = USER_META.get(uid, {}) if isinstance(USER_META.get(uid, {}), dict) else {}
        uname = meta.get("username") or "---"
        lines.append(f"{i}️⃣ {uid} | {uname} | 🛒 {cnt} | ✅ {done} | 💰 {money(total)} تومان")
    return "\n".join(lines)


def admin_finance_ultra(period=None):
    now = time.time()
    with orders_lock:
        values = list(ORDERS.values())
    if period == "today":
        values = [o for o in values if now - int(o.get("created", 0)) < 86400]
    elif period == "week":
        values = [o for o in values if now - int(o.get("created", 0)) < 7*86400]
    elif period == "month":
        values = [o for o in values if now - int(o.get("created", 0)) < 30*86400]
    base = sum(num(o.get("price", 0)) for o in values)
    discount = sum(num(o.get("discount", 0)) for o in values)
    final = sum(num(o.get("final", 0)) for o in values)
    completed = [o for o in values if o.get("status") == "تکمیل شد"]
    done_final = sum(num(o.get("final", 0)) for o in completed)
    wallet_total = sum(num(o.get("final", 0)) for o in values if o.get("wallet_paid"))
    card_total = sum(num(o.get("final", 0)) for o in values if o.get("payment_method") == "card_receipt" and o.get("receipt"))
    refunds = sum(num(o.get("final", 0)) for o in values if o.get("wallet_refunded"))
    referral = sum(num(x.get("amount", 0)) for x in REFERRAL_COMMISSION_LOG if any(str(o.get("id")) == str(x.get("order_id")) for o in values))
    buyers = len({str(o.get("chat_id")) for o in values if o.get("chat_id")})
    return (
        "💰 مرکز حسابداری شفاف\n\n"
        f"📦 سفارش‌ها: {len(values):,}\n"
        f"👥 خریداران یکتا: {buyers:,}\n"
        f"💵 قیمت پایه: {money(base)} تومان\n"
        f"🎟 مجموع تخفیف: {money(discount)} تومان\n"
        f"💳 مبلغ نهایی سفارش‌ها: {money(final)} تومان\n"
        f"🟢 مبلغ سفارش‌های تکمیل‌شده: {money(done_final)} تومان\n\n"
        f"👛 پرداخت کیف پول: {money(wallet_total)} تومان\n"
        f"💳 پرداخت کارت/رسید: {money(card_total)} تومان\n"
        f"↩️ برگشت وجه کیف پول: {money(refunds)} تومان\n"
        f"💎 کمیسیون معرفی: {money(referral)} تومان\n\n"
        f"📐 تخفیف از قیمت پایه: {money(discount)} تومان\n"
        f"📊 میانگین هر سفارش: {money(final // len(values) if values else 0)} تومان"
    )


def risk_level(uid):
    uid = str(uid)
    flags = []
    orders = get_user_orders(uid)
    dup = sum(1 for r in RECEIPT_RECORDS.values() if isinstance(r, dict) and str(r.get("uid")) == uid and r.get("duplicate"))
    if dup: flags.append("رسید تکراری")
    if len(orders) >= 5 and sum(1 for o in orders if o.get("status") == "لغو شد") >= 3: flags.append("لغوهای زیاد")
    level = "LOW"
    if flags: level = "MEDIUM"
    if dup >= 2: level = "HIGH"
    RISK_FLAGS[uid] = {"level": level, "flags": flags, "updated": int(time.time())}
    return level, flags


def receipt_fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def receipt_security_check(uid, order, data):
    fp = receipt_fingerprint(data)
    previous = RECEIPT_RECORDS.get(fp)
    duplicate = bool(previous)
    same_user = bool(previous and str(previous.get("uid")) == str(uid))
    other_user = bool(previous and str(previous.get("uid")) != str(uid))
    size = len(data)
    suspicious = size < 4096
    reason = []
    if duplicate: reason.append("این تصویر قبلاً ثبت شده است")
    if other_user: reason.append("این رسید قبلاً با حساب دیگری ثبت شده است")
    if suspicious: reason.append("حجم تصویر غیرعادی کم است؛ بررسی دستی لازم است")
    return {
        "fingerprint": fp, "duplicate": duplicate, "same_user": same_user,
        "other_user": other_user, "suspicious": suspicious, "reason": reason,
        "size": size, "previous": previous
    }


def receipt_duplicate_message(uid, check):
    prev = check.get("previous") or {}
    if check.get("other_user"):
        return ("🚫 این رسید قبلاً با یک حساب دیگر ثبت شده است.\n\n"
                f"👤 حساب قبلی: {prev.get('uid', '---')}\n"
                f"📦 سفارش قبلی: #{prev.get('order_id', '---')}\n\n"
                "⚠️ سفارش فعلی خودکار تأیید نمی‌شود و برای بررسی مدیریت ارسال شد.")
    return "⚠️ این رسید قبلاً ارسال شده است.\n\n❌ لطفاً رسید جدید و مربوط به همین پرداخت را ارسال کنید."


def admin_receipt_alert(order, check):
    reasons = " | ".join(check.get("reason") or []) or "بدون هشدار خودکار"
    prev = check.get("previous") or {}
    return (
        "🚨 هشدار امنیت رسید\n\n"
        f"📦 سفارش جدید: #{order.get('id')}\n"
        f"👤 کاربر: {order.get('chat_id')}\n"
        f"💰 مبلغ سفارش: {money(order.get('final',0))} تومان\n"
        f"🧬 شناسه رسید: {check.get('fingerprint','')[:24]}...\n"
        f"📏 حجم تصویر: {check.get('size',0):,} بایت\n"
        f"⚠️ دلیل: {reasons}\n"
        f"👤 حساب قبلی: {prev.get('uid','---')}\n"
        f"📦 سفارش قبلی: #{prev.get('order_id','---')}\n\n"
        "🔎 نتیجه: نیازمند بررسی دستی"
    )


def admin_dashboard_ultra():
    c = admin_summary()
    today = admin_finance_ultra("today")
    pending_receipts = sum(1 for r in RECEIPT_RECORDS.values() if isinstance(r, dict) and r.get("suspicious"))
    return (
        "👑 مرکز مدیریت\n\n"
        f"🔴 سفارش در انتظار: {c['در انتظار بررسی']:,}\n"
        f"🔵 در حال انجام: {c['در حال انجام']:,}\n"
        f"🟢 تکمیل‌شده: {c['تکمیل شد']:,}\n"
        f"❌ لغوشده: {c['لغو شد']:,}\n"
        f"💳 رسیدهای نیازمند بررسی: {pending_receipts:,}\n"
        f"👥 خریداران: {len([u for u in USERS if get_user_orders(u)]):,}\n\n"
        f"{today}\n\n"
        f"{admin_reports_text()}\n\n"
        "⚡ برای حساب دقیق یک سفارش: «حساب سفارش #ID»\n"
        "⚡ برای پرونده مشتری: «پرونده #USER»"
    )


def ultra_pre_router(m, text, uid):
    # Normalize keypad labels before routing. Rubika delivers ChatKeypad clicks
    # as NewMessage text; hidden Unicode marks can otherwise make a visible
    # button appear to do nothing.
    import unicodedata
    text = unicodedata.normalize("NFKC", str(text or "")).strip()
    for _mark in ("\u200c", "\u200d", "\u200e", "\u200f", "\ufeff"):
        text = text.replace(_mark, "")
    text = " ".join(text.split())

    # Hard aliases for every important admin/customer keypad action.
    aliases = {
        "💳 مالی/کیف پول": "💳 کیف پول",
        "💳 مدیریت کیف پول": "💳 کیف پول",
        "💳 کیف پول کاربران": "💳 کیف پول",
        "💳 کیف پول‌ها": "💳 کیف پول",
        "🚨 هشدارهای امنیتی": "🚨 هشدارها",
        "⚠️ هشدارها": "🚨 هشدارها",
        "📦 سفارش ها": "📦 سفارش‌ها",
        "📦 سفارشها": "📦 سفارش‌ها",
        "🏠 بازگشت به اصلی": "🔙 بازگشت به مرحله قبل",
        "🔙 بازگشت": "🔙 بازگشت به مرحله قبل",
        "🔙 خدمات": "🔙 بازگشت به مرحله قبل",
        "🎟 ثبت کد تخفیف": "🎟 ثبت کد دعوت",
    }
    text = aliases.get(text, text)

    if is_admin(m):
        stage = USER_STAGE.get(uid)
        if stage == "ultra_order_id":
            set_stage(uid, "main")
            try:
                oid_q = str(text).strip().lstrip("#")
                with orders_lock:
                    oo = next((x for x in ORDERS.values() if str(x.get("id")) == oid_q), None)
                send(uid, financial_order_text(oo), admin_buttons(oo) if oo else ADMIN_KB) if oo else send(uid, "❌ سفارش پیدا نشد.", ADMIN_KB)
            except Exception:
                send(uid, "❌ شماره سفارش نامعتبر است.", ADMIN_KB)
            return True
        if stage == "ultra_user_id":
            set_stage(uid, "main")
            send(uid, customer_full_profile(str(text).strip().lstrip("#")), ADMIN_USERS_KB)
            return True
    if is_admin(m):
        # These are the visible buttons in the current admin keypad. Keep them
        # here so they cannot be swallowed by legacy admin routes.
        if text in ("📦 سفارش‌ها", "📦 سفارش ها", "📦 سفارشها", "admin_orders"):
            admin_list("در انتظار بررسی", uid)
            return True
        if text in ("💳 کیف پول", "💳 مالی/کیف پول", "💳 مدیریت کیف پول", "💳 کیف پول‌ها", "wallet_admin"):
            send(uid, "💳 مدیریت کامل کیف پول\n\nاز دکمه‌های زیر استفاده کنید.", wallet_admin_kb())
            return True
        if text in ("🚨 هشدارها", "⚠️ هشدارها", "🚨 هشدارهای امنیتی", "ultra_alerts"):
            alerts = [r for r in RECEIPT_RECORDS.values() if isinstance(r, dict) and (r.get("duplicate") or r.get("suspicious"))]
            if not alerts:
                send(uid, "🚨 هشدارها\n\n🟢 هیچ هشدار فعالی ثبت نشده است.", ADMIN_KB)
            else:
                lines = ["🚨 هشدارهای امنیتی\n", f"📊 تعداد: {len(alerts):,}", ""]
                for r in alerts[-50:][::-1]:
                    kind = "رسید تکراری" if r.get("duplicate") else "رسید مشکوک"
                    lines.append(f"👤 {r.get('uid','---')} | 📦 #{r.get('order_id','---')} | {kind}")
                send(uid, "\n".join(lines)[:7000], ADMIN_KB)
            return True
        if text == "🔙 حسابداری":
            send(uid, admin_finance_ultra(), ADMIN_FINANCE_KB)
            return True
        if text == "🧮 حساب سفارش":
            set_stage(uid, "ultra_order_id")
            send(uid, "🧮 شماره سفارش را بفرستید.\nمثال: 10482", ADMIN_KB); return True
        if text == "👤 پرونده مشتری":
            set_stage(uid, "ultra_user_id")
            send(uid, "👤 آیدی کاربر مشتری را بفرستید.", ADMIN_USERS_KB); return True
        if text in ("👑 مرکز کنترل", "🎛 مرکز کنترل", "📊 داشبورد مدیریتی"): 
            send(uid, admin_dashboard_ultra(), ADMIN_KB); return True
        if text in ("👥 خریداران", "👥 تمام خریداران"):
            send(uid, admin_buyers_text(0), ADMIN_USERS_KB); return True
        if text == "➡️ خریداران بعدی":
            send(uid, admin_buyers_text(1), ADMIN_USERS_KB); return True
        if text in ("💰 حسابداری شفاف", "🧮 حسابداری"):
            send(uid, admin_finance_ultra(), ADMIN_FINANCE_KB); return True
        if text == "📈 حسابداری امروز":
            send(uid, admin_finance_ultra("today"), ADMIN_FINANCE_KB); return True
        if text == "📈 حسابداری هفته":
            send(uid, admin_finance_ultra("week"), ADMIN_FINANCE_KB); return True
        if text == "📈 حسابداری ماه":
            send(uid, admin_finance_ultra("month"), ADMIN_FINANCE_KB); return True
        m1 = re.match(r"^(?:حساب سفارش|🧮 حساب سفارش)\s*#?(\d+)$", text)
        if m1:
            with orders_lock:
                o = next((x for x in ORDERS.values() if str(x.get("id")) == m1.group(1)), None)
            if not o:
                send(uid, "❌ سفارش پیدا نشد.", ADMIN_KB)
            else:
                send(uid, financial_order_text(o), admin_buttons(o))
            return True
        m2 = re.match(r"^(?:پرونده|👤 پرونده)\s*#?(.+)$", text)
        if m2:
            send(uid, customer_full_profile(m2.group(1).strip()), ADMIN_USERS_KB); return True
        if text in ("⚠️ هشدارها", "🚨 هشدارهای امنیتی"):
            alerts=[]
            for r in RECEIPT_RECORDS.values():
                if isinstance(r,dict) and (r.get("duplicate") or r.get("suspicious")):
                    alerts.append(r)
            if not alerts:
                send(uid,"🟢 هشدار فعالی ثبت نشده است.",ADMIN_KB)
            else:
                lines=["🚨 هشدارهای رسید\n"]
                for r in alerts[-50:][::-1]:
                    lines.append(f"👤 {r.get('uid')} | 📦 #{r.get('order_id')} | {'مشترک' if r.get('other_user') else 'تکراری' if r.get('duplicate') else 'مشکوک'}")
                send(uid,"\n".join(lines)[:7000],ADMIN_KB)
            return True
        if text == "📜 گزارش فعالیت":
            if not AUDIT_LOG:
                send(uid,"📜 گزارش فعالیت خالی است.",ADMIN_SYSTEM_KB)
            else:
                lines=["📜 آخرین عملیات ثبت‌شده\n"]
                for a in AUDIT_LOG[-50:][::-1]:
                    tm=time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(int(a.get("time",0))))
                    lines.append(f"{tm} | {a.get('action')} | user={a.get('uid','-')} | order={a.get('order_id','-')} | admin={a.get('admin_id','-')}")
                send(uid,"\n".join(lines)[:7000],ADMIN_SYSTEM_KB)
            return True
        m3 = re.match(r"^تگ\s+#?([^\s]+)\s+(.+)$", text)
        if m3:
            target, tag = m3.group(1), m3.group(2).strip()[:50]
            arr=USER_TAGS.setdefault(target,[])
            if tag not in arr: arr.append(tag)
            USER_TAGS[target]=arr[-30:]; _ultra_save(USER_TAGS_FILE,USER_TAGS)
            audit_event("add_tag",uid,target,note=tag)
            send(uid,f"🏷 تگ «{tag}» برای {target} ثبت شد.",ADMIN_USERS_KB); return True
        if text == "🧪 تست کامل سیستم":
            with state_lock: poll_age=time.monotonic()-last_poll_ok
            checks=[
                ("Polling", poll_age < 180), ("Orders", isinstance(ORDERS,dict)),
                ("Wallet", isinstance(WALLETS,dict)), ("معرفی", isinstance(REFERRALS,dict)),
                ("Receipts", isinstance(RECEIPT_RECORDS,dict)), ("گزارش فعالیت", isinstance(AUDIT_LOG,list)),
                ("Backup", os.path.isdir(DATA_DIR))]
            send(uid,"🧪 تست سلامت سیستم\n\n"+"\n".join(("🟢 " if ok else "🔴 ")+name for name,ok in checks),ADMIN_SYSTEM_KB); return True

    # کاربر: اعلان‌های خوانده‌نشده
    if text == "🔔 اعلان‌های خوانده‌نشده":
        arr=[x for x in NOTIFICATIONS.get(uid,[]) if isinstance(x,dict) and not x.get("read")]
        for x in arr: x["read"]=True
        _ultra_save(NOTIFICATIONS_FILE,NOTIFICATIONS)
        send(uid, "🔔 اعلان‌های خوانده‌نشده\n\n" + ("\n\n".join(str(x.get("body",x.get("title","اعلان"))) for x in arr) if arr else "📭 موردی ندارید."), CUSTOMER_MORE_KB); return True
    if text == "💰 گزارش مالی من":
        base,disc,final,wp,cp,rf,rc=user_financial_totals(uid)
        send(uid, "💰 گزارش مالی من\n\n"+f"قیمت پایه: {money(base)} تومان\n🎁 تخفیف: {money(disc)} تومان\n💵 مجموع سفارش‌ها: {money(final)} تومان\n👛 پرداخت کیف پول: {money(wp)} تومان\n💳 پرداخت کارت: {money(cp)} تومان\n↩️ برگشت وجه: {money(rf)} تومان\n💎 کمیسیون معرفی: {money(rc)} تومان\n💰 موجودی: {money(wallet(uid))} تومان", CUSTOMER_ACCOUNT_KB); return True
    return False


def admin_buttons(o):
    n = o.get("id")
    status = o.get("status")
    rows = [[(f"🧮 حساب سفارش #{n}", "ultra_order")]]
    if status == "در انتظار بررسی":
        rows.append([(f"🔵 شروع #{n}", "start"), (f"🟢 تکمیل #{n}", "done")])
        rows.append([(f"🔴 لغو #{n}", "cancel")])
    elif status == "در حال انجام":
        rows.append([(f"🟢 تکمیل #{n}", "done"), (f"🔴 لغو #{n}", "cancel")])
    rows.append([("👤 پرونده مشتری", "ultra_user"), ("🔙 پنل مدیریت", "admin")])
    return kb(rows)


# دکمه‌های مرکز کنترل ULTRA MAX
ADMIN_KB = kb([
    [("👑 داشبورد", "ultra_dashboard"), ("📦 سفارش‌ها", "admin_orders")],
    [("👥 خریداران", "ultra_buyers"), ("💰 حسابداری", "ultra_finance")],
    [("🚨 هشدارها", "ultra_alerts"), ("💳 مالی/کیف پول", "admin_finance")],
    [("📣 بازاریابی", "admin_marketing"), ("🛠 سیستم", "admin_system")],
    [("💾 پشتیبان", "admin_backup")],
    [("🏠 اصلی", "home")]
])

# =========================
# MESSAGE HANDLER
# =========================

def handle(m):
    if ultra_pre_router(m, (getattr(m, "text", "") or "").strip(), str(getattr(m, "chat_id", "") or "")):
        return


    if not m:
        return

    uid = str(
        getattr(m, "chat_id", "") or ""
    )

    sid = str(
        getattr(m, "sender_id", "") or ""
    )

    text = (
        getattr(m, "text", "") or ""
    ).strip()

    # Rubika may return ZWNJ/ZWJ/bidi marks differently from the keypad label.
    # Keep the original text for data entry, but use a normalized copy for menu routing.
    import unicodedata
    route_text = unicodedata.normalize("NFKC", text)
    for _mark in ("\u200c", "\u200d", "\u200e", "\u200f", "\ufeff"):
        route_text = route_text.replace(_mark, "")
    route_text = " ".join(route_text.split())

    aliases = {
        "🛍 خرید": "🛍 خرید",
        "📦 سفارشها": "📦 سفارش‌ها",
        "📦 سفارش ها": "📦 سفارش‌ها",
        "👤 حساب کاربری": "👤 حساب کاربری",
        "💰 کیف پول": "💰 کیف پول",
        "🎟 ثبت کد تخفیف": "🎟 ثبت کد دعوت",
        "🏠 بازگشت به اصلی": "🔙 بازگشت به مرحله قبل",
        "🔙 بازگشت": "🔙 بازگشت به مرحله قبل",
        "🔙 خدمات": "🔙 بازگشت به مرحله قبل",
    }
    if route_text in aliases:
        text = aliases[route_text]

    print("ROUTE:", uid, repr(text), "stage=", USER_STAGE.get(uid))

    # Avoid logging every message: hosted log I/O can noticeably add latency.

    # HARD ROUTES FOR MAIN MENU: these are intentionally before the long admin/state router.
    # Reply-keyboard clicks arrive as ordinary text messages.
    if text == "🛍 خرید" and not is_admin(m):
        if ADMIN_SETTINGS.get("shop_paused") and not is_admin(m):
            send(uid, "⏸ خریدها موقتاً متوقف شده است.\nلطفاً بعداً دوباره تلاش کنید.", MAIN)
        else:
            set_stage(uid, "services")
            send(uid, customer_service_info_text(), SERVICES_KB)
        return
    if text == "📦 سفارش‌ها" and not is_admin(m):
        send(uid, user_orders_text(uid), CUSTOMER_ORDER_KB)
        return
    if text == "👤 حساب کاربری" and not is_admin(m):
        send(uid, user_profile_text(uid), CUSTOMER_ACCOUNT_KB)
        return
    if text == "💰 کیف پول" and not is_admin(m):
        send(uid, f"💰 کیف پول من\n\n💵 موجودی فعلی: {money(wallet(uid))} تومان", CUSTOMER_ACCOUNT_KB)
        return

    # ADMIN
    if is_admin(m):

        if admin_command(
            text,
            uid
        ):
            return

        admin_stage = USER_STAGE.get(uid)
        if admin_stage in ("wallet_lookup", "wallet_credit", "wallet_debit", "wallet_set", "wallet_zero"):
            if admin_wallet_command(uid, text):
                return
        if admin_stage == "admin_user_profile":
            set_stage(uid, "main")
            admin_user_profile(uid, text)
            return
        if admin_stage == "admin_search_order":
            set_stage(uid, "main")
            admin_search_order(uid, text)
            return
        if admin_stage == "admin_search_user":
            set_stage(uid, "main")
            admin_search_user(uid, text)
            return
        if admin_stage == "admin_broadcast":
            if text == "لغو همگانی":
                set_stage(uid, "main")
                send(uid, "❌ ارسال همگانی لغو شد.", ADMIN_KB)
                return
            set_stage(uid, "main")
            users = [x for x in USERS if x not in BLOCKED_USERS and x not in ADMINS]
            send(uid, f"📢 ارسال به {len(users):,} کاربر شروع شد.", ADMIN_KB)
            def _broadcast():
                ok = 0
                for u in users:
                    if queue_send(u, text, MAIN):
                        ok += 1
                admin_log(f"broadcast:{ok}/{len(users)}", "", uid)
                send(uid, f"📢 همگانی تمام شد.\n✅ موفق: {ok:,}\n❌ ناموفق: {len(users)-ok:,}", ADMIN_KB)
            Thread(target=_broadcast, daemon=True).start()
            return
        if admin_stage == "admin_message_user":
            set_stage(uid, "main")
            mmsg = re.match(r"^پیام\s+#?([^\s]+)\s+(.+)$", text, re.S)
            if mmsg:
                admin_message_user(uid, mmsg.group(1), mmsg.group(2))
            else:
                send(uid, "❌ فرمت اشتباه است.\nپیام #USER متن پیام", ADMIN_KB)
            return
        if admin_stage == "admin_block_user":
            set_stage(uid, "main")
            toggle_block(uid, text)
            return
        if admin_stage == "admin_announcement":
            if text == "لغو اطلاعیه":
                set_stage(uid, "main")
                send(uid, "❌ ثبت اطلاعیه لغو شد.", ADMIN_MARKETING_KB)
                return
            set_stage(uid, "main")
            admin_announce(uid, text)
            return

    # کنترل دسترسی کاربران عادی
    if not is_admin(m) and uid in BLOCKED_USERS:
        send(uid, "🚫 دسترسی شما به ربات مسدود شده است.")
        return

    if not is_admin(m) and ADMIN_SETTINGS.get("maintenance"):
        send(uid, "🛠 ربات موقتاً در حال بروزرسانی است. لطفاً بعداً دوباره تلاش کنید.")
        return

    if USER_STAGE.get(uid) == "referral_code":
        if text in ("❌ لغو", "❌ خروج"):
            set_stage(uid, "main")
            send(uid, "✅ لغو شد.", MAIN)
            return
        ok, msg = apply_referral(uid, text)
        set_stage(uid, "main")
        send(uid, msg, MAIN if ok else kb([[("🎟 ثبت دوباره", "referral_code")], [("👥 دعوت دوستان", "referral")], [("🏠 اصلی", "home")]]))
        return

    # START
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        referral_code = parts[1].strip() if len(parts) > 1 else None
        start(m, referral_code)
        return

    # PAYMENT METHOD
    if text == "💰 پرداخت با کیف پول":
        pay_wallet(uid)
        return

    if text == "💳 پرداخت کارت":
        o = current_order(uid)
        if o:
            payment(uid, o)
        else:
            send(uid, "❌ سفارش در حال پرداختی ندارید.", MAIN)
        return

    # CANCEL
    if text in ("❌ خروج", "❌ لغو"):
        with pending_lock:
            PENDING_ORDERS.pop(uid, None)
        set_stage(uid, "main")
        send(uid, "✅ لغو شد.", MAIN)
        return

    if text == "🔙 بازگشت به مرحله قبل":
        back_previous(uid)
        return

    # USER CENTER
    if text == "👤 حساب کاربری" or text == "👤 حساب من":
        send(uid, user_profile_text(uid), CUSTOMER_ACCOUNT_KB)
        return

    if text == "📊 آمار من":
        send(uid, user_stats_text(uid), CUSTOMER_ACCOUNT_KB)
        return

    if text in ("🧾 سفارش‌ها", "🧾 سفارش‌های من"):
        send(uid, user_orders_text(uid), CUSTOMER_ORDER_KB)
        return

    if text in ("📦 پیگیری", "📦 پیگیری سفارش"):
        send(uid, user_track(uid), CUSTOMER_ORDER_KB)
        return

    if text == "📌 آخرین سفارش":
        send(uid, user_last(uid), CUSTOMER_ORDER_KB)
        return
    if text == "🔁 سفارش مجدد":
        reorder_last(uid)
        return

    if text in ("💰 کیف پول", "💰 موجودی کیف پول"):
        send(
            uid,
            f"💰 کیف پول من\n\n💵 موجودی فعلی: {money(wallet(uid))} تومان\n\n"
            "💡 موجودی کیف پول می‌تواند برای پرداخت سفارش‌های فروشگاه استفاده شود.\n"
            "🔐 تغییرات کیف پول توسط مدیریت ثبت و قابل پیگیری است.",
            CUSTOMER_ACCOUNT_KB
        )
        return

    if text == "💳 گردش کیف پول":
        send(uid, wallet_history_text(uid), CUSTOMER_ACCOUNT_KB)
        return

    if text in ("⭐ امتیاز من", "⭐ امتیاز"):
        send(uid, customer_points_text(uid), CUSTOMER_MORE_KB)
        return

    if text == "🎁 تخفیف و هدیه" or text == "🎁 تخفیف‌ها":
        send(
            uid,
            "🎁 تخفیف‌ها و هدایا\n\n"
            "🏷 کدهای فعال فروشگاه در زمان خرید قابل استفاده‌اند.\n"
            "💡 اگر کد تخفیف دارید، آن را در مرحله پرداخت وارد کنید.\n"
            "⚠️ هر کد ممکن است محدودیت استفاده یا تاریخ اعتبار داشته باشد.",
            CUSTOMER_MORE_KB
        )
        return

    if text in ("📣 اطلاعیه‌ها",):
        send(uid, announcements_text(), CUSTOMER_MORE_KB)
        return

    if text in ("🔔 اعلان‌های من", "🔔 اعلان‌ها / پیام‌ها", "📨 پیام‌ها"):
        send(uid, customer_notifications_text(uid), CUSTOMER_NOTIFICATIONS_KB)
        return

    if text == "📌 علاقه‌مندی‌ها":
        send(uid, favorites_text(uid), kb([
            [("🛍 خرید", "services"), ("📌 علاقه‌مندی‌ها", "favorites")],
            [("🔙 حساب کاربری", "profile"), ("🏠 اصلی", "home")]
        ]))
        return

    if text in ("🎟 ثبت کد دعوت", "🎟 ثبت کد تخفیف"):
        set_stage(uid, "referral_code")
        send(uid, "🎟 ثبت کد معرف\n\nکد معرف دوستتان را دقیقاً ارسال کنید.\nربات ابتدا معتبر بودن کد را بررسی می‌کند و فقط در صورت صحیح بودن آن را ثبت می‌کند.", kb([[("❌ لغو", "cancel")], [("🏠 اصلی", "home")]]))
        return

    if text in ("👥 دعوت دوستان", "🎟 کد معرف من"):
        send(uid, customer_referral_text(uid), CUSTOMER_REFERRAL_KB)
        return

    if text in ("📚 راهنمای کامل", "📚 توضیحات کامل", "📚 آموزش ربات"):
        send(uid, docs_text(), CUSTOMER_MORE_KB)
        return

    if text == "ℹ️ درباره فروشگاه":
        send(uid, customer_about_text(), CUSTOMER_MORE_KB)
        return

    if text == "❓ راهنما":
        send(uid, docs_text(), CUSTOMER_MORE_KB)
        return

    # SERVICES
    if text in ("🏠 بازگشت به اصلی", "🔙 بازگشت", "🔙 خدمات"):
        back_previous(uid)
        return

    if text in ("🛍 خرید", "🛍 خدمات", "🛍 پنل افزایش", "🛒 خرید", "🛒 ثبت سفارش", "🛍 خرید بزرگ"):
        if ADMIN_SETTINGS.get("shop_paused") and not is_admin(m):
            send(uid, "⏸ خریدها موقتاً متوقف شده است.\nلطفاً بعداً دوباره تلاش کنید.", MAIN)

            return
        set_stage(uid, "services")
        send(uid, customer_service_info_text(), SERVICES_KB)
        return

    # توضیحات خدمات حذف شده؛ نام‌های قدیمی فقط به منوی خرید هدایت می‌شوند.
    if text in ("ℹ️ توضیحات", "ℹ️ توضیحات خدمات", "❓ راهنمای خرید", "❓ راهنما"):
        set_stage(uid, "services")
        send(uid, "🛍 انتخاب سرویس", SERVICES_KB)
        return

    # BUY
    if text == "🛒 خرید":
        set_stage(uid, "services")
        send(uid, customer_service_info_text(), SERVICES_KB)
        return

    if text == "📣 کانال":
        show_prices(uid, CHANNEL, "📣 ", "📣 تعرفه کانال"); return
    if text == "👥 گروه":
        show_prices(uid, GROUP, "👥 ", "👥 تعرفه گروه"); return
    if text == "⭐ روبینو":
        show_prices(uid, FOLLOWERS, "⭐ ", "⭐ تعرفه روبینو"); return

    # CHANNEL
    if text in ("📣 کانال", "📣 افزایش کانال"):
        set_stage(uid, "services")
        show_prices(uid, CHANNEL, "📣 ", "📣 تعرفه کانال")
        return

    # GROUP
    if text in ("👥 گروه", "👥 افزایش گروه"):
        set_stage(uid, "services")
        show_prices(uid, GROUP, "👥 ", "👥 تعرفه گروه")
        return

    # FOLLOWERS
    if text in ("⭐ فالور", "⭐ افزایش فالور", "⭐ افزایش روبینو"):
        set_stage(uid, "services")
        show_prices(uid, FOLLOWERS, "⭐ ", "⭐ تعرفه روبینو")
        return

    if text == "💰 مشاهده تعرفه کانال":
        show_prices(uid, CHANNEL, "📣 ", "📣 تعرفه کانال"); return
    if text == "💰 مشاهده تعرفه گروه":
        show_prices(uid, GROUP, "👥 ", "👥 تعرفه گروه"); return
    if text in ("💰 مشاهده تعرفه روبینو", "💰 مشاهده تعرفه فالور"):
        show_prices(uid, FOLLOWERS, "⭐ ", "⭐ تعرفه روبینو"); return

    # PRICE
    price = extract_price(text)

    if price:

        typ, service, amount = price

        create_order(
            m,
            service,
            amount,
            typ
        )

        return

    # LAST ORDER
    o = current_order(uid)

    # NO DISCOUNT
    if text == "❌ ندارم":
        if o and o.get("discount_wait"):
            with pending_lock:
                if uid in PENDING_ORDERS:
                    PENDING_ORDERS[uid]["discount_wait"] = 0
                    PENDING_ORDERS[uid]["final"] = num(PENDING_ORDERS[uid]["price"])
                    PENDING_ORDERS[uid]["coupon_code"] = ""
                    PENDING_ORDERS[uid]["payment_method"] = "card_receipt"
            payment(uid, o)
        return

    # RECEIPT
    if is_media(m):

        receipt(m)
        return

    # DISCOUNT
    if o and o.get("discount_wait"):

        discount(
            m,
            text
        )

        return

    # TARGET
    if o and o.get("waiting"):

        set_target(
            m,
            text
        )

        return

    # TRACK
    if text in ("📦 پیگیری", "📦 پیگیری سفارش"):
        send(uid,user_track(uid),kb([[('🧾 سفارش‌های من','orders'),('🔁 سفارش مجدد','reorder')],[('🔙 بازگشت به مرحله قبل','back'),('🏠 اصلی','home')]])); return

    # ORDERS
    if text in ("🧾 سفارش‌ها", "🧾 سفارش‌های من"):
        send(uid,user_orders(uid),CUSTOMER_ORDER_KB); return

    # RULES
    if text == "📜 قوانین":

        send(
            uid,
            "📜 قوانین:\n"
            "1️⃣ آیدی صحیح ارسال کنید.\n"
            "2️⃣ در صورت لینک غلط همه فرستادین گردن خودتون هست و هیچ گونه مسئولیت ما نداریم.\n"
            "3️⃣ پس از پرداخت رسید ارسال شود.\n"
            "4️⃣ اگر رسید فیک بفرستید سفارش کنسل میشه."
        )

        return

    # SUPPORT
    if text in ("📞 پشتیبانی", "🆘 پشتیبانی"):

        send(
            uid,
            "🆘 پشتیبانی فروشگاه\n\n"
            f"🆔 آیدی پشتیبانی: {SUPPORT}"
        )

        return

    # HOME
    if text == "🏠 اصلی":

        start(m)
        return

    if is_admin(m):
        return

    send(
        uid,
        "👇 از منو انتخاب کنید."
    )


# =========================
# UPDATE
# =========================

def process_update(item):
    global last_message_at

    try:
        m = updates.Update(
            item
        ).to_message()

        if m:
            with state_lock:
                last_message_at = time.monotonic()

            handle(m)

    except Exception as e:
        # پیام خراب نباید polling را متوقف کند.
        print(
            "UPDATE:",
            repr(e)
        )


# =========================
# API
# =========================

def api_updates(offset=""):
    params = {"limit": POLL_LIMIT}

    if offset:
        params["offset_id"] = offset

    response = None

    try:
        response = http.post(
            f"{API}/getUpdates",
            json=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}"
            )

        data = response.json()

    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(
            f"NETWORK/JSON: {e}"
        ) from e

    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    if not isinstance(data, dict):
        raise RuntimeError("INVALID_JSON")

    if data.get("status") != "OK":
        raise RuntimeError(
            str(
                data.get("status_det")
                or data
            )
        )

    x = data.get("data") or {}

    if not isinstance(x, dict):
        raise RuntimeError("INVALID_DATA")

    arr = x.get("updates") or []

    new_offset = (
        x.get("next_offset_id")
        or offset
    )

    _mark_poll_ok()

    return arr, new_offset


# =========================
# CLEAR OLD
# =========================

def clear_old_updates():

    print(
        "CLEARING OLD UPDATES..."
    )

    offset = read(
        OF,
        ""
    )

    if not valid_offset(offset):
        offset = ""

    loops = 0

    while loops < 1000:

        loops += 1

        try:

            arr, new_offset = api_updates(
                offset
            )

            if (
                new_offset
                and new_offset != offset
            ):

                offset = new_offset

                write(
                    OF,
                    offset
                )

            if not arr:
                break

            print(
                "OLD UPDATES SKIPPED:",
                len(arr)
            )

            # بدون sleep اضافی

        except Exception as e:

            print(
                "CLEAR ERROR:",
                repr(e)
            )

            break

    if offset:
        write(
            OF,
            offset
        )

    write(
        READY,
        str(int(time.time()))
    )

    print(
        "OLD UPDATES CLEARED"
    )


# =========================
# FAST POLLING
# =========================

def polling():
    """Low-latency polling loop with bounded backoff and safe update dispatch."""
    global last_poll_ok

    backoff = BACKOFF_MIN
    initialized = False

    while True:
        try:
            with offset_lock:
                offset = read(OFFSET_FILE, "").strip()

            if not initialized:
                if not offset:
                    clear_old_updates()
                    with offset_lock:
                        offset = read(OFFSET_FILE, "").strip()
                initialized = True

            arr, next_offset = api_updates(offset)

            # Successful API contact: immediately return to minimum latency.
            backoff = BACKOFF_MIN
            _mark_poll_ok()

            if next_offset:
                with offset_lock:
                    write(OFFSET_FILE, str(next_offset))
                offset = str(next_offset)

            if not arr:
                time.sleep(EMPTY_POLL_DELAY)
                continue

            # Dispatch immediately; don't process updates serially.
            for upd in arr:
                try:
                    executor.submit(process_update, upd)
                except Exception as e:
                    print("DISPATCH ERROR:", e)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            err = repr(e)
            print("POLL ERROR:", err)
            # Rubika may reject a stale/invalid offset with INVALID_INPUT.
            # Drop only the polling cursor and retry from a clean state.
            if "INVALID_INPUT" in err:
                try:
                    with offset_lock:
                        write(OFFSET_FILE, "")
                    print("POLL: invalid offset cleared; retrying without offset")
                except Exception as clear_err:
                    print("POLL OFFSET CLEAR ERROR:", repr(clear_err))
                initialized = True
                time.sleep(0.5)
                continue
            time.sleep(backoff)
            backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, backoff * 1.6))

class Handler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"OK"
        )

    def do_HEAD(self):

        self.send_response(
            200
        )

        self.end_headers()

    def log_message(
        self,
        *args
    ):
        pass

def web_server():

    while True:

        try:

            server = ThreadingHTTPServer(
                ("0.0.0.0", PORT),
                Handler
            )
            server.daemon_threads = True
            server.allow_reuse_address = True

            print(
                "WEB:",
                PORT
            )

            server.serve_forever()

        except Exception as e:

            print(
                "WEB ERROR:",
                repr(e)
            )

            time.sleep(2)


# =========================
# WATCHDOG
# =========================

def watchdog():
    """
    اگر polling واقعاً گیر کند، process را خارج می‌کند تا Render
    آن را دوباره اجرا کند. قطع عادی اینترنت توسط polling مدیریت می‌شود.
    """
    while True:
        time.sleep(30)

        with state_lock:
            silent_for = time.monotonic() - last_poll_ok

        if silent_for > 180:
            print(
                "WATCHDOG: polling stalled for",
                round(silent_for),
                "seconds -> restart"
            )
            os._exit(1)


# =========================
# MAIN
# =========================

def main():
    _install_exception_hooks()
    if not TOKEN:
        raise RuntimeError(
            "TOKEN environment variable is missing. "
            "Set TOKEN in Render Environment Variables."
        )

    Thread(
        target=web_server,
        daemon=True,
        name="web-server"
    ).start()

    Thread(
        target=watchdog,
        daemon=True,
        name="watchdog"
    ).start()

    polling()


if __name__ == "__main__":
    while True:
        try:
            main()

        except KeyboardInterrupt:
            print("BOT STOPPED")
            break

        except SystemExit:
            raise

        except Exception as e:
            print("FATAL:", repr(e))
            time.sleep(1)
