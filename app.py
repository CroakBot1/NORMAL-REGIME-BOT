import os
import time
import uuid
import traceback
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime

from pybit.unified_trading import HTTP


MODE = os.getenv("BYBIT_MODE", "live").strip().lower()
TESTNET = MODE != "live"

COIN = "USDT"

RESERVE_USDT = Decimal(os.getenv("RESERVE_USDT", "501"))
MIN_TRANSFER_USDT = Decimal(os.getenv("MIN_TRANSFER_USDT", "1"))
POSITION_TOPUP_USDT = Decimal(os.getenv("POSITION_TOPUP_USDT", "50"))

# Use positive value in env.
# Example: LOSS_CLOSE_USDT=0.01 means close when unrealisedPnl <= -0.01
# If you put negative value, code still converts it safely with -abs().
LOSS_CLOSE_USDT = Decimal(os.getenv("LOSS_CLOSE_USDT", "0.01"))

BOT_SLEEP_SEC = int(os.getenv("BOT_SLEEP_SEC", "15"))

POSITION_LOCK_FILE = os.getenv(
    "POSITION_LOCK_FILE",
    "/tmp/bybit_position_topup.lock"
).strip()


# ============================================================
# COPY TRADE SETTINGS
# Account #1 = leader.
# Accounts #2 to #50 = followers.
# ============================================================

COPY_TRADE_ENABLED = os.getenv("COPY_TRADE_ENABLED", "false").strip().lower() == "true"
COPY_TRADE_LEADER_ACCOUNT = int(os.getenv("COPY_TRADE_LEADER_ACCOUNT", "1"))
COPY_TRADE_FOLLOWERS_START = int(os.getenv("COPY_TRADE_FOLLOWERS_START", "2"))
COPY_TRADE_FOLLOWERS_END = int(os.getenv("COPY_TRADE_FOLLOWERS_END", "50"))
COPY_TRADE_LEVERAGE = Decimal(os.getenv("COPY_TRADE_LEVERAGE", "3"))
COPY_TRADE_WALLET_PCT = Decimal(os.getenv("COPY_TRADE_WALLET_PCT", "0.10"))
COPY_TRADE_MIN_ORDER_USDT = Decimal(os.getenv("COPY_TRADE_MIN_ORDER_USDT", "5"))
COPY_TRADE_REQUIRE_NO_FOLLOWER_POSITION = (
    os.getenv("COPY_TRADE_REQUIRE_NO_FOLLOWER_POSITION", "true").strip().lower() == "true"
)
COPY_TRADE_LOCK_PREFIX = os.getenv(
    "COPY_TRADE_LOCK_PREFIX",
    "/tmp/bybit_copy_trade.lock"
).strip()


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


def is_placeholder_credential(value: str) -> bool:
    """
    Prevent fake template values from being loaded as real API keys.

    Examples skipped:
      your_api_key_2_here
      your_api_secret_2_here
      changeme
      replace_me
    """
    v = (value or "").strip().lower()

    if not v:
        return True

    placeholders = (
        "your_api_key",
        "your_api_secret",
        "your_first_api",
        "your_second_api",
        "your_third_api",
        "your_",
        "_here",
        "changeme",
        "change_me",
        "replace_me",
        "replace",
        "example",
        "sample",
    )

    return any(p in v for p in placeholders)


def load_bybit_accounts():
    """
    Loads up to 50 Bybit API credential pairs.

    Valid:
      BYBIT_API_KEY_1
      BYBIT_API_SECRET_1

    Optional:
      BYBIT_API_KEY_2
      BYBIT_API_SECRET_2
      ...
      BYBIT_API_KEY_50
      BYBIT_API_SECRET_50

    Placeholder values are skipped so account #2 to #50 templates
    will not break account #1.
    """
    accounts = []

    for i in range(1, 51):
        key = os.getenv(f"BYBIT_API_KEY_{i}", "").strip()
        secret = os.getenv(f"BYBIT_API_SECRET_{i}", "").strip()

        key_is_placeholder = is_placeholder_credential(key)
        secret_is_placeholder = is_placeholder_credential(secret)

        if key_is_placeholder and secret_is_placeholder:
            continue

        if key_is_placeholder or secret_is_placeholder:
            raise RuntimeError(
                f"Invalid or missing BYBIT_API_KEY_{i} / BYBIT_API_SECRET_{i}. "
                f"Remove placeholders or set real credentials."
            )

        session = HTTP(
            testnet=TESTNET,
            api_key=key,
            api_secret=secret,
        )

        accounts.append({
            "index": i,
            "name": f"account_{i}",
            "session": session,
            "lock_file": f"{POSITION_LOCK_FILE}.{i}",
            "copy_lock_file": f"{COPY_TRADE_LOCK_PREFIX}.{i}",
        })

    if not accounts:
        raise RuntimeError(
            "No valid Bybit API credentials found. "
            "Set BYBIT_API_KEY_1 and BYBIT_API_SECRET_1 in Render Environment."
        )

    return accounts


def position_lock_exists(lock_file: str) -> bool:
    try:
        return os.path.exists(lock_file)
    except Exception:
        return False


def create_position_lock(lock_file: str):
    try:
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write(str(datetime.now()))
    except Exception as e:
        log(f"FAILED TO CREATE LOCK FILE: {e}")


def clear_position_lock(lock_file: str):
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
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


def get_unified_usdt_wallet(session) -> Decimal:
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


def get_fund_usdt_wallet(session) -> Decimal:
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


def get_transferable_amount_unified(session) -> Decimal:
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


def get_qty_step(session, symbol: str, category: str = "linear") -> Decimal:
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


def get_symbol_last_price(session, category: str, symbol: str) -> Decimal:
    r = require_ok(
        session.get_tickers(category=category, symbol=symbol),
        f"get_tickers {category} {symbol}"
    )

    items = r.get("result", {}).get("list", []) or []
    if not items:
        raise RuntimeError(f"No ticker found for {category} {symbol}")

    item = items[0]
    price = (
        item.get("lastPrice")
        or item.get("markPrice")
        or item.get("indexPrice")
    )

    price_d = D(price)
    if price_d <= 0:
        raise RuntimeError(f"Invalid ticker price for {symbol}: {item}")

    return price_d


def get_lot_size_filter(session, symbol: str, category: str = "linear"):
    try:
        r = require_ok(
            session.get_instruments_info(category=category, symbol=symbol),
            f"get_instruments_info {category} {symbol}"
        )

        items = r.get("result", {}).get("list", []) or []
        if not items:
            return {
                "qty_step": Decimal("0.001"),
                "min_order_qty": Decimal("0.001"),
                "max_mkt_order_qty": Decimal("999999999"),
            }

        lot = items[0].get("lotSizeFilter", {}) or {}

        return {
            "qty_step": D(lot.get("qtyStep", "0.001")),
            "min_order_qty": D(lot.get("minOrderQty", "0.001")),
            "max_mkt_order_qty": D(
                lot.get("maxMktOrderQty")
                or lot.get("maxOrderQty")
                or "999999999"
            ),
        }

    except Exception as e:
        log(f"get_lot_size_filter failed for {symbol}: {e}")
        return {
            "qty_step": Decimal("0.001"),
            "min_order_qty": Decimal("0.001"),
            "max_mkt_order_qty": Decimal("999999999"),
        }


def set_symbol_leverage(session, category: str, symbol: str, leverage: Decimal):
    lev = fmt_amount(leverage)

    try:
        resp = session.set_leverage(
            category=category,
            symbol=symbol,
            buyLeverage=lev,
            sellLeverage=lev,
        )

        if isinstance(resp, dict) and resp.get("retCode", 0) == 0:
            log(f"LEVERAGE SET -> {category} {symbol} {lev}x")
            return

        ret_msg = str((resp or {}).get("retMsg", "")).lower()

        if "not modified" in ret_msg or "same" in ret_msg:
            log(f"LEVERAGE ALREADY SET -> {category} {symbol} {lev}x")
            return

        require_ok(resp, f"set_leverage {category} {symbol}")

    except Exception as e:
        log(f"SET LEVERAGE WARNING -> {category} {symbol} {lev}x: {e}")


def copy_trade_signature(position) -> str:
    return (
        f"{position.get('category')}|"
        f"{position.get('symbol')}|"
        f"{position.get('side')}|"
        f"{position.get('position_idx')}"
    )


def read_text_file(path: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass

    return ""


def write_text_file(path: str, text: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        log(f"FAILED TO WRITE FILE {path}: {e}")


def remove_file_if_exists(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log(f"FAILED TO REMOVE FILE {path}: {e}")


def close_position_market(session, category: str, symbol: str, side: str, size: Decimal, position_idx=None):
    try:
        qty_step = get_qty_step(session, symbol, category)
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
            "qty": fmt_amount(qty),
            "reduceOnly": True,
            "closeOnTrigger": True,
        }

        if position_idx is not None:
            try:
                params["positionIdx"] = int(position_idx)
            except Exception:
                pass

        log(f"CLOSE PARAMS -> {params}")

        resp = require_ok(
            session.place_order(**params),
            f"place_order close {category} {symbol}"
        )

        log(f"CLOSE ORDER SENT -> {category} {symbol} {close_side} qty={qty}")
        log(f"RESP: {resp}")

    except Exception as e:
        log(f"FAILED TO CLOSE POSITION {symbol}: {e}")
        traceback.print_exc()


def get_open_positions(session):
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

            log(f"GET POSITIONS -> category={category} raw_count={len(plist)}")

            for p in plist:
                size = D(p.get("size"))

                log(
                    f"RAW POSITION -> category={category} "
                    f"symbol={p.get('symbol')} side={p.get('side')} "
                    f"size={p.get('size')} positionIdx={p.get('positionIdx')} "
                    f"unrealisedPnl={p.get('unrealisedPnl') or p.get('unrealizedPnl')}"
                )

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


def monitor_and_close_on_loss(session, open_positions):
    trigger_loss = -abs(LOSS_CLOSE_USDT)

    log(f"LOSS CLOSE TRIGGER = {trigger_loss} {COIN}")
    log(f"OPEN POSITIONS COUNT = {len(open_positions)}")

    for p in open_positions:
        category = p["category"]
        sym = p["symbol"]
        side = p["side"]
        size = p["size"]
        position_idx = p["position_idx"]
        unrealised_pnl = p["unrealised_pnl"]

        log(
            f"POSITION CHECK -> category={category} symbol={sym} side={side} "
            f"size={size} positionIdx={position_idx} "
            f"unrealisedPnl={unrealised_pnl} trigger={trigger_loss}"
        )

        if unrealised_pnl <= trigger_loss:
            log(
                f"LOSS LIMIT HIT -> closing now: {category} {sym} {side} "
                f"size={size} pnl={unrealised_pnl}"
            )
            close_position_market(session, category, sym, side, size, position_idx)
        else:
            log(f"NO CLOSE -> pnl {unrealised_pnl} is not <= trigger {trigger_loss}")


def transfer_excess_to_fund(session, open_positions):
    if open_positions:
        log("SKIP TRANSFER: naay open position")
        return

    wallet_usdt = get_unified_usdt_wallet(session)
    transferable = get_transferable_amount_unified(session)
    excess = wallet_usdt - RESERVE_USDT

    log(f"UNIFIED walletBalance = {wallet_usdt} {COIN}")
    log(f"Transferable amount   = {transferable} {COIN}")
    log(f"Reserve target        = {RESERVE_USDT} {COIN}")

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


def transfer_fund_to_unified_when_position_once(session, open_positions, lock_file):
    if not open_positions:
        if position_lock_exists(lock_file):
            clear_position_lock(lock_file)

        log("NO POSITION: skip FUND -> UNIFIED top-up")
        return

    for p in open_positions:
        log(
            f"OPEN POSITION FOUND -> {p['category']} "
            f"{p['symbol']} {p['side']} size={p['size']}"
        )

    if position_lock_exists(lock_file):
        log("TOP-UP ALREADY DONE FOR CURRENT POSITION CYCLE")
        return

    amount = q2(POSITION_TOPUP_USDT)
    fund_wallet = get_fund_usdt_wallet(session)

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

        create_position_lock(lock_file)
        log("POSITION LOCK CREATED")

    except Exception as e:
        log(f"FUND -> UNIFIED TRANSFER FAILED: {e}")
        traceback.print_exc()


def pick_leader_position(leader_positions):
    if not leader_positions:
        return None

    return leader_positions[0]


def follower_has_same_position(follower_positions, leader_position) -> bool:
    leader_symbol = leader_position["symbol"]
    leader_side = leader_position["side"]
    leader_category = leader_position["category"]

    for p in follower_positions:
        if (
            p["category"] == leader_category
            and p["symbol"] == leader_symbol
            and p["side"] == leader_side
            and p["size"] > 0
        ):
            return True

    return False


def open_copy_position_for_follower(follower_account, leader_position):
    session = follower_account["session"]
    account_name = follower_account["name"]

    category = leader_position["category"]
    symbol = leader_position["symbol"]
    side = leader_position["side"]
    position_idx = leader_position.get("position_idx")

    if category != "linear":
        log(
            f"{account_name} COPY SKIP: only linear USDT perps are supported. "
            f"Got category={category}"
        )
        return

    follower_positions = get_open_positions(session)

    if COPY_TRADE_REQUIRE_NO_FOLLOWER_POSITION and follower_positions:
        log(f"{account_name} COPY SKIP: follower already has open position")
        return

    if follower_has_same_position(follower_positions, leader_position):
        log(f"{account_name} COPY SKIP: same position already exists")
        return

    wallet_usdt = get_unified_usdt_wallet(session)
    margin_to_use = q2(wallet_usdt * COPY_TRADE_WALLET_PCT)
    notional_to_open = q2(margin_to_use * COPY_TRADE_LEVERAGE)

    if margin_to_use < COPY_TRADE_MIN_ORDER_USDT:
        log(
            f"{account_name} COPY SKIP: margin too small "
            f"({margin_to_use} < {COPY_TRADE_MIN_ORDER_USDT})"
        )
        return

    price = get_symbol_last_price(session, category, symbol)
    lot = get_lot_size_filter(session, symbol, category)

    qty_step = lot["qty_step"]
    min_order_qty = lot["min_order_qty"]
    max_mkt_order_qty = lot["max_mkt_order_qty"]

    raw_qty = notional_to_open / price
    qty = round_qty_by_step(raw_qty, qty_step)

    if max_mkt_order_qty > 0:
        qty = min(qty, max_mkt_order_qty)

    if qty <= 0 or qty < min_order_qty:
        log(
            f"{account_name} COPY SKIP: qty invalid "
            f"qty={qty}, min={min_order_qty}, raw={raw_qty}"
        )
        return

    set_symbol_leverage(session, category, symbol, COPY_TRADE_LEVERAGE)

    params = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": fmt_amount(qty),
        "reduceOnly": False,
        "orderLinkId": f"copy-{account_name}-{uuid.uuid4().hex[:16]}",
    }

    if position_idx is not None:
        try:
            params["positionIdx"] = int(position_idx)
        except Exception:
            pass

    log(f"{account_name} COPY PARAMS -> {params}")

    resp = require_ok(
        session.place_order(**params),
        f"{account_name} copy place_order {category} {symbol}"
    )

    log(
        f"{account_name} COPY ORDER SENT -> "
        f"{category} {symbol} {side} qty={qty} "
        f"wallet={wallet_usdt} margin={margin_to_use} "
        f"notional={notional_to_open} leverage={COPY_TRADE_LEVERAGE}x"
    )
    log(f"{account_name} COPY RESP: {resp}")


def run_copy_trade_cycle(accounts):
    if not COPY_TRADE_ENABLED:
        return

    leader_account = None

    for account in accounts:
        if account["index"] == COPY_TRADE_LEADER_ACCOUNT:
            leader_account = account
            break

    if leader_account is None:
        log(f"COPY TRADE ERROR: leader account #{COPY_TRADE_LEADER_ACCOUNT} not found")
        return

    try:
        leader_positions = get_open_positions(leader_account["session"])
    except Exception as e:
        log(f"COPY TRADE ERROR: cannot read leader positions: {e}")
        traceback.print_exc()
        return

    leader_position = pick_leader_position(leader_positions)

    if not leader_position:
        log("COPY TRADE: leader has no open position")

        for account in accounts:
            if COPY_TRADE_FOLLOWERS_START <= account["index"] <= COPY_TRADE_FOLLOWERS_END:
                remove_file_if_exists(account["copy_lock_file"])

        return

    signature = copy_trade_signature(leader_position)

    log(
        "COPY TRADE LEADER POSITION -> "
        f"{leader_position['category']} {leader_position['symbol']} "
        f"{leader_position['side']} size={leader_position['size']} "
        f"signature={signature}"
    )

    for account in accounts:
        idx = account["index"]

        if idx == COPY_TRADE_LEADER_ACCOUNT:
            continue

        if idx < COPY_TRADE_FOLLOWERS_START or idx > COPY_TRADE_FOLLOWERS_END:
            continue

        lock_file = account["copy_lock_file"]
        old_signature = read_text_file(lock_file)

        if old_signature == signature:
            log(f"{account['name']} COPY SKIP: already copied this leader position")
            continue

        try:
            open_copy_position_for_follower(account, leader_position)
            write_text_file(lock_file, signature)

        except Exception as e:
            log(f"{account['name']} COPY ERROR: {e}")
            traceback.print_exc()


def run_loss_close_priority(accounts):
    """
    Runs close-loss check before copy-trade and before reserve/top-up cycle.
    """
    log("===== PRIORITY LOSS CLOSE CHECK START =====")

    for account in accounts:
        session = account["session"]
        account_name = account["name"]

        try:
            open_positions = get_open_positions(session)
            log(f"{account_name} PRIORITY CLOSE CHECK: positions={len(open_positions)}")
            monitor_and_close_on_loss(session, open_positions)

        except Exception as e:
            log(f"{account_name} PRIORITY CLOSE ERROR: {e}")
            traceback.print_exc()

    log("===== PRIORITY LOSS CLOSE CHECK END =====")


def run_cycle(account):
    session = account["session"]
    lock_file = account["lock_file"]
    account_name = account["name"]

    log(f"===== START CYCLE: {account_name} =====")

    try:
        open_positions = get_open_positions(session)
    except Exception as e:
        log(f"{account_name} GET OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        monitor_and_close_on_loss(session, open_positions)
    except Exception as e:
        log(f"{account_name} MONITOR/CLOSE ERROR: {e}")
        traceback.print_exc()

    try:
        open_positions = get_open_positions(session)
    except Exception as e:
        log(f"{account_name} REFRESH OPEN POSITIONS ERROR: {e}")
        traceback.print_exc()
        open_positions = []

    try:
        transfer_excess_to_fund(session, open_positions)
    except Exception as e:
        log(f"{account_name} TRANSFER EXCESS ERROR: {e}")
        traceback.print_exc()

    try:
        transfer_fund_to_unified_when_position_once(session, open_positions, lock_file)
    except Exception as e:
        log(f"{account_name} POSITION TOP-UP ERROR: {e}")
        traceback.print_exc()

    log(f"===== END CYCLE: {account_name} =====")


def main():
    accounts = load_bybit_accounts()

    log("Bybit Reserve Bot started on Render worker")
    log(f"Mode: {MODE}")
    log(f"Testnet: {TESTNET}")
    log(f"Reserve USDT: {RESERVE_USDT}")
    log(f"Loss close USDT setting: {LOSS_CLOSE_USDT}")
    log(f"Effective loss trigger: {-abs(LOSS_CLOSE_USDT)}")
    log(f"Sleep seconds: {BOT_SLEEP_SEC}")
    log(f"Loaded accounts: {len(accounts)}")
    log(f"Loaded account indexes: {[a['index'] for a in accounts]}")
    log(f"Copy trade enabled: {COPY_TRADE_ENABLED}")
    log(f"Copy trade leader: account_{COPY_TRADE_LEADER_ACCOUNT}")
    log(f"Copy trade followers: {COPY_TRADE_FOLLOWERS_START} to {COPY_TRADE_FOLLOWERS_END}")
    log(f"Copy trade leverage: {COPY_TRADE_LEVERAGE}x")
    log(f"Copy trade wallet pct: {COPY_TRADE_WALLET_PCT}")

    while True:
        try:
            run_loss_close_priority(accounts)
        except Exception as e:
            log(f"PRIORITY CLOSE LOOP ERROR: {e}")
            traceback.print_exc()

        try:
            run_copy_trade_cycle(accounts)
        except Exception as e:
            log(f"COPY TRADE LOOP ERROR: {e}")
            traceback.print_exc()

        for account in accounts:
            try:
                run_cycle(account)
            except Exception as e:
                log(f"ACCOUNT LOOP ERROR {account['name']}: {e}")
                traceback.print_exc()

        time.sleep(BOT_SLEEP_SEC)


if __name__ == "__main__":
    main()
