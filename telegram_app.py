import os
import time
import json
import base64
import hashlib
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

import requests
from cryptography.fernet import Fernet, InvalidToken
from pybit.unified_trading import HTTP

# IMPORTANT:
# app.py is your OLD bot file.
# This wrapper imports app.py but does not edit the old logic.
import app as core


# ============================================================
# TELEGRAM MONITOR CONFIG
# ============================================================
TELEGRAM_ENABLED = core.env_bool("TELEGRAM_ENABLED", "true")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
TELEGRAM_ADMIN_LINK = os.getenv("TELEGRAM_ADMIN_LINK", "https://t.me/Assistant_quantum").strip()

TELEGRAM_POLL_TIMEOUT_SEC = core.env_int("TELEGRAM_POLL_TIMEOUT_SEC", "2")
TELEGRAM_PROCESS_LIMIT = core.env_int("TELEGRAM_PROCESS_LIMIT", "20")
TELEGRAM_MONITOR_INTERVAL_SEC = core.env_int("TELEGRAM_MONITOR_INTERVAL_SEC", "300")
TELEGRAM_MONITOR_SEND_OK_EVERY_CHECK = core.env_bool("TELEGRAM_MONITOR_SEND_OK_EVERY_CHECK", "false")
TELEGRAM_DELETE_SENSITIVE_MESSAGES = core.env_bool("TELEGRAM_DELETE_SENSITIVE_MESSAGES", "true")

ACCESS_DAYS = core.env_int("ACCESS_DAYS", "30")
BOT_DB_PATH = os.getenv("BOT_DB_PATH", "/tmp/skynet7rader_monitor.db").strip()
DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY", "").strip()

BYBIT_MONITOR_CATEGORY = os.getenv("BYBIT_MONITOR_CATEGORY", "linear").strip().lower()


DEFAULT_ACCESS_CODES_RAW = """
392051
847162
502938
115684
773029
482195
605317
229481
938572
150643
827319
404856
591274
336109
728495
201948
864032
551726
913840
476203
109587
683214
357902
824165
590371
216498
743056
189274
635108
402837
957123
126489
730591
584210
319647
842056
671932
250384
918273
463519
137402
895621
524038
701945
362810
948572
215036
679413
483120
506794
"""


def load_access_codes():
    raw = os.getenv("ACCESS_CODES", DEFAULT_ACCESS_CODES_RAW)
    raw = raw.replace("\n", ",").replace(" ", ",")
    codes = []

    for item in raw.split(","):
        code = item.strip()
        if code:
            codes.append(code)

    return sorted(set(codes))


ACCESS_CODES = load_access_codes()


# ============================================================
# SMALL HELPERS
# ============================================================

def now_utc() -> datetime:
    return datetime.utcnow()


def now_str() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S")


def dt_to_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value: str):
    if not value:
        return None

    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def safe_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def mask_value(value: str, left=4, right=4) -> str:
    value = safe_text(value)

    if len(value) <= left + right:
        return "*" * len(value)

    return value[:left] + "..." + value[-right:]


def chunks(text: str, limit=3900):
    text = safe_text(text)

    if len(text) <= limit:
        return [text]

    out = []
    while text:
        out.append(text[:limit])
        text = text[limit:]

    return out


def format_decimal(value) -> str:
    try:
        d = Decimal(str(value))
        s = format(d.normalize(), "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:
        return str(value)


# ============================================================
# ENCRYPTION
# Stored API keys/secrets are encrypted in SQLite.
# Keep DATA_ENCRYPTION_KEY stable. If changed, old saved API keys
# cannot be decrypted anymore.
# ============================================================

def make_fernet():
    if not DATA_ENCRYPTION_KEY or DATA_ENCRYPTION_KEY.upper().startswith("CHANGE_"):
        return None

    digest = hashlib.sha256(DATA_ENCRYPTION_KEY.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


FERNET = make_fernet()


def encrypt_secret(value: str) -> str:
    if not FERNET:
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY missing. Set a long random DATA_ENCRYPTION_KEY in Render env vars."
        )

    return FERNET.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not FERNET:
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY missing. Cannot decrypt saved API credentials."
        )

    return FERNET.decrypt(value.encode("utf-8")).decode("utf-8")


# ============================================================
# SQLITE DATABASE
# ============================================================

def db_connect():
    db_path = Path(BOT_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id TEXT PRIMARY KEY,
                telegram_username TEXT,
                first_name TEXT,
                last_name TEXT,
                skynet_username TEXT,
                api_key_enc TEXT,
                api_secret_enc TEXT,
                api_registered_at TEXT,
                api_last_status TEXT,
                api_last_checked_at TEXT,
                api_last_balance_usdt TEXT,
                api_last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_codes (
                code TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unused',
                claimed_by_chat_id TEXT,
                claimed_telegram_username TEXT,
                claimed_skynet_username TEXT,
                activated_at TEXT,
                expires_at TEXT,
                expired_notified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_states (
                chat_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                payload_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                level TEXT,
                message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )

        for code in ACCESS_CODES:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_codes (
                    code, status, created_at, updated_at
                )
                VALUES (?, 'unused', ?, ?)
                """,
                (code, now_str(), now_str()),
            )


def db_get_meta(key: str, default=""):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM bot_meta WHERE key = ?",
            (key,),
        ).fetchone()

        if not row:
            return default

        return row["value"]


def db_set_meta(key: str, value: str):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_meta (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, str(value), now_str()),
        )


def log_monitor(chat_id: str, level: str, message: str):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO monitor_logs (chat_id, level, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(chat_id), level, message[:2000], now_str()),
        )


# ============================================================
# USER / ACCESS CODE DATABASE FUNCTIONS
# ============================================================

def ensure_user(chat_id, telegram_username="", first_name="", last_name=""):
    chat_id = str(chat_id)

    with db_connect() as conn:
        row = conn.execute(
            "SELECT chat_id FROM users WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        if row:
            conn.execute(
                """
                UPDATE users
                SET telegram_username = ?,
                    first_name = ?,
                    last_name = ?,
                    updated_at = ?
                WHERE chat_id = ?
                """,
                (
                    safe_text(telegram_username),
                    safe_text(first_name),
                    safe_text(last_name),
                    now_str(),
                    chat_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (
                    chat_id,
                    telegram_username,
                    first_name,
                    last_name,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    safe_text(telegram_username),
                    safe_text(first_name),
                    safe_text(last_name),
                    now_str(),
                    now_str(),
                ),
            )


def get_user(chat_id):
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()


def get_active_access(chat_id):
    chat_id = str(chat_id)

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM access_codes
            WHERE claimed_by_chat_id = ?
              AND status = 'active'
            ORDER BY expires_at DESC
            """,
            (chat_id,),
        ).fetchall()

        for row in rows:
            expires_at = parse_dt(row["expires_at"])

            if expires_at and expires_at > now_utc():
                return row

            conn.execute(
                """
                UPDATE access_codes
                SET status = 'expired',
                    updated_at = ?
                WHERE code = ?
                """,
                (now_str(), row["code"]),
            )

    return None


def user_has_active_access(chat_id) -> bool:
    return get_active_access(chat_id) is not None


def get_latest_access(chat_id):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT * FROM access_codes
            WHERE claimed_by_chat_id = ?
            ORDER BY activated_at DESC
            LIMIT 1
            """,
            (str(chat_id),),
        ).fetchone()


def redeem_access_code(chat_id, code: str, telegram_username="", skynet_username=""):
    chat_id = str(chat_id)
    code = safe_text(code).strip()

    if not code:
        return False, "❌ Walay access code. Format: /redeemAccessCode 392051"

    active = get_active_access(chat_id)
    if active:
        return (
            False,
            "✅ Active pa imong access.\n"
            f"Code: {active['code']}\n"
            f"Expires: {active['expires_at']}",
        )

    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM access_codes WHERE code = ?",
            (code,),
        ).fetchone()

        if not row:
            return False, "❌ Invalid access code. Contact admin for correct code."

        if row["status"] == "unused" and not row["claimed_by_chat_id"]:
            activated_at = now_utc()
            expires_at = activated_at + timedelta(days=ACCESS_DAYS)

            conn.execute(
                """
                UPDATE access_codes
                SET status = 'active',
                    claimed_by_chat_id = ?,
                    claimed_telegram_username = ?,
                    claimed_skynet_username = ?,
                    activated_at = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    chat_id,
                    safe_text(telegram_username),
                    safe_text(skynet_username),
                    dt_to_str(activated_at),
                    dt_to_str(expires_at),
                    now_str(),
                    code,
                ),
            )

            return (
                True,
                "✅ Access code activated.\n"
                f"Countdown started now: {dt_to_str(activated_at)} UTC\n"
                f"Expiration: {dt_to_str(expires_at)} UTC\n\n"
                "Next step: /registerApi",
            )

        if row["claimed_by_chat_id"] == chat_id:
            expires_at = parse_dt(row["expires_at"])

            if row["status"] == "active" and expires_at and expires_at > now_utc():
                return (
                    False,
                    "✅ This code is already active for your account.\n"
                    f"Expires: {row['expires_at']}",
                )

            return (
                False,
                "❌ This code was already used and expired. Contact admin for a new code.",
            )

        return False, "❌ This access code was already used by another user."


def mark_expired_codes_and_notify():
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM access_codes
            WHERE status = 'active'
              AND expires_at IS NOT NULL
            """
        ).fetchall()

        for row in rows:
            expires_at = parse_dt(row["expires_at"])

            if not expires_at or expires_at > now_utc():
                continue

            conn.execute(
                """
                UPDATE access_codes
                SET status = 'expired',
                    expired_notified_at = COALESCE(expired_notified_at, ?),
                    updated_at = ?
                WHERE code = ?
                """,
                (now_str(), now_str(), row["code"]),
            )

            if not row["expired_notified_at"] and row["claimed_by_chat_id"]:
                tg_send(
                    row["claimed_by_chat_id"],
                    "⛔ Your 1-month access expired.\n\n"
                    f"Expired at: {row['expires_at']} UTC\n"
                    f"Payment/Admin: {TELEGRAM_ADMIN_LINK}\n\n"
                    "After payment, redeem a new access code.",
                )


def set_user_state(chat_id, state: str, payload=None):
    payload = payload or {}

    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO user_states (chat_id, state, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state = excluded.state,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (str(chat_id), state, json.dumps(payload), now_str()),
        )


def get_user_state(chat_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_states WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()

        if not row:
            return None, {}

        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}

        return row["state"], payload


def clear_user_state(chat_id):
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM user_states WHERE chat_id = ?",
            (str(chat_id),),
        )


# ============================================================
# TELEGRAM API
# ============================================================

def tg_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def tg_request(method: str, payload=None, timeout=15):
    if not TELEGRAM_ENABLED:
        return None

    if not TELEGRAM_BOT_TOKEN:
        core.log("TELEGRAM: missing TELEGRAM_BOT_TOKEN")
        return None

    try:
        response = requests.post(
            tg_api_url(method),
            json=payload or {},
            timeout=timeout,
        )
        data = response.json()

        if not data.get("ok"):
            core.log(f"TELEGRAM API ERROR {method}: {data}")

        return data

    except Exception as e:
        core.log(f"TELEGRAM REQUEST ERROR {method}: {e}")
        return None


def tg_send(chat_id, text: str, reply_markup=None):
    if not chat_id:
        return

    for part in chunks(text):
        payload = {
            "chat_id": str(chat_id),
            "text": part,
            "disable_web_page_preview": True,
        }

        if reply_markup:
            payload["reply_markup"] = reply_markup

        tg_request("sendMessage", payload)


def tg_admin(text: str):
    if TELEGRAM_ADMIN_CHAT_ID:
        tg_send(TELEGRAM_ADMIN_CHAT_ID, text)


def tg_delete_message(chat_id, message_id):
    if not TELEGRAM_DELETE_SENSITIVE_MESSAGES:
        return

    if not chat_id or not message_id:
        return

    tg_request(
        "deleteMessage",
        {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
        },
    )


def main_keyboard():
    return {
        "keyboard": [
            ["/paymentMethod", "/redeemAccessCode"],
            ["/registerApi", "/connectApi"],
            ["/walletUtaBalance", "/apiStatus"],
            ["/positions", "/1monthlyIncome"],
            ["/1Monthexpiration", "/monitorNow"],
            ["/myAccount", "/help"],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def tg_set_commands():
    commands = [
        {"command": "start", "description": "Start bot"},
        {"command": "menu", "description": "Show menu"},
        {"command": "paymentmethod", "description": "Payment/admin link"},
        {"command": "redeemaccesscode", "description": "Activate 1-month access"},
        {"command": "registerapi", "description": "Register Bybit API"},
        {"command": "connectapi", "description": "Test API connectivity"},
        {"command": "walletutabalance", "description": "Show UTA wallet balance"},
        {"command": "apistatus", "description": "Last API monitor status"},
        {"command": "positions", "description": "Show open positions"},
        {"command": "monthlyincome", "description": "This month closed PnL"},
        {"command": "expiration", "description": "Show subscription expiration"},
        {"command": "monitornow", "description": "Run monitor check now"},
        {"command": "myaccount", "description": "Show account details"},
        {"command": "deleteapi", "description": "Delete saved API credentials"},
        {"command": "help", "description": "Help"},
    ]

    tg_request("setMyCommands", {"commands": commands})


# ============================================================
# BYBIT MONITOR FUNCTIONS
# ============================================================

def make_user_bybit_session(api_key: str, api_secret: str) -> HTTP:
    return HTTP(
        testnet=core.TESTNET,
        api_key=api_key,
        api_secret=api_secret,
    )


def test_bybit_credentials(api_key: str, api_secret: str, label="TELEGRAM USER"):
    http = make_user_bybit_session(api_key, api_secret)
    balance = core.get_unified_usdt_wallet_for(http, label)

    return {
        "ok": True,
        "session": http,
        "balance": balance,
    }


def get_user_api_credentials(chat_id):
    user = get_user(chat_id)

    if not user:
        return None, None, "User not found."

    if not user["api_key_enc"] or not user["api_secret_enc"]:
        return None, None, "No API registered."

    try:
        api_key = decrypt_secret(user["api_key_enc"])
        api_secret = decrypt_secret(user["api_secret_enc"])
        return api_key, api_secret, ""

    except InvalidToken:
        return None, None, "Cannot decrypt API credentials. DATA_ENCRYPTION_KEY changed."

    except Exception as e:
        return None, None, str(e)


def save_user_api(chat_id, skynet_username: str, api_key: str, api_secret: str, balance=None):
    api_key_enc = encrypt_secret(api_key)
    api_secret_enc = encrypt_secret(api_secret)

    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET skynet_username = ?,
                api_key_enc = ?,
                api_secret_enc = ?,
                api_registered_at = ?,
                api_last_status = 'ok',
                api_last_checked_at = ?,
                api_last_balance_usdt = ?,
                api_last_error = '',
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                skynet_username,
                api_key_enc,
                api_secret_enc,
                now_str(),
                now_str(),
                safe_text(balance),
                now_str(),
                str(chat_id),
            ),
        )


def update_user_api_status(chat_id, status: str, balance="", error=""):
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET api_last_status = ?,
                api_last_checked_at = ?,
                api_last_balance_usdt = ?,
                api_last_error = ?,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                status,
                now_str(),
                safe_text(balance),
                safe_text(error)[:1000],
                now_str(),
                str(chat_id),
            ),
        )


def delete_user_api(chat_id):
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET api_key_enc = NULL,
                api_secret_enc = NULL,
                api_registered_at = NULL,
                api_last_status = NULL,
                api_last_checked_at = NULL,
                api_last_balance_usdt = NULL,
                api_last_error = NULL,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (now_str(), str(chat_id)),
        )


def run_user_api_check(chat_id, notify=True):
    user = get_user(chat_id)

    if not user:
        return False, "User not found."

    if not user_has_active_access(chat_id):
        return False, "Access expired or not activated."

    api_key, api_secret, err = get_user_api_credentials(chat_id)
    if err:
        return False, err

    label = f"TG {user['skynet_username'] or chat_id}"

    try:
        result = test_bybit_credentials(api_key, api_secret, label=label)
        balance = result["balance"]
        update_user_api_status(chat_id, "ok", balance=balance, error="")

        msg = (
            "✅ API CONNECTED\n"
            f"Username: {user['skynet_username'] or 'N/A'}\n"
            f"API Key: {mask_value(api_key)}\n"
            f"UTA Balance: {format_decimal(balance)} USDT\n"
            f"Checked: {now_str()} UTC"
        )

        if notify:
            tg_send(chat_id, msg)

        return True, msg

    except Exception as e:
        error_msg = str(e)
        update_user_api_status(chat_id, "error", balance="", error=error_msg)

        msg = (
            "❌ API CONNECTION FAILED\n"
            f"Username: {user['skynet_username'] or 'N/A'}\n"
            f"API Key: {mask_value(api_key)}\n"
            f"Error: {error_msg}\n"
            f"Checked: {now_str()} UTC"
        )

        if notify:
            tg_send(chat_id, msg)

        return False, msg


def get_user_positions_text(chat_id):
    user = get_user(chat_id)

    if not user:
        return "❌ User not found."

    if not user_has_active_access(chat_id):
        return require_access_text()

    api_key, api_secret, err = get_user_api_credentials(chat_id)
    if err:
        return f"❌ {err}\nUse /registerApi first."

    try:
        http = make_user_bybit_session(api_key, api_secret)
        positions = core.get_open_positions_for(
            http,
            f"TG {user['skynet_username'] or chat_id}",
        )

        if not positions:
            return "📭 No open positions."

        msg = "📈 OPEN POSITIONS\n\n"

        for p in positions:
            msg += (
                f"Symbol: {p['symbol']}\n"
                f"Side: {p['side']}\n"
                f"Size: {p['size']}\n"
                f"PnL: {p['unrealised_pnl']} USDT\n"
                f"Category: {p['category']}\n"
                "--------------------\n"
            )

        return msg

    except Exception as e:
        return f"❌ Failed to fetch positions: {e}"


def get_user_monthly_income_text(chat_id):
    user = get_user(chat_id)

    if not user:
        return "❌ User not found."

    if not user_has_active_access(chat_id):
        return require_access_text()

    api_key, api_secret, err = get_user_api_credentials(chat_id)
    if err:
        return f"❌ {err}\nUse /registerApi first."

    try:
        http = make_user_bybit_session(api_key, api_secret)

        start_dt = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(time.time() * 1000)

        total = Decimal("0")
        count = 0
        cursor = None

        while True:
            params = {
                "category": BYBIT_MONITOR_CATEGORY,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 100,
            }

            if cursor:
                params["cursor"] = cursor

            resp = core.require_ok(
                http.get_closed_pnl(**params),
                f"TG {chat_id} get_closed_pnl",
            )

            result = resp.get("result", {}) or {}
            items = result.get("list", []) or []

            for item in items:
                total += core.D(item.get("closedPnl"))
                count += 1

            cursor = result.get("nextPageCursor") or ""

            if not cursor:
                break

        return (
            "💰 1 MONTH INCOME / CLOSED PNL\n"
            f"From: {dt_to_str(start_dt)} UTC\n"
            f"To: {now_str()} UTC\n"
            f"Closed trades counted: {count}\n"
            f"Total closed PnL: {format_decimal(total)} USDT"
        )

    except AttributeError:
        return (
            "❌ pybit method get_closed_pnl not found.\n"
            "Update pybit to pybit==5.16.0 or newer."
        )

    except Exception as e:
        return f"❌ Failed to calculate monthly income: {e}"


# ============================================================
# TELEGRAM MESSAGE TEXT
# ============================================================

def require_access_text():
    return (
        "⛔ Access required.\n\n"
        "Step 1: Pay/contact admin:\n"
        f"{TELEGRAM_ADMIN_LINK}\n\n"
        "Step 2: Admin will give you access code.\n"
        "Step 3: Activate it:\n"
        "/redeemAccessCode YOUR_CODE\n\n"
        "After activation, register API:\n"
        "/registerApi"
    )


def menu_text(chat_id=None):
    access = get_active_access(chat_id) if chat_id else None
    user = get_user(chat_id) if chat_id else None

    access_line = "⛔ Not active"
    if access:
        access_line = f"✅ Active until {access['expires_at']} UTC"

    api_line = "❌ Not registered"
    if user and user["api_key_enc"] and user["api_secret_enc"]:
        api_line = f"✅ Registered as {user['skynet_username'] or 'N/A'}"

    return (
        "🤖 SKYNET7RADER MONITOR\n\n"
        f"Access: {access_line}\n"
        f"API: {api_line}\n\n"
        "COMMANDS:\n"
        "/paymentMethod - payment/admin link\n"
        "/redeemAccessCode CODE - activate 1 month\n"
        "/registerApi - register Bybit API\n"
        "/connectApi - test API key/secret connectivity\n"
        "/walletUtaBalance - show UTA wallet balance\n"
        "/apiStatus - last monitor status\n"
        "/positions - open positions\n"
        "/1monthlyIncome - current month closed PnL\n"
        "/1Monthexpiration - access expiration\n"
        "/monitorNow - check API now\n"
        "/myAccount - account info\n"
        "/deleteApi - remove saved API\n"
        "/help - show help\n\n"
        "Security tip: for monitoring only, use read-only Bybit API keys."
    )


# ============================================================
# COMMAND HANDLERS
# ============================================================

def handle_start(chat_id):
    tg_send(
        chat_id,
        menu_text(chat_id),
        reply_markup=main_keyboard(),
    )


def handle_payment_method(chat_id):
    tg_send(
        chat_id,
        "💳 PAYMENT METHOD / ADMIN\n\n"
        "Pay/contact the admin here:\n"
        f"{TELEGRAM_ADMIN_LINK}\n\n"
        "After payment, admin will give you a 1-month access code.\n\n"
        "Then activate using:\n"
        "/redeemAccessCode YOUR_CODE\n\n"
        "Example:\n"
        "/redeemAccessCode 392051",
        reply_markup=main_keyboard(),
    )


def handle_redeem(chat_id, args, telegram_username=""):
    if not args:
        tg_send(
            chat_id,
            "Enter access code like this:\n"
            "/redeemAccessCode 392051",
            reply_markup=main_keyboard(),
        )
        return

    code = args[0].strip()
    user = get_user(chat_id)
    skynet_username = user["skynet_username"] if user else ""

    ok, msg = redeem_access_code(
        chat_id=chat_id,
        code=code,
        telegram_username=telegram_username,
        skynet_username=skynet_username,
    )

    tg_send(chat_id, msg, reply_markup=main_keyboard())

    if ok:
        tg_admin(
            "✅ ACCESS CODE ACTIVATED\n"
            f"Chat ID: {chat_id}\n"
            f"Telegram: @{telegram_username or 'N/A'}\n"
            f"Code: {code}\n"
            f"Time: {now_str()} UTC"
        )


def validate_skynet_username(username: str):
    username = safe_text(username).strip()

    if len(username) < 3:
        return False, "Username too short. Minimum 3 characters."

    if len(username) > 32:
        return False, "Username too long. Maximum 32 characters."

    allowed = username.replace("_", "").replace("-", "")
    if not allowed.isalnum():
        return False, "Username can use letters, numbers, underscore, hyphen only."

    return True, ""


def handle_register_api(chat_id, args=None):
    args = args or []

    if not user_has_active_access(chat_id):
        tg_send(chat_id, require_access_text(), reply_markup=main_keyboard())
        return

    if not FERNET:
        tg_send(
            chat_id,
            "❌ DATA_ENCRYPTION_KEY is missing.\n\n"
            "Set DATA_ENCRYPTION_KEY in Render env vars first, then redeploy.\n"
            "This is required so saved API keys/secrets are encrypted.",
            reply_markup=main_keyboard(),
        )
        return

    if len(args) >= 3:
        skynet_username = args[0].strip()
        api_key = args[1].strip()
        api_secret = args[2].strip()
        complete_api_registration(chat_id, skynet_username, api_key, api_secret)
        return

    set_user_state(chat_id, "await_register_username", {})
    tg_send(
        chat_id,
        "📝 REGISTER API\n\n"
        "Step 1/3: Send your Skynet username.\n\n"
        "Example:\n"
        "john_trader\n\n"
        "Cancel: /cancel",
    )


def complete_api_registration(chat_id, skynet_username, api_key, api_secret):
    ok, err = validate_skynet_username(skynet_username)
    if not ok:
        tg_send(chat_id, f"❌ {err}\nUse /registerApi again.")
        return

    api_key = safe_text(api_key).strip()
    api_secret = safe_text(api_secret).strip()

    if not api_key or not api_secret:
        tg_send(chat_id, "❌ Missing API key or secret. Use /registerApi again.")
        return

    tg_send(chat_id, "🔄 Testing Bybit API connection...")

    try:
        result = test_bybit_credentials(
            api_key,
            api_secret,
            label=f"REGISTER {skynet_username}",
        )
        balance = result["balance"]

        save_user_api(
            chat_id=chat_id,
            skynet_username=skynet_username,
            api_key=api_key,
            api_secret=api_secret,
            balance=balance,
        )

        tg_send(
            chat_id,
            "✅ API REGISTERED & CONNECTED\n\n"
            f"Username: {skynet_username}\n"
            f"API Key: {mask_value(api_key)}\n"
            f"UTA Balance: {format_decimal(balance)} USDT\n"
            f"Checked: {now_str()} UTC\n\n"
            "You can now use:\n"
            "/connectApi\n"
            "/walletUtaBalance\n"
            "/apiStatus",
            reply_markup=main_keyboard(),
        )

        tg_admin(
            "✅ USER API REGISTERED\n"
            f"Chat ID: {chat_id}\n"
            f"Username: {skynet_username}\n"
            f"API Key: {mask_value(api_key)}\n"
            f"UTA Balance: {format_decimal(balance)} USDT"
        )

    except Exception as e:
        tg_send(
            chat_id,
            "❌ API TEST FAILED\n\n"
            f"Error: {e}\n\n"
            "Check if API key/secret is correct.\n"
            "For monitor only, read-only API permission is enough.\n"
            "Use /registerApi to try again.",
            reply_markup=main_keyboard(),
        )


def handle_state_message(chat_id, text, message_id=None):
    state, payload = get_user_state(chat_id)

    if not state:
        tg_send(chat_id, "Type /menu to see commands.", reply_markup=main_keyboard())
        return

    if state == "await_register_username":
        username = safe_text(text).strip()
        ok, err = validate_skynet_username(username)

        if not ok:
            tg_send(chat_id, f"❌ {err}\nSend another username or /cancel.")
            return

        payload["skynet_username"] = username
        set_user_state(chat_id, "await_register_api_key", payload)

        tg_send(
            chat_id,
            "Step 2/3: Send your Bybit API KEY.\n\n"
            "Security tip: create a read-only key if this is only for monitoring.\n"
            "Cancel: /cancel",
        )
        return

    if state == "await_register_api_key":
        api_key = safe_text(text).strip()
        tg_delete_message(chat_id, message_id)

        if not api_key:
            tg_send(chat_id, "❌ API key is empty. Send again or /cancel.")
            return

        payload["api_key"] = api_key
        set_user_state(chat_id, "await_register_api_secret", payload)

        tg_send(
            chat_id,
            "Step 3/3: Send your Bybit API SECRET.\n\n"
            "I will try to delete your sensitive message after receiving it.\n"
            "Cancel: /cancel",
        )
        return

    if state == "await_register_api_secret":
        api_secret = safe_text(text).strip()
        tg_delete_message(chat_id, message_id)

        if not api_secret:
            tg_send(chat_id, "❌ API secret is empty. Use /registerApi again.")
            clear_user_state(chat_id)
            return

        skynet_username = payload.get("skynet_username", "")
        api_key = payload.get("api_key", "")

        clear_user_state(chat_id)
        complete_api_registration(chat_id, skynet_username, api_key, api_secret)
        return

    clear_user_state(chat_id)
    tg_send(chat_id, "State reset. Type /menu.", reply_markup=main_keyboard())


def handle_connect_api(chat_id):
    if not user_has_active_access(chat_id):
        tg_send(chat_id, require_access_text(), reply_markup=main_keyboard())
        return

    ok, msg = run_user_api_check(chat_id, notify=False)
    tg_send(chat_id, msg, reply_markup=main_keyboard())


def handle_wallet_balance(chat_id):
    if not user_has_active_access(chat_id):
        tg_send(chat_id, require_access_text(), reply_markup=main_keyboard())
        return

    user = get_user(chat_id)
    api_key, api_secret, err = get_user_api_credentials(chat_id)

    if err:
        tg_send(chat_id, f"❌ {err}\nUse /registerApi first.", reply_markup=main_keyboard())
        return

    try:
        result = test_bybit_credentials(
            api_key,
            api_secret,
            label=f"WALLET {user['skynet_username'] or chat_id}",
        )
        balance = result["balance"]

        update_user_api_status(chat_id, "ok", balance=balance, error="")

        tg_send(
            chat_id,
            "💰 WALLET UTA BALANCE\n\n"
            f"Username: {user['skynet_username'] or 'N/A'}\n"
            f"UTA USDT: {format_decimal(balance)} USDT\n"
            f"Checked: {now_str()} UTC",
            reply_markup=main_keyboard(),
        )

    except Exception as e:
        update_user_api_status(chat_id, "error", balance="", error=str(e))
        tg_send(
            chat_id,
            "❌ Failed to fetch wallet balance.\n"
            f"Error: {e}",
            reply_markup=main_keyboard(),
        )


def handle_api_status(chat_id):
    user = get_user(chat_id)

    if not user:
        tg_send(chat_id, "❌ User not found. Use /start.")
        return

    access = get_active_access(chat_id)
    access_line = "⛔ No active access"

    if access:
        access_line = f"✅ Active until {access['expires_at']} UTC"

    api_registered = bool(user["api_key_enc"] and user["api_secret_enc"])

    msg = (
        "📡 API STATUS\n\n"
        f"Access: {access_line}\n"
        f"Username: {user['skynet_username'] or 'N/A'}\n"
        f"API registered: {'✅ Yes' if api_registered else '❌ No'}\n"
        f"Last status: {user['api_last_status'] or 'N/A'}\n"
        f"Last checked: {user['api_last_checked_at'] or 'N/A'} UTC\n"
        f"Last balance: {user['api_last_balance_usdt'] or 'N/A'} USDT\n"
    )

    if user["api_last_error"]:
        msg += f"Last error: {user['api_last_error']}\n"

    tg_send(chat_id, msg, reply_markup=main_keyboard())


def handle_expiration(chat_id):
    access = get_active_access(chat_id)

    if access:
        expires = parse_dt(access["expires_at"])
        remaining = expires - now_utc() if expires else None
        days = remaining.days if remaining else 0
        hours = remaining.seconds // 3600 if remaining else 0

        tg_send(
            chat_id,
            "🗓️ 1 MONTH EXPIRATION\n\n"
            f"Code: {access['code']}\n"
            f"Activated: {access['activated_at']} UTC\n"
            f"Expires: {access['expires_at']} UTC\n"
            f"Remaining: {days} days, {hours} hours",
            reply_markup=main_keyboard(),
        )
        return

    latest = get_latest_access(chat_id)

    if latest:
        tg_send(
            chat_id,
            "⛔ No active access.\n\n"
            f"Latest code: {latest['code']}\n"
            f"Status: {latest['status']}\n"
            f"Expired: {latest['expires_at'] or 'N/A'} UTC\n\n"
            f"Admin: {TELEGRAM_ADMIN_LINK}",
            reply_markup=main_keyboard(),
        )
    else:
        tg_send(chat_id, require_access_text(), reply_markup=main_keyboard())


def handle_my_account(chat_id):
    user = get_user(chat_id)
    access = get_active_access(chat_id)

    if not user:
        tg_send(chat_id, "❌ User not found. Use /start.")
        return

    msg = (
        "👤 MY ACCOUNT\n\n"
        f"Chat ID: {chat_id}\n"
        f"Telegram: @{user['telegram_username'] or 'N/A'}\n"
        f"Skynet username: {user['skynet_username'] or 'N/A'}\n"
        f"API registered: {'✅ Yes' if user['api_key_enc'] else '❌ No'}\n"
        f"API last status: {user['api_last_status'] or 'N/A'}\n"
        f"API last checked: {user['api_last_checked_at'] or 'N/A'} UTC\n"
    )

    if access:
        msg += (
            "\nACCESS:\n"
            f"Status: ✅ Active\n"
            f"Code: {access['code']}\n"
            f"Expires: {access['expires_at']} UTC\n"
        )
    else:
        msg += "\nACCESS:\nStatus: ⛔ Not active\n"

    tg_send(chat_id, msg, reply_markup=main_keyboard())


def handle_delete_api(chat_id):
    delete_user_api(chat_id)
    clear_user_state(chat_id)

    tg_send(
        chat_id,
        "✅ Saved API key/secret deleted from server.\n"
        "You can register again using /registerApi.",
        reply_markup=main_keyboard(),
    )


def handle_admin_users(chat_id):
    if str(chat_id) != str(TELEGRAM_ADMIN_CHAT_ID):
        tg_send(chat_id, "❌ Admin only.")
        return

    with db_connect() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        registered = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE api_key_enc IS NOT NULL"
        ).fetchone()["c"]
        active_codes = conn.execute(
            "SELECT COUNT(*) AS c FROM access_codes WHERE status = 'active'"
        ).fetchone()["c"]
        unused_codes = conn.execute(
            "SELECT COUNT(*) AS c FROM access_codes WHERE status = 'unused'"
        ).fetchone()["c"]

        rows = conn.execute(
            """
            SELECT chat_id, telegram_username, skynet_username, api_last_status, api_last_checked_at
            FROM users
            ORDER BY updated_at DESC
            LIMIT 20
            """
        ).fetchall()

    msg = (
        "🛡️ ADMIN USERS\n\n"
        f"Total users: {total_users}\n"
        f"Registered APIs: {registered}\n"
        f"Active codes: {active_codes}\n"
        f"Unused codes: {unused_codes}\n\n"
        "Recent users:\n"
    )

    for row in rows:
        msg += (
            f"- {row['skynet_username'] or 'N/A'} "
            f"@{row['telegram_username'] or 'N/A'} "
            f"status={row['api_last_status'] or 'N/A'} "
            f"checked={row['api_last_checked_at'] or 'N/A'}\n"
        )

    tg_send(chat_id, msg)


def handle_command(chat_id, text, telegram_username=""):
    parts = safe_text(text).strip().split()
    if not parts:
        return

    command_raw = parts[0].split("@")[0]
    command = command_raw.lower()
    args = parts[1:]

    if command in ("/start",):
        clear_user_state(chat_id)
        handle_start(chat_id)

    elif command in ("/menu", "/help"):
        clear_user_state(chat_id)
        tg_send(chat_id, menu_text(chat_id), reply_markup=main_keyboard())

    elif command in ("/paymentmethod", "/payment"):
        clear_user_state(chat_id)
        handle_payment_method(chat_id)

    elif command in ("/redeemaccesscode", "/accesscode", "/login", "/redeem"):
        clear_user_state(chat_id)
        handle_redeem(chat_id, args, telegram_username=telegram_username)

    elif command in ("/registerapi", "/register"):
        clear_user_state(chat_id)
        handle_register_api(chat_id, args)

    elif command in ("/connectapi", "/connect"):
        clear_user_state(chat_id)
        handle_connect_api(chat_id)

    elif command in ("/walletutabalance", "/balance", "/wallet"):
        clear_user_state(chat_id)
        handle_wallet_balance(chat_id)

    elif command in ("/apistatus", "/status"):
        clear_user_state(chat_id)
        handle_api_status(chat_id)

    elif command in ("/positions", "/position"):
        clear_user_state(chat_id)
        tg_send(chat_id, get_user_positions_text(chat_id), reply_markup=main_keyboard())

    elif command in ("/1monthlyincome", "/monthlyincome", "/income"):
        clear_user_state(chat_id)
        tg_send(chat_id, get_user_monthly_income_text(chat_id), reply_markup=main_keyboard())

    elif command in ("/1monthexpiration", "/expiration", "/expire"):
        clear_user_state(chat_id)
        handle_expiration(chat_id)

    elif command in ("/monitornow", "/checknow"):
        clear_user_state(chat_id)
        handle_connect_api(chat_id)

    elif command in ("/myaccount", "/account"):
        clear_user_state(chat_id)
        handle_my_account(chat_id)

    elif command in ("/deleteapi", "/removeapi"):
        clear_user_state(chat_id)
        handle_delete_api(chat_id)

    elif command in ("/cancel",):
        clear_user_state(chat_id)
        tg_send(chat_id, "✅ Cancelled.", reply_markup=main_keyboard())

    elif command in ("/adminusers",):
        clear_user_state(chat_id)
        handle_admin_users(chat_id)

    else:
        tg_send(
            chat_id,
            "❓ Unknown command.\nType /menu.",
            reply_markup=main_keyboard(),
        )


def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message") or {}
    if not message:
        return

    chat = message.get("chat", {}) or {}
    from_user = message.get("from", {}) or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")

    if not chat_id:
        return

    if chat_type != "private":
        tg_send(chat_id, "⚠️ For API registration, please message me in private chat.")
        return

    telegram_username = from_user.get("username", "")
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")

    ensure_user(
        chat_id=chat_id,
        telegram_username=telegram_username,
        first_name=first_name,
        last_name=last_name,
    )

    text = safe_text(message.get("text", "")).strip()
    message_id = message.get("message_id")

    if not text:
        tg_send(chat_id, "Type /menu to see commands.", reply_markup=main_keyboard())
        return

    if text.startswith("/"):
        handle_command(chat_id, text, telegram_username=telegram_username)
    else:
        handle_state_message(chat_id, text, message_id=message_id)


def telegram_poll_commands():
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN:
        return

    try:
        last_update_id = int(db_get_meta("telegram_last_update_id", "0"))
    except Exception:
        last_update_id = 0

    params = {
        "offset": last_update_id + 1,
        "timeout": TELEGRAM_POLL_TIMEOUT_SEC,
        "limit": TELEGRAM_PROCESS_LIMIT,
    }

    try:
        response = requests.get(
            tg_api_url("getUpdates"),
            params=params,
            timeout=TELEGRAM_POLL_TIMEOUT_SEC + 10,
        )
        data = response.json()

        if not data.get("ok"):
            core.log(f"TELEGRAM getUpdates error: {data}")
            return

        updates = data.get("result", []) or []

        for update in updates:
            update_id = update.get("update_id")

            if update_id is not None:
                db_set_meta("telegram_last_update_id", str(update_id))

            try:
                handle_update(update)
            except Exception as e:
                core.log(f"TELEGRAM handle_update error: {e}")
                traceback.print_exc()

    except Exception as e:
        core.log(f"TELEGRAM poll error: {e}")


# ============================================================
# BACKGROUND API CONNECTIVITY MONITOR
# ============================================================

LAST_MONITOR_RUN_TS = 0


def monitor_registered_users_connectivity(force=False):
    global LAST_MONITOR_RUN_TS

    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN:
        return

    now_ts = time.time()

    if not force and (now_ts - LAST_MONITOR_RUN_TS) < TELEGRAM_MONITOR_INTERVAL_SEC:
        return

    LAST_MONITOR_RUN_TS = now_ts

    mark_expired_codes_and_notify()

    with db_connect() as conn:
        users = conn.execute(
            """
            SELECT *
            FROM users
            WHERE api_key_enc IS NOT NULL
              AND api_secret_enc IS NOT NULL
            """
        ).fetchall()

    for user in users:
        chat_id = user["chat_id"]

        if not user_has_active_access(chat_id):
            continue

        old_status = user["api_last_status"] or ""

        try:
            api_key = decrypt_secret(user["api_key_enc"])
            api_secret = decrypt_secret(user["api_secret_enc"])

            result = test_bybit_credentials(
                api_key,
                api_secret,
                label=f"MONITOR {user['skynet_username'] or chat_id}",
            )
            balance = result["balance"]
            update_user_api_status(chat_id, "ok", balance=balance, error="")

            if old_status != "ok" or TELEGRAM_MONITOR_SEND_OK_EVERY_CHECK:
                tg_send(
                    chat_id,
                    "🟢 API CONNECTIVITY RESTORED / OK\n"
                    f"Username: {user['skynet_username'] or 'N/A'}\n"
                    f"UTA Balance: {format_decimal(balance)} USDT\n"
                    f"Checked: {now_str()} UTC",
                )

        except Exception as e:
            error_msg = str(e)
            update_user_api_status(chat_id, "error", balance="", error=error_msg)

            if old_status != "error":
                msg = (
                    "🔴 API CONNECTIVITY ERROR\n"
                    f"Username: {user['skynet_username'] or 'N/A'}\n"
                    f"Error: {error_msg}\n"
                    f"Checked: {now_str()} UTC\n\n"
                    "Please check API key/secret permissions or IP restrictions."
                )
                tg_send(chat_id, msg)

                tg_admin(
                    "🔴 USER API ERROR\n"
                    f"Chat ID: {chat_id}\n"
                    f"Username: {user['skynet_username'] or 'N/A'}\n"
                    f"Error: {error_msg}"
                )

            log_monitor(chat_id, "error", error_msg)


# ============================================================
# MAIN WRAPPER
# This keeps the old app.py logic untouched.
# Sequence:
# 1. Telegram commands
# 2. old API #01 reserve/top-up/loss logic
# 3. old copy-trade logic
# 4. old follower reserve/top-up/loss logic
# 5. Telegram monitor
# ============================================================

def main():
    db_init()
    tg_set_commands()

    core.log("Skynet7rader Telegram Monitor Wrapper started")
    core.log(f"Telegram enabled: {TELEGRAM_ENABLED}")
    core.log(f"Telegram admin link: {TELEGRAM_ADMIN_LINK}")
    core.log(f"Database path: {BOT_DB_PATH}")
    core.log(f"Access days: {ACCESS_DAYS}")
    core.log(f"Loaded access codes: {len(ACCESS_CODES)}")
    core.log("Old app.py logic remains imported and untouched")

    tg_admin(
        "🟢 Skynet7rader monitor started\n"
        f"Mode: {core.MODE}\n"
        f"Testnet: {core.TESTNET}\n"
        f"Time: {now_str()} UTC"
    )

    while True:
        telegram_poll_commands()

        # OLD LOGIC #1: master reserve/top-up/loss-close
        core.run_cycle()

        # OLD LOGIC #2: copy trade from API #01 to API #02-#50
        core.copy_trade_sync_from_master()

        # OLD LOGIC #3: follower reserve/top-up/loss-close
        core.run_follower_reserve_cycles()

        # NEW TELEGRAM MONITOR: registered users API connectivity
        monitor_registered_users_connectivity()

        telegram_poll_commands()

        time.sleep(core.BOT_SLEEP_SEC)


if __name__ == "__main__":
    main()
