import os
import time
import uuid
import traceback
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime

from pybit.unified_trading import HTTP

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


MODE = os.getenv("BYBIT_MODE", "live").strip().lower()
TESTNET = MODE != "live"


def first_env(*names, default=""):
    """
    Return the first non-empty env var value.
    Placeholder values are ignored so incomplete .env rows for #02-#50
    do not break startup.
    """
    placeholder_prefixes = (
        "PASTE_",
        "YOUR_",
        "CHANGE_ME",
        "REPLACE_ME",
        "PUT_",
    )

    for name in names:
        value = os.getenv(name)
        if value is None:
            continue

        value = str(value).strip()
        if not value:
            continue

        upper_value = value.upper()
        if upper_value.startswith(placeholder_prefixes):
            continue

        return value

    return default


def env_bool(name, default="false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )


def env_int(name, default="0") -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return int(default)


def env_float(name, default="0") -> float:
    try:
        return float(str(os.getenv(name, default)).strip())
    except Exception:
        return float(default)


def env_decimal(name, default="0") -> Decimal:
    try:
        return Decimal(str(os.getenv(name, default)).strip())
    except Exception:
        return Decimal(str(default))


# ============================================================
# API CREDENTIALS
#
# API #01 = MASTER
#   BYBIT_API_KEY / BYBIT_API_SECRET
#   or BYBIT_API_KEY_01 / BYBIT_API_SECRET_01
#
# API #02-#50 = FOLLOWERS
#   BYBIT_API_KEY_02 / BYBIT_API_SECRET_02
#   ...
#   BYBIT_API_KEY_50 / BYBIT_API_SECRET_50
#
# IMPORTANT:
# Do not hard-code real API keys in this file.
# Put secrets in Render env vars or local .env only.
# ============================================================
API_KEY = first_env("BYBIT_API_KEY", "BYBIT_API_KEY_01", "BYBIT_API_KEY_1")
API_SECRET = first_env("BYBIT_API_SECRET", "BYBIT_API_SECRET_01", "BYBIT_API_SECRET_1")

COIN = "USDT"

# These values still come from Render Environment Variables or .env.
RESERVE_USDT = Decimal(os.getenv("RESERVE_USDT", "501"))
MIN_TRANSFER_USDT = Decimal(os.getenv("MIN_TRANSFER_USDT", "1"))
POSITION_TOPUP_USDT = Decimal(os.getenv("POSITION_TOPUP_USDT", "50"))
LOSS_CLOSE_USDT = Decimal(os.getenv("LOSS_CLOSE_USDT", "70"))
BOT_SLEEP_SEC = int(os.getenv("BOT_SLEEP_SEC", "15"))

POSITION_LOCK_FILE = os.getenv(
    "POSITION_LOCK_FILE",
    "/tmp/bybit_position_topup.lock"
).strip()


# ============================================================
# MULTI-ACCOUNT SETTINGS
# ============================================================
MAX_API_ACCOUNTS = env_int("MAX_API_ACCOUNTS", os.getenv("COPY_MAX_ACCOUNTS", "50"))

# Turn this on so API #02-#50 also run:
# - reserve logic
# - surplus transfer UNIFIED -> FUND
# - position top-up FUND -> UNIFIED
# - loss-close logic
FOLLOWER_RESERVE_ENABLED = env_bool("FOLLOWER_RESERVE_ENABLED", "true")
FOLLOWER_SLEEP_BETWEEN_ACCOUNTS_SEC = env_float("FOLLOWER_SLEEP_BETWEEN_ACCOUNTS_SEC", "0.25")


# ============================================================
# COPY-TRADE SETTINGS
# API #01 is master. API #02-#50 follow master.
# Default symbol: BTCUSDT.
# Set COPY_SYMBOLS=ALL to copy all monitored linear USDT positions.
# ============================================================
COPY_TRADE_ENABLED = env_bool("COPY_TRADE_ENABLED", "true")
COPY_CATEGORY = os.getenv("COPY_CATEGORY", "linear").strip().lower()
COPY_SYMBOLS_RAW = os.getenv("COPY_SYMBOLS", "BTCUSDT").strip().upper()
COPY_SYMBOLS = None if COPY_SYMBOLS_RAW in ("ALL", "*") else {
    s.strip().upper()
    for s in COPY_SYMBOLS_RAW.split(",")
    if s.strip()
}
COPY_SIZE_MULTIPLIER = env_decimal("COPY_SIZE_MULTIPLIER", "1")
COPY_SET_LEVERAGE = env_bool("COPY_SET_LEVERAGE", "true")
COPY_CLOSE_EXTRA_POSITIONS = env_bool("COPY_CLOSE_EXTRA_POSITIONS", "true")
COPY_SLEEP_BETWEEN_ACCOUNTS_SEC = env_float("COPY_SLEEP_BETWEEN_ACCOUNTS_SEC", "0.25")
COPY_DRY_RUN = env_bool("COPY_DRY_RUN", "false")


if not API_KEY or not API_SECRET:
    print("ERROR: Missing master API key/secret. Set BYBIT_API_KEY and BYBIT_API_SECRET.", flush=True)
    raise SystemExit(1)


session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def D(v):
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def fmt_amount(x: Decimal) -> str:
    s = format(x.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def require_ok(resp, context="request"):
    if not isinstance(resp, dict):
        raise RuntimeError(f"{context} returned non-dict response: {resp}")

    ret_code = resp.get("retCode", 0)
    if ret_code != 0:
        raise RuntimeError(
            f"{context} failed: retCode={ret_code} "
            f"retMsg={resp.get('retMsg')} resp={resp}"
        )

    return resp


# ============================================================
# OLD / ORIGINAL MASTER LOGIC
# These functions intentionally keep the same behavior for API #01.
# API #02-#50 use added *_for_account functions below, so the master
# logic stays intact.
# ============================================================

def position_lock_exists() -> bool:
    try:
        return os.path.exists(POSITION_LOCK_FILE)
    except Exception:
        return False


def create_position_lock():
    try:
        with open(POSITION_LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(datetime.now()))
    except Exception as e:
        log(f"FAILED TO CREATE LOCK FILE: {e}")


def clear_position_lock():
    try:
        if os.path.exists(POSITION_LOCK_FILE):
            os.remove(POSITION_LOCK_FILE)
            log("POSITION LOCK CLEARED")
    except Exception as e:
        log(f"FAILED TO CLEAR LOCK FILE: {e}")


def extract_coin_balance_from_any_result(resp, coin=COIN) -> Decimal:
    result = (resp or {}).get("result", {}) or {}

    balance_keys = (
        "walletBalance",
        "transferBalance",
        "transferSafeAmount",
        "availableToWithdraw",
        "availableBalance",
        "withdrawableAmount",
        "amount",
        "balance",
    )

    bal = result.get("balance")

    if isinstance(bal, dict):
        for key in balance_keys:
            if key in bal:
                return D(bal.get(key))

    if isinstance(bal, list):
        for item in bal:
            item_coin = item.get("coin") or item.get("coinName")
            if item_coin == coin:
                for key in balance_keys:
                    if key in item:
                        return D(item.get(key))

    lst = result.get("list", []) or []

    for item in lst:
        item_coin = item.get("coin") or item.get("coinName")

        if item_coin == coin:
            for key in balance_keys:
                if key in item:
                    return D(item.get(key))

        for c in item.get("coin", []) or []:
            if c.get("coin") == coin:
                for key in balance_keys:
                    if key in c:
                        return D(c.get(key))

    for key in balance_keys:
        if key in result:
            return D(result.get(key))

    return Decimal("0")


def get_unified_usdt_wallet() -> Decimal:
    r = require_ok(
        session.get_wallet_balance(accountType="UNIFIED", coin=COIN),
        "get_wallet_balance UNIFIED"
    )

    result = r.get("result", {}) or {}
    rows = result.get("list", []) or []

    for row in rows:
        for c in row.get("coin", []) or []:
            if c.get("coin") == COIN:
                return D(c.get("walletBalance"))

    raise RuntimeError(f"UNIFIED {COIN} walletBalance not found in response: {r}")


def get_fund_usdt_wallet() -> Decimal:
    errors = []

    try:
        r = require_ok(
            session.get_coin_balance(accountType="FUND", coin=COIN),
            "get_coin_balance FUND"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        log(f"FUND balance source = get_coin_balance -> {amt} {COIN}")
        return amt
    except Exception as e:
        errors.append(f"get_coin_balance FUND failed: {e}")

    try:
        r = require_ok(
            session.get_coins_balance(accountType="FUND"),
            "get_coins_balance FUND"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        log(f"FUND balance source = get_coins_balance -> {amt} {COIN}")
        return amt
    except Exception as e:
        errors.append(f"get_coins_balance FUND failed: {e}")

    raise RuntimeError(" | ".join(errors))


def get_transferable_amount_unified() -> Decimal:
    errors = []

    try:
        r = session.get_transferable_amount(coinName=COIN)

        if isinstance(r, dict) and r.get("retCode", 0) == 0:
            res = r.get("result", {}) or {}

            amt = D(res.get("availableWithdrawal"))
            if amt > 0:
                return amt

            mp = res.get("availableWithdrawalMap", {}) or {}
            amt = D(mp.get(COIN))
            if amt > 0:
                return amt

    except Exception as e:
        errors.append(f"get_transferable_amount failed: {e}")

    try:
        r = require_ok(
            session.get_coin_balance(
                accountType="UNIFIED",
                toAccountType="FUND",
                coin=COIN,
                withTransferSafeAmount=1
            ),
            "get_coin_balance UNIFIED transfer-safe"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        if amt > 0:
            return amt

    except Exception as e:
        errors.append(f"get_coin_balance transfer-safe failed: {e}")

    try:
        r = require_ok(
            session.get_coins_balance(accountType="UNIFIED", coin=COIN),
            "get_coins_balance UNIFIED"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        if amt > 0:
            return amt

    except Exception as e:
        errors.append(f"get_coins_balance UNIFIED failed: {e}")

    log("TRANSFERABLE FALLBACK -> returning 0 | " + " | ".join(errors))
    return Decimal("0")


def get_qty_step(symbol: str, category: str = "linear") -> Decimal:
    try:
        r = require_ok(
            session.get_instruments_info(category=category, symbol=symbol),
            f"get_instruments_info {category} {symbol}"
        )

        items = r.get("result", {}).get("list", []) or []
        if not items:
            return Decimal("0.001")

        lot = items[0].get("lotSizeFilter", {}) or {}
        step = lot.get("qtyStep", "0.001")
        step_d = D(step)

        return step_d if step_d > 0 else Decimal("0.001")

    except Exception as e:
        log(f"get_qty_step failed for {symbol}: {e}")
        return Decimal("0.001")


def round_qty_by_step(qty: Decimal, step: Decimal) -> Decimal:
    try:
        if step <= 0:
            return qty
        return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    except Exception:
        return qty


def close_position_market(category: str, symbol: str, side: str, size: Decimal, position_idx=None):
    try:
        qty_step = get_qty_step(symbol, category)
        qty = round_qty_by_step(size, qty_step)

        if qty <= 0:
            log(f"SKIP CLOSE: invalid qty for {symbol}")
            return

        close_side = "Sell" if str(side).lower() == "buy" else "Buy"

        params = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": True,
            "closeOnTrigger": True,
        }

        if position_idx is not None:
            try:
                params["positionIdx"] = int(position_idx)
            except Exception:
                pass

        resp = require_ok(
            session.place_order(**params),
            f"place_order close {category} {symbol}"
        )

        log(f"CLOSE ORDER SENT -> {category} {symbol} {close_side} qty={qty}")
        log(f"RESP: {resp}")

    except Exception as e:
        log(f"FAILED TO CLOSE POSITION {symbol}: {e}")
        traceback.print_exc()


def get_open_positions():
    checks = [
        ("linear", {"settleCoin": "USDT"}),
        ("inverse", {}),
        ("option", {}),
    ]

    found = []

    for category, extra in checks:
        cursor = None

        while True:
            params = {
                "category": category,
                "limit": 200,
            }
            params.update(extra)

            if cursor:
                params["cursor"] = cursor

            try:
                r = require_ok(
                    session.get_positions(**params),
                    f"get_positions {category}"
                )
            except Exception as e:
                log(f"get_positions failed for {category}: {e}")
                break

            result = r.get("result", {}) or {}
            plist = result.get("list", []) or []

            for p in plist:
                size = D(p.get("size"))

                if size <= 0:
                    continue

                found.append({
                    "category": category,
                    "symbol": p.get("symbol", "?"),
                    "side": p.get("side", ""),
                    "size": size,
                    "position_idx": p.get("positionIdx"),
                    "unrealised_pnl": D(
                        p.get("unrealisedPnl")
                        or p.get("unrealizedPnl")
                        or p.get("unrealisedProfit")
                        or "0"
                    ),
                })

            cursor = result.get("nextPageCursor") or ""

            if not cursor:
                break

    return found


def monitor_and_close_on_loss(open_positions):
    trigger_loss = -abs(LOSS_CLOSE_USDT)

    for p in open_positions:
        category = p["category"]
        sym = p["symbol"]
        side = p["side"]
        size = p["size"]
        position_idx = p["position_idx"]
        unrealised_pnl = p["unrealised_pnl"]

        log(
            f"POSITION CHECK -> {category} {sym} {side} "
            f"size={size} unrealisedPnl={unrealised_pnl}"
        )

        if unrealised_pnl <= trigger_loss:
            log(
                f"LOSS LIMIT HIT -> {category} {sym} {side} "
                f"size={size} pnl={unrealised_pnl}"
            )
            close_position_market(category, sym, side, size, position_idx)


def transfer_excess_to_fund(open_positions):
    if open_positions:
        log("SKIP TRANSFER: naay open position")
        return

    wallet_usdt = get_unified_usdt_wallet()
    transferable = get_transferable_amount_unified()
    excess = wallet_usdt - RESERVE_USDT

    log(f"UNIFIED walletBalance = {wallet_usdt} {COIN}")
    log(f"Transferable amount = {transferable} {COIN}")
    log(f"Reserve target      = {RESERVE_USDT} {COIN}")

    if excess <= 0:
        log("NO TRANSFER: walay subra sa reserve")
        return

    amount = min(excess, transferable)
    amount = q2(amount)

    if amount <= 0:
        log("NO TRANSFER: invalid rounded amount")
        return

    if amount < MIN_TRANSFER_USDT:
        log(f"NO TRANSFER: gamay ra kaayo ({amount} < {MIN_TRANSFER_USDT})")
        return

    try:
        resp = require_ok(
            session.create_internal_transfer(
                transferId=str(uuid.uuid4()),
                coin=COIN,
                amount=fmt_amount(amount),
                fromAccountType="UNIFIED",
                toAccountType="FUND",
            ),
            "create_internal_transfer UNIFIED->FUND"
        )

        log(f"TRANSFER SUCCESS: {amount} {COIN} UNIFIED -> FUND")
        log(f"RESP: {resp}")

    except Exception as e:
        log(f"TRANSFER FAILED: {e}")
        traceback.print_exc()


def transfer_fund_to_unified_when_position_once(open_positions):
    if not open_positions:
        if position_lock_exists():
            clear_position_lock()

        log("NO POSITION: skip FUND -> UNIFIED top-up")
        return

    for p in open_positions:
        log(
            f"OPEN POSITION FOUND -> {p['category']} "
            f"{p['symbol']} {p['side']} size={p['size']}"
        )

    if position_lock_exists():
        log("TOP-UP ALREADY DONE FOR CURRENT POSITION CYCLE")
        return

    amount = q2(POSITION_TOPUP_USDT)
    fund_wallet = get_fund_usdt_wallet()

    log(f"FUND walletBalance   = {fund_wallet} {COIN}")
    log(f"Position top-up need = {amount} {COIN}")

    if amount <= 0:
        log("NO TRANSFER: invalid POSITION_TOPUP_USDT")
        return

    if fund_wallet < amount:
        log(f"NO TRANSFER: kulang ang pondo sa FUND ({fund_wallet} < {amount})")
        return

    try:
        resp = require_ok(
            session.create_internal_transfer(
                transferId=str(uuid.uuid4()),
                coin=COIN,
                amount=fmt_amount(amount),
                fromAccountType="FUND",
                toAccountType="UNIFIED",
            ),
            "create_internal_transfer FUND->UNIFIED"
        )

        log(f"TRANSFER SUCCESS: {amount} {COIN} FUND -> UNIFIED")
        log(f"RESP: {resp}")

        create_position_lock()
        log("POSITION LOCK CREATED")

    except Exception as e:
        log(f"FUND -> UNIFIED TRANSFER FAILED: {e}")
        traceback.print_exc()


def run_cycle():
    try:
        open_positions = get_open_positions()
    except Exception as e:
        log(f"GET OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        monitor_and_close_on_loss(open_positions)
    except Exception as e:
        log(f"MONITOR/CLOSE ERROR: {e}")
        traceback.print_exc()

    try:
        open_positions = get_open_positions()
    except Exception as e:
        log(f"REFRESH OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        transfer_excess_to_fund(open_positions)
    except Exception as e:
        log(f"FATAL CYCLE ERROR: {e}")
        traceback.print_exc()

    try:
        transfer_fund_to_unified_when_position_once(open_positions)
    except Exception as e:
        log(f"POSITION TOP-UP ERROR: {e}")
        traceback.print_exc()


# ============================================================
# MULTI-ACCOUNT ADD-ON
# API #02-#50 will run the same reserve/top-up/loss-close logic
# without changing the old API #01 functions above.
# ============================================================

def make_account_session(api_key: str, api_secret: str) -> HTTP:
    return HTTP(
        testnet=TESTNET,
        api_key=api_key,
        api_secret=api_secret,
    )


def get_indexed_secret(prefix: str, idx: int) -> str:
    return first_env(
        f"{prefix}_{idx:02d}",
        f"{prefix}_{idx}",
        default=""
    )


def get_account_lock_file(idx: int) -> str:
    if idx == 1:
        return POSITION_LOCK_FILE

    root, ext = os.path.splitext(POSITION_LOCK_FILE)

    if ext:
        return f"{root}_{idx:02d}{ext}"

    return f"{POSITION_LOCK_FILE}_{idx:02d}.lock"


def load_accounts():
    accounts = [{
        "idx": 1,
        "label": "API#01 MASTER",
        "session": session,
        "lock_file": get_account_lock_file(1),
        "is_master": True,
    }]

    for idx in range(2, MAX_API_ACCOUNTS + 1):
        key = get_indexed_secret("BYBIT_API_KEY", idx)
        secret = get_indexed_secret("BYBIT_API_SECRET", idx)

        if not key or not secret:
            continue

        try:
            accounts.append({
                "idx": idx,
                "label": f"API#{idx:02d}",
                "session": make_account_session(key, secret),
                "lock_file": get_account_lock_file(idx),
                "is_master": False,
            })
        except Exception as e:
            log(f"API#{idx:02d}: failed to create HTTP session: {e}")

    return accounts


ACCOUNTS = load_accounts()
FOLLOWER_ACCOUNTS = [a for a in ACCOUNTS if not a["is_master"]]


def position_lock_exists_for(lock_file: str) -> bool:
    try:
        return os.path.exists(lock_file)
    except Exception:
        return False


def create_position_lock_for(lock_file: str, label: str):
    try:
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write(str(datetime.now()))
    except Exception as e:
        log(f"{label}: FAILED TO CREATE LOCK FILE: {e}")


def clear_position_lock_for(lock_file: str, label: str):
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            log(f"{label}: POSITION LOCK CLEARED")
    except Exception as e:
        log(f"{label}: FAILED TO CLEAR LOCK FILE: {e}")


def get_unified_usdt_wallet_for(http_session: HTTP, label: str) -> Decimal:
    r = require_ok(
        http_session.get_wallet_balance(accountType="UNIFIED", coin=COIN),
        f"{label} get_wallet_balance UNIFIED"
    )

    result = r.get("result", {}) or {}
    rows = result.get("list", []) or []

    for row in rows:
        for c in row.get("coin", []) or []:
            if c.get("coin") == COIN:
                return D(c.get("walletBalance"))

    raise RuntimeError(f"{label}: UNIFIED {COIN} walletBalance not found in response: {r}")


def get_fund_usdt_wallet_for(http_session: HTTP, label: str) -> Decimal:
    errors = []

    try:
        r = require_ok(
            http_session.get_coin_balance(accountType="FUND", coin=COIN),
            f"{label} get_coin_balance FUND"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        log(f"{label}: FUND balance source = get_coin_balance -> {amt} {COIN}")
        return amt
    except Exception as e:
        errors.append(f"get_coin_balance FUND failed: {e}")

    try:
        r = require_ok(
            http_session.get_coins_balance(accountType="FUND"),
            f"{label} get_coins_balance FUND"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        log(f"{label}: FUND balance source = get_coins_balance -> {amt} {COIN}")
        return amt
    except Exception as e:
        errors.append(f"get_coins_balance FUND failed: {e}")

    raise RuntimeError(f"{label}: " + " | ".join(errors))


def get_transferable_amount_unified_for(http_session: HTTP, label: str) -> Decimal:
    errors = []

    try:
        r = http_session.get_transferable_amount(coinName=COIN)

        if isinstance(r, dict) and r.get("retCode", 0) == 0:
            res = r.get("result", {}) or {}

            amt = D(res.get("availableWithdrawal"))
            if amt > 0:
                return amt

            mp = res.get("availableWithdrawalMap", {}) or {}
            amt = D(mp.get(COIN))
            if amt > 0:
                return amt

    except Exception as e:
        errors.append(f"get_transferable_amount failed: {e}")

    try:
        r = require_ok(
            http_session.get_coin_balance(
                accountType="UNIFIED",
                toAccountType="FUND",
                coin=COIN,
                withTransferSafeAmount=1
            ),
            f"{label} get_coin_balance UNIFIED transfer-safe"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        if amt > 0:
            return amt

    except Exception as e:
        errors.append(f"get_coin_balance transfer-safe failed: {e}")

    try:
        r = require_ok(
            http_session.get_coins_balance(accountType="UNIFIED", coin=COIN),
            f"{label} get_coins_balance UNIFIED"
        )
        amt = extract_coin_balance_from_any_result(r, COIN)
        if amt > 0:
            return amt

    except Exception as e:
        errors.append(f"get_coins_balance UNIFIED failed: {e}")

    log(f"{label}: TRANSFERABLE FALLBACK -> returning 0 | " + " | ".join(errors))
    return Decimal("0")


def get_qty_step_for(http_session: HTTP, label: str, symbol: str, category: str = "linear") -> Decimal:
    try:
        r = require_ok(
            http_session.get_instruments_info(category=category, symbol=symbol),
            f"{label} get_instruments_info {category} {symbol}"
        )

        items = r.get("result", {}).get("list", []) or []
        if not items:
            return Decimal("0.001")

        lot = items[0].get("lotSizeFilter", {}) or {}
        step = lot.get("qtyStep", "0.001")
        step_d = D(step)

        return step_d if step_d > 0 else Decimal("0.001")

    except Exception as e:
        log(f"{label}: get_qty_step failed for {symbol}: {e}")
        return Decimal("0.001")


def close_position_market_for(
    http_session: HTTP,
    label: str,
    category: str,
    symbol: str,
    side: str,
    size: Decimal,
    position_idx=None,
):
    try:
        qty_step = get_qty_step_for(http_session, label, symbol, category)
        qty = round_qty_by_step(size, qty_step)

        if qty <= 0:
            log(f"{label}: SKIP CLOSE: invalid qty for {symbol}")
            return

        close_side = "Sell" if str(side).lower() == "buy" else "Buy"

        params = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": True,
            "closeOnTrigger": True,
        }

        if position_idx is not None:
            try:
                params["positionIdx"] = int(position_idx)
            except Exception:
                pass

        resp = http_session.place_order(**params)

        if isinstance(resp, dict) and resp.get("retCode", 0) == 10001:
            msg = str(resp.get("retMsg", "")).lower()
            if "position idx" in msg or "positionidx" in msg:
                retry_params = dict(params)
                retry_params.pop("positionIdx", None)
                log(f"{label}: retry close without positionIdx -> {symbol}")
                resp = http_session.place_order(**retry_params)

        require_ok(
            resp,
            f"{label} place_order close {category} {symbol}"
        )

        log(f"{label}: CLOSE ORDER SENT -> {category} {symbol} {close_side} qty={qty}")
        log(f"{label}: RESP: {resp}")

    except Exception as e:
        log(f"{label}: FAILED TO CLOSE POSITION {symbol}: {e}")
        traceback.print_exc()


def get_open_positions_for(http_session: HTTP, label: str):
    checks = [
        ("linear", {"settleCoin": "USDT"}),
        ("inverse", {}),
        ("option", {}),
    ]

    found = []

    for category, extra in checks:
        cursor = None

        while True:
            params = {
                "category": category,
                "limit": 200,
            }
            params.update(extra)

            if cursor:
                params["cursor"] = cursor

            try:
                r = require_ok(
                    http_session.get_positions(**params),
                    f"{label} get_positions {category}"
                )
            except Exception as e:
                log(f"{label}: get_positions failed for {category}: {e}")
                break

            result = r.get("result", {}) or {}
            plist = result.get("list", []) or []

            for p in plist:
                size = D(p.get("size"))

                if size <= 0:
                    continue

                found.append({
                    "category": category,
                    "symbol": p.get("symbol", "?"),
                    "side": p.get("side", ""),
                    "size": size,
                    "position_idx": p.get("positionIdx"),
                    "unrealised_pnl": D(
                        p.get("unrealisedPnl")
                        or p.get("unrealizedPnl")
                        or p.get("unrealisedProfit")
                        or "0"
                    ),
                })

            cursor = result.get("nextPageCursor") or ""

            if not cursor:
                break

    return found


def monitor_and_close_on_loss_for(account: dict, open_positions):
    http_session = account["session"]
    label = account["label"]
    trigger_loss = -abs(LOSS_CLOSE_USDT)

    for p in open_positions:
        category = p["category"]
        sym = p["symbol"]
        side = p["side"]
        size = p["size"]
        position_idx = p["position_idx"]
        unrealised_pnl = p["unrealised_pnl"]

        log(
            f"{label}: POSITION CHECK -> {category} {sym} {side} "
            f"size={size} unrealisedPnl={unrealised_pnl}"
        )

        if unrealised_pnl <= trigger_loss:
            log(
                f"{label}: LOSS LIMIT HIT -> {category} {sym} {side} "
                f"size={size} pnl={unrealised_pnl}"
            )
            close_position_market_for(
                http_session=http_session,
                label=label,
                category=category,
                symbol=sym,
                side=side,
                size=size,
                position_idx=position_idx,
            )


def transfer_excess_to_fund_for(account: dict, open_positions):
    http_session = account["session"]
    label = account["label"]

    if open_positions:
        log(f"{label}: SKIP TRANSFER: naay open position")
        return

    wallet_usdt = get_unified_usdt_wallet_for(http_session, label)
    transferable = get_transferable_amount_unified_for(http_session, label)
    excess = wallet_usdt - RESERVE_USDT

    log(f"{label}: UNIFIED walletBalance = {wallet_usdt} {COIN}")
    log(f"{label}: Transferable amount = {transferable} {COIN}")
    log(f"{label}: Reserve target      = {RESERVE_USDT} {COIN}")

    if excess <= 0:
        log(f"{label}: NO TRANSFER: walay subra sa reserve")
        return

    amount = min(excess, transferable)
    amount = q2(amount)

    if amount <= 0:
        log(f"{label}: NO TRANSFER: invalid rounded amount")
        return

    if amount < MIN_TRANSFER_USDT:
        log(f"{label}: NO TRANSFER: gamay ra kaayo ({amount} < {MIN_TRANSFER_USDT})")
        return

    try:
        resp = require_ok(
            http_session.create_internal_transfer(
                transferId=str(uuid.uuid4()),
                coin=COIN,
                amount=fmt_amount(amount),
                fromAccountType="UNIFIED",
                toAccountType="FUND",
            ),
            f"{label} create_internal_transfer UNIFIED->FUND"
        )

        log(f"{label}: TRANSFER SUCCESS: {amount} {COIN} UNIFIED -> FUND")
        log(f"{label}: RESP: {resp}")

    except Exception as e:
        log(f"{label}: TRANSFER FAILED: {e}")
        traceback.print_exc()


def transfer_fund_to_unified_when_position_once_for(account: dict, open_positions):
    http_session = account["session"]
    label = account["label"]
    lock_file = account["lock_file"]

    if not open_positions:
        if position_lock_exists_for(lock_file):
            clear_position_lock_for(lock_file, label)

        log(f"{label}: NO POSITION: skip FUND -> UNIFIED top-up")
        return

    for p in open_positions:
        log(
            f"{label}: OPEN POSITION FOUND -> {p['category']} "
            f"{p['symbol']} {p['side']} size={p['size']}"
        )

    if position_lock_exists_for(lock_file):
        log(f"{label}: TOP-UP ALREADY DONE FOR CURRENT POSITION CYCLE")
        return

    amount = q2(POSITION_TOPUP_USDT)
    fund_wallet = get_fund_usdt_wallet_for(http_session, label)

    log(f"{label}: FUND walletBalance   = {fund_wallet} {COIN}")
    log(f"{label}: Position top-up need = {amount} {COIN}")

    if amount <= 0:
        log(f"{label}: NO TRANSFER: invalid POSITION_TOPUP_USDT")
        return

    if fund_wallet < amount:
        log(f"{label}: NO TRANSFER: kulang ang pondo sa FUND ({fund_wallet} < {amount})")
        return

    try:
        resp = require_ok(
            http_session.create_internal_transfer(
                transferId=str(uuid.uuid4()),
                coin=COIN,
                amount=fmt_amount(amount),
                fromAccountType="FUND",
                toAccountType="UNIFIED",
            ),
            f"{label} create_internal_transfer FUND->UNIFIED"
        )

        log(f"{label}: TRANSFER SUCCESS: {amount} {COIN} FUND -> UNIFIED")
        log(f"{label}: RESP: {resp}")

        create_position_lock_for(lock_file, label)
        log(f"{label}: POSITION LOCK CREATED -> {lock_file}")

    except Exception as e:
        log(f"{label}: FUND -> UNIFIED TRANSFER FAILED: {e}")
        traceback.print_exc()


def run_reserve_cycle_for_account(account: dict):
    label = account["label"]
    http_session = account["session"]

    try:
        open_positions = get_open_positions_for(http_session, label)
    except Exception as e:
        log(f"{label}: GET OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        monitor_and_close_on_loss_for(account, open_positions)
    except Exception as e:
        log(f"{label}: MONITOR/CLOSE ERROR: {e}")
        traceback.print_exc()

    try:
        open_positions = get_open_positions_for(http_session, label)
    except Exception as e:
        log(f"{label}: REFRESH OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        transfer_excess_to_fund_for(account, open_positions)
    except Exception as e:
        log(f"{label}: FATAL CYCLE ERROR: {e}")
        traceback.print_exc()

    try:
        transfer_fund_to_unified_when_position_once_for(account, open_positions)
    except Exception as e:
        log(f"{label}: POSITION TOP-UP ERROR: {e}")
        traceback.print_exc()


def run_follower_reserve_cycles():
    if not FOLLOWER_RESERVE_ENABLED:
        return

    if not FOLLOWER_ACCOUNTS:
        log("FOLLOWER RESERVE: enabled but no API #02-#50 keys loaded")
        return

    for account in FOLLOWER_ACCOUNTS:
        try:
            run_reserve_cycle_for_account(account)
        except Exception as e:
            log(f"{account['label']}: follower reserve cycle error: {e}")
            traceback.print_exc()

        if FOLLOWER_SLEEP_BETWEEN_ACCOUNTS_SEC > 0:
            time.sleep(FOLLOWER_SLEEP_BETWEEN_ACCOUNTS_SEC)


# ============================================================
# COPY-TRADE ADD-ON
# API #01 is master. API #02-#50 copy:
# - side
# - quantity
# - leverage
# - close when master closes
# ============================================================

def copy_require_ok(resp, context="copy request", tolerate_codes=None):
    tolerate_codes = set(tolerate_codes or [])

    if not isinstance(resp, dict):
        raise RuntimeError(f"{context} returned non-dict response: {resp}")

    ret_code = resp.get("retCode", 0)
    if ret_code == 0 or ret_code in tolerate_codes:
        return resp

    raise RuntimeError(
        f"{context} failed: retCode={ret_code} "
        f"retMsg={resp.get('retMsg')} resp={resp}"
    )


def symbol_allowed_for_copy(symbol: str) -> bool:
    sym = str(symbol or "").upper()
    return COPY_SYMBOLS is None or sym in COPY_SYMBOLS


def copy_get_qty_step(http_session: HTTP, label: str, symbol: str, category: str = "linear") -> Decimal:
    return get_qty_step_for(http_session, label, symbol, category)


def copy_round_qty(http_session: HTTP, label: str, category: str, symbol: str, qty: Decimal) -> Decimal:
    step = copy_get_qty_step(http_session, label, symbol, category)
    return round_qty_by_step(qty, step)


def copy_position_key(position: dict):
    return (
        str(position.get("symbol", "")).upper(),
        str(position.get("side", "")).capitalize(),
    )


def copy_normalize_position(category: str, p: dict):
    size = D(p.get("size"))

    if size <= 0:
        return None

    symbol = str(p.get("symbol") or "").upper()
    side = str(p.get("side") or "").capitalize()

    if not symbol or side not in ("Buy", "Sell"):
        return None

    if not symbol_allowed_for_copy(symbol):
        return None

    leverage_raw = p.get("leverage")
    leverage = D(leverage_raw)

    return {
        "category": category,
        "symbol": symbol,
        "side": side,
        "size": size,
        "position_idx": p.get("positionIdx"),
        "leverage": leverage,
        "leverage_raw": str(leverage_raw or ""),
        "unrealised_pnl": D(
            p.get("unrealisedPnl")
            or p.get("unrealizedPnl")
            or p.get("unrealisedProfit")
            or "0"
        ),
    }


def copy_get_open_positions_for_session(http_session: HTTP, label: str):
    category = COPY_CATEGORY
    found = []

    if category not in ("linear", "inverse"):
        log(f"COPY {label}: unsupported COPY_CATEGORY={category}. Use linear or inverse.")
        return found

    cursor = None

    while True:
        params = {
            "category": category,
            "limit": 200,
        }

        if category == "linear":
            params["settleCoin"] = COIN

        if cursor:
            params["cursor"] = cursor

        try:
            r = copy_require_ok(
                http_session.get_positions(**params),
                f"COPY {label} get_positions {category}"
            )
        except Exception as e:
            log(f"COPY {label}: get_positions failed for {category}: {e}")
            break

        result = r.get("result", {}) or {}
        plist = result.get("list", []) or []

        for p in plist:
            normalized = copy_normalize_position(category, p)
            if normalized:
                found.append(normalized)

        cursor = result.get("nextPageCursor") or ""

        if not cursor:
            break

    return found


def copy_get_position_mode(http_session: HTTP, category: str, symbol: str, label: str) -> str:
    try:
        r = copy_require_ok(
            http_session.get_positions(category=category, symbol=symbol),
            f"COPY {label} detect position mode {category} {symbol}"
        )

        result = r.get("result", {}) or {}
        plist = result.get("list", []) or []

        for p in plist:
            try:
                idx = int(p.get("positionIdx", 0))
            except Exception:
                idx = 0

            if idx in (1, 2):
                return "hedge"

    except Exception as e:
        log(f"COPY {label}: cannot detect position mode for {symbol}: {e}")

    return "oneway"


def copy_position_idx_for_side(
    http_session: HTTP,
    category: str,
    symbol: str,
    position_side: str,
    label: str,
):
    mode = copy_get_position_mode(http_session, category, symbol, label)

    if mode == "hedge":
        return 1 if str(position_side).capitalize() == "Buy" else 2

    return 0


def copy_format_decimal(x: Decimal) -> str:
    if x <= 0:
        return "0"

    s = format(x.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def copy_set_leverage_if_needed(
    http_session: HTTP,
    label: str,
    category: str,
    symbol: str,
    leverage: Decimal,
):
    if not COPY_SET_LEVERAGE:
        return

    if leverage <= 0:
        return

    lev = copy_format_decimal(leverage)

    if COPY_DRY_RUN:
        log(f"COPY {label}: DRY RUN set leverage {symbol} buy/sell={lev}")
        return

    try:
        resp = http_session.set_leverage(
            category=category,
            symbol=symbol,
            buyLeverage=lev,
            sellLeverage=lev,
        )

        ret_code = resp.get("retCode") if isinstance(resp, dict) else None
        ret_msg = str(resp.get("retMsg", "") if isinstance(resp, dict) else resp).lower()

        if ret_code == 0:
            log(f"COPY {label}: leverage synced -> {symbol} {lev}x")
            return

        if ret_code == 110043 or "not modified" in ret_msg or "same" in ret_msg:
            log(f"COPY {label}: leverage already same -> {symbol} {lev}x")
            return

        copy_require_ok(resp, f"COPY {label} set_leverage {symbol}")

    except Exception as e:
        log(f"COPY {label}: set leverage failed for {symbol}: {e}")


def copy_market_order(
    http_session: HTTP,
    label: str,
    category: str,
    symbol: str,
    side: str,
    qty: Decimal,
    reduce_only: bool,
    position_side: str,
    position_idx=None,
):
    qty = copy_round_qty(http_session, label, category, symbol, qty)

    if qty <= 0:
        log(f"COPY {label}: skip order {symbol}, invalid qty after rounding")
        return

    params = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": copy_format_decimal(qty),
    }

    if reduce_only:
        params["reduceOnly"] = True
        params["closeOnTrigger"] = True

    if position_idx is None:
        position_idx = copy_position_idx_for_side(
            http_session,
            category,
            symbol,
            position_side,
            label,
        )

    if position_idx is not None:
        try:
            params["positionIdx"] = int(position_idx)
        except Exception:
            pass

    if COPY_DRY_RUN:
        log(f"COPY {label}: DRY RUN order params={params}")
        return

    try:
        resp = http_session.place_order(**params)

        if isinstance(resp, dict) and resp.get("retCode", 0) == 10001:
            msg = str(resp.get("retMsg", "")).lower()
            if "position idx" in msg or "positionidx" in msg:
                retry_params = dict(params)
                retry_params.pop("positionIdx", None)
                log(f"COPY {label}: retry order without positionIdx -> {symbol}")
                resp = http_session.place_order(**retry_params)

        copy_require_ok(
            resp,
            f"COPY {label} place_order {category} {symbol} {side} qty={qty}"
        )

        action = "CLOSE/REDUCE" if reduce_only else "OPEN/ADD"
        log(f"COPY {label}: {action} order sent -> {symbol} {side} qty={qty}")

    except Exception as e:
        log(f"COPY {label}: order failed {symbol} {side} qty={qty}: {e}")
        traceback.print_exc()


def copy_close_position(http_session: HTTP, label: str, position: dict, qty: Decimal = None):
    category = position["category"]
    symbol = position["symbol"]
    position_side = position["side"]
    close_side = "Sell" if position_side == "Buy" else "Buy"
    size = qty if qty is not None else position["size"]

    copy_market_order(
        http_session=http_session,
        label=label,
        category=category,
        symbol=symbol,
        side=close_side,
        qty=size,
        reduce_only=True,
        position_side=position_side,
        position_idx=position.get("position_idx"),
    )


def copy_open_or_add_position(
    http_session: HTTP,
    label: str,
    target: dict,
    qty: Decimal,
):
    copy_set_leverage_if_needed(
        http_session=http_session,
        label=label,
        category=target["category"],
        symbol=target["symbol"],
        leverage=target.get("leverage", Decimal("0")),
    )

    copy_market_order(
        http_session=http_session,
        label=label,
        category=target["category"],
        symbol=target["symbol"],
        side=target["side"],
        qty=qty,
        reduce_only=False,
        position_side=target["side"],
        position_idx=None,
    )


def copy_sync_one_follower(account: dict, master_positions: list):
    label = account["label"]
    http_session = account["session"]

    follower_positions = copy_get_open_positions_for_session(http_session, label)

    master_by_key = {copy_position_key(p): p for p in master_positions}
    follower_by_key = {copy_position_key(p): p for p in follower_positions}

    if not master_by_key:
        log(f"COPY {label}: master has no monitored open position")

    # Close follower positions that the master no longer has.
    if COPY_CLOSE_EXTRA_POSITIONS:
        for key, actual in follower_by_key.items():
            if key not in master_by_key:
                log(
                    f"COPY {label}: extra position found -> close "
                    f"{actual['symbol']} {actual['side']} qty={actual['size']}"
                )
                copy_close_position(http_session, label, actual)

    # Match every master position.
    for key, target in master_by_key.items():
        desired_qty = target["size"] * COPY_SIZE_MULTIPLIER
        desired_qty = copy_round_qty(
            http_session,
            label,
            target["category"],
            target["symbol"],
            desired_qty,
        )

        if desired_qty <= 0:
            log(f"COPY {label}: desired qty <= 0 for {target['symbol']}, skip")
            continue

        log(
            f"COPY {label}: target -> {target['symbol']} {target['side']} "
            f"qty={desired_qty} leverage={target.get('leverage_raw') or target.get('leverage')}"
        )

        actual = follower_by_key.get(key)

        if actual is None:
            log(
                f"COPY {label}: missing position -> open "
                f"{target['symbol']} {target['side']} qty={desired_qty}"
            )
            copy_open_or_add_position(http_session, label, target, desired_qty)
            continue

        actual_qty = copy_round_qty(
            http_session,
            label,
            actual["category"],
            actual["symbol"],
            actual["size"],
        )

        diff = desired_qty - actual_qty
        step = copy_get_qty_step(http_session, label, target["symbol"], target["category"])

        if abs(diff) < step:
            copy_set_leverage_if_needed(
                http_session=http_session,
                label=label,
                category=target["category"],
                symbol=target["symbol"],
                leverage=target.get("leverage", Decimal("0")),
            )
            log(
                f"COPY {label}: already matched -> "
                f"{target['symbol']} {target['side']} qty={actual_qty}"
            )
            continue

        if diff > 0:
            log(
                f"COPY {label}: add qty -> {target['symbol']} "
                f"{target['side']} add={diff}"
            )
            copy_open_or_add_position(http_session, label, target, diff)
        else:
            reduce_qty = abs(diff)
            log(
                f"COPY {label}: reduce qty -> {actual['symbol']} "
                f"{actual['side']} reduce={reduce_qty}"
            )
            copy_set_leverage_if_needed(
                http_session=http_session,
                label=label,
                category=target["category"],
                symbol=target["symbol"],
                leverage=target.get("leverage", Decimal("0")),
            )
            copy_close_position(http_session, label, actual, reduce_qty)


def copy_trade_sync_from_master():
    if not COPY_TRADE_ENABLED:
        return

    if not FOLLOWER_ACCOUNTS:
        log("COPY: enabled but no follower API keys found from #02-#50")
        return

    master_positions = copy_get_open_positions_for_session(session, "MASTER/API#01")

    if master_positions:
        for p in master_positions:
            log(
                f"COPY MASTER: {p['category']} {p['symbol']} {p['side']} "
                f"qty={p['size']} leverage={p.get('leverage_raw') or p.get('leverage')} "
                f"positionIdx={p.get('position_idx')}"
            )
    else:
        log("COPY MASTER: no monitored open positions")

    for account in FOLLOWER_ACCOUNTS:
        try:
            copy_sync_one_follower(account, master_positions)
        except Exception as e:
            log(f"COPY {account['label']}: sync error: {e}")
            traceback.print_exc()

        if COPY_SLEEP_BETWEEN_ACCOUNTS_SEC > 0:
            time.sleep(COPY_SLEEP_BETWEEN_ACCOUNTS_SEC)


def main():
    log("Bybit Reserve + Copy Trade Bot started on Render worker")
    log(f"Mode: {MODE}")
    log(f"Testnet: {TESTNET}")
    log(f"Reserve USDT: {RESERVE_USDT}")
    log(f"Sleep seconds: {BOT_SLEEP_SEC}")
    log(f"Max API accounts: {MAX_API_ACCOUNTS}")
    log(f"Accounts loaded: {len(ACCOUNTS)}")
    log(f"Followers loaded: {len(FOLLOWER_ACCOUNTS)}")
    log("Master old reserve/top-up/loss-close logic: ENABLED for API#01")
    log(f"Follower reserve/top-up/loss-close logic: {FOLLOWER_RESERVE_ENABLED}")
    log(f"Follower account delay: {FOLLOWER_SLEEP_BETWEEN_ACCOUNTS_SEC}s")

    if FOLLOWER_ACCOUNTS:
        for account in FOLLOWER_ACCOUNTS:
            log(f"{account['label']}: lock file -> {account['lock_file']}")

    if COPY_TRADE_ENABLED:
        log("Copy trade: ENABLED")
        log("Copy master: API#01")
        log(f"Copy category: {COPY_CATEGORY}")
        log(f"Copy symbols: {COPY_SYMBOLS_RAW}")
        log(f"Copy size multiplier: {COPY_SIZE_MULTIPLIER}")
        log(f"Copy set leverage: {COPY_SET_LEVERAGE}")
        log(f"Copy close extra positions: {COPY_CLOSE_EXTRA_POSITIONS}")
        log(f"Copy dry run: {COPY_DRY_RUN}")
    else:
        log("Copy trade: DISABLED")

    while True:
        # 1. API #01 runs the exact old reserve/top-up/loss-close cycle.
        run_cycle()

        # 2. API #02-#50 copy API #01 trading activity.
        copy_trade_sync_from_master()

        # 3. API #02-#50 run their own reserve/top-up/loss-close cycle.
        run_follower_reserve_cycles()

        time.sleep(BOT_SLEEP_SEC)


if __name__ == "__main__":
    main()
