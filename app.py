import os
import time
import uuid
import traceback
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime

from pybit.unified_trading import HTTP


MODE = os.getenv("BYBIT_MODE", "live").strip().lower()
TESTNET = MODE != "live"

# ============================================================
# HARD CODED BYBIT API CREDENTIALS
# Paste your NEW Bybit API key and secret here.
# Warning: Do not use a public GitHub repo with hard-coded keys.
# ============================================================
API_KEY = "Mj8lClA90BOjNUidPI"
API_SECRET = "etAdJ9i1gEhFIU1YNPP1nUzeXsPfaXZaRcmx"

COIN = "USDT"

# These values still come from Render Environment Variables or .env.
RESERVE_USDT = Decimal(os.getenv("RESERVE_USDT", "401"))
MIN_TRANSFER_USDT = Decimal(os.getenv("MIN_TRANSFER_USDT", "1"))
POSITION_TOPUP_USDT = Decimal(os.getenv("POSITION_TOPUP_USDT", "50"))
LOSS_CLOSE_USDT = Decimal(os.getenv("LOSS_CLOSE_USDT", "70"))
BOT_SLEEP_SEC = int(os.getenv("BOT_SLEEP_SEC", "15"))

POSITION_LOCK_FILE = os.getenv(
    "POSITION_LOCK_FILE",
    "/tmp/bybit_position_topup.lock"
).strip()


if not API_KEY or not API_SECRET:
    print("ERROR: Missing hard-coded API_KEY or API_SECRET", flush=True)
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
            session.get_coins_balance(accountType="UNIFIED"),
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


def main():
    log("Bybit Reserve Bot started on Render worker")
    log(f"Mode: {MODE}")
    log(f"Testnet: {TESTNET}")
    log(f"Reserve USDT: {RESERVE_USDT}")
    log(f"Sleep seconds: {BOT_SLEEP_SEC}")

    while True:
        run_cycle()
        time.sleep(BOT_SLEEP_SEC)


if __name__ == "__main__":
    main()
