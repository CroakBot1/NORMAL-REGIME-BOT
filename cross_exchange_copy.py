import json
import os
import time
import threading
import traceback
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import ccxt
except Exception:
    ccxt = None


PLACEHOLDER_PREFIXES = (
    "PASTE_",
    "YOUR_",
    "CHANGE_ME",
    "REPLACE_ME",
    "PUT_",
    "REAL_",
    "EXAMPLE_",
)


@dataclass
class CrossAccountConfig:
    exchange_name: str
    label: str
    api_key: str
    api_secret: str
    sandbox: bool = False
    default_type: str = "swap"
    position_mode: str = "auto"
    size_multiplier: Decimal = Decimal("1")
    set_leverage: bool = True
    close_extra_positions: bool = True
    reserve_enabled: bool = True
    loss_close_enabled: bool = True


@dataclass
class CrossCopyConfig:
    enabled: bool
    copy_trade_enabled: bool
    reserve_enabled: bool
    loss_close_enabled: bool
    sleep_sec: float
    dry_run: bool
    check_balance: bool
    default_type: str
    position_mode: str
    symbols: List[str]
    size_multiplier: Decimal
    set_leverage: bool
    close_extra_positions: bool
    reserve_usdt: Decimal
    min_free_usdt_after_reserve: Decimal
    loss_close_usdt: Decimal
    max_exchanges: int
    max_accounts_per_exchange: int
    accounts: List[CrossAccountConfig]


def D(value: Any) -> Decimal:
    try:
        if value is None or value == "":
            return Decimal("0")
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def clean_value(value: Any) -> str:
    if value is None:
        return ""

    value = str(value).strip()

    if not value:
        return ""

    upper_value = value.upper()

    for prefix in PLACEHOLDER_PREFIXES:
        if upper_value.startswith(prefix):
            return ""

    return value


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def int_value(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def normalize_exchange_name(name: str) -> str:
    name = str(name or "").strip().lower()

    aliases = {
        "huobi": "htx",
        "crypto.com": "cryptocom",
        "crypto_com": "cryptocom",
        "gate": "gateio",
        "binanceusdm": "binance",
        "binancecoinm": "binance",
    }

    return aliases.get(name, name)


def normalize_position_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()

    if mode in ("hedge", "hedged", "dual", "both"):
        return "hedge"

    if mode in ("oneway", "one-way", "one_way", "single"):
        return "oneway"

    return "auto"


def exchange_is_supported(exchange_name: str) -> bool:
    if ccxt is None:
        return False

    try:
        return exchange_name in getattr(ccxt, "exchanges", [])
    except Exception:
        return hasattr(ccxt, exchange_name)


def normalize_bybit_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace("/", "").replace(":USDT", "")


def split_usdt_symbol(symbol: str) -> Tuple[str, str]:
    normalized = normalize_bybit_symbol(symbol)

    if normalized.endswith("USDT"):
        return normalized[:-4], "USDT"

    if normalized.endswith("USDC"):
        return normalized[:-4], "USDC"

    return normalized, "USDT"


def read_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config_from_json_and_app(
    app_module: Any,
    path: str = "live_exchanges.json",
) -> CrossCopyConfig:
    data = read_json_file(path)

    enabled = bool_value(data.get("enabled"), True)
    copy_trade_enabled = bool_value(data.get("copy_trade_enabled"), True)
    reserve_enabled = bool_value(data.get("reserve_enabled"), True)
    loss_close_enabled = bool_value(data.get("loss_close_enabled"), True)
    sleep_sec = float(data.get("sleep_sec", 15))
    dry_run = bool_value(data.get("dry_run"), False)
    check_balance = bool_value(data.get("check_balance"), False)
    default_type = clean_value(data.get("default_type")) or "swap"
    position_mode = normalize_position_mode(data.get("position_mode", "auto"))

    max_exchanges = int_value(data.get("max_exchanges"), 50)
    max_accounts_per_exchange = int_value(data.get("max_accounts_per_exchange"), 50)

    if max_exchanges <= 0:
        max_exchanges = 50

    if max_accounts_per_exchange <= 0:
        max_accounts_per_exchange = 50

    max_exchanges = min(max_exchanges, 50)
    max_accounts_per_exchange = min(max_accounts_per_exchange, 50)

    app_reserve = getattr(app_module, "RESERVE_USDT", Decimal("501"))
    app_min_transfer = getattr(app_module, "MIN_TRANSFER_USDT", Decimal("1"))
    app_loss_close = getattr(app_module, "LOSS_CLOSE_USDT", Decimal("70"))

    reserve_usdt = D(data.get("reserve_usdt", app_reserve))
    min_free_usdt_after_reserve = D(data.get("min_free_usdt_after_reserve", app_min_transfer))
    loss_close_usdt = D(data.get("loss_close_usdt", app_loss_close))

    if reserve_usdt < 0:
        reserve_usdt = Decimal("0")

    if min_free_usdt_after_reserve < 0:
        min_free_usdt_after_reserve = Decimal("0")

    if loss_close_usdt <= 0:
        loss_close_usdt = D(app_loss_close)

    raw_symbols = data.get("symbols", ["BTCUSDT"])

    if not isinstance(raw_symbols, list):
        raw_symbols = ["BTCUSDT"]

    symbols = [
        normalize_bybit_symbol(s)
        for s in raw_symbols
        if normalize_bybit_symbol(s)
    ]

    if not symbols:
        symbols = ["BTCUSDT"]

    size_multiplier = D(data.get("size_multiplier", "1"))
    if size_multiplier <= 0:
        size_multiplier = Decimal("1")

    set_leverage = bool_value(data.get("set_leverage"), True)
    close_extra_positions = bool_value(data.get("close_extra_positions"), True)

    accounts: List[CrossAccountConfig] = []
    raw_followers = data.get("followers", [])

    if not isinstance(raw_followers, list):
        raw_followers = []

    exchange_count = 0

    for follower in raw_followers:
        if exchange_count >= max_exchanges:
            break

        if not isinstance(follower, dict):
            continue

        exchange_name = normalize_exchange_name(follower.get("name"))

        if not exchange_name:
            continue

        if not exchange_is_supported(exchange_name):
            continue

        exchange_count += 1

        follower_default_type = clean_value(follower.get("default_type")) or default_type
        follower_position_mode = normalize_position_mode(
            follower.get("position_mode", position_mode)
        )
        follower_sandbox = bool_value(follower.get("sandbox"), False)

        follower_multiplier = D(follower.get("size_multiplier", size_multiplier))
        if follower_multiplier <= 0:
            follower_multiplier = size_multiplier

        follower_set_leverage = bool_value(follower.get("set_leverage"), set_leverage)

        follower_close_extra = bool_value(
            follower.get("close_extra_positions"),
            close_extra_positions,
        )

        follower_reserve_enabled = bool_value(
            follower.get("reserve_enabled"),
            reserve_enabled,
        )

        follower_loss_close_enabled = bool_value(
            follower.get("loss_close_enabled"),
            loss_close_enabled,
        )

        raw_accounts = follower.get("accounts")

        if isinstance(raw_accounts, list):
            account_items = raw_accounts[:max_accounts_per_exchange]
        else:
            account_items = [follower]

        account_number = 0

        for account in account_items:
            if not isinstance(account, dict):
                continue

            api_key = clean_value(account.get("api_key"))
            api_secret = clean_value(account.get("api_secret"))

            if not api_key or not api_secret:
                continue

            account_number += 1

            account_label = clean_value(account.get("label"))
            if not account_label:
                account_label = f"{exchange_name.upper()}#{account_number:02d}"

            account_multiplier = D(account.get("size_multiplier", follower_multiplier))
            if account_multiplier <= 0:
                account_multiplier = follower_multiplier

            accounts.append(
                CrossAccountConfig(
                    exchange_name=exchange_name,
                    label=account_label,
                    api_key=api_key,
                    api_secret=api_secret,
                    sandbox=bool_value(account.get("sandbox"), follower_sandbox),
                    default_type=clean_value(account.get("default_type")) or follower_default_type,
                    position_mode=normalize_position_mode(
                        account.get("position_mode", follower_position_mode)
                    ),
                    size_multiplier=account_multiplier,
                    set_leverage=bool_value(account.get("set_leverage"), follower_set_leverage),
                    close_extra_positions=bool_value(
                        account.get("close_extra_positions"),
                        follower_close_extra,
                    ),
                    reserve_enabled=bool_value(
                        account.get("reserve_enabled"),
                        follower_reserve_enabled,
                    ),
                    loss_close_enabled=bool_value(
                        account.get("loss_close_enabled"),
                        follower_loss_close_enabled,
                    ),
                )
            )

    return CrossCopyConfig(
        enabled=enabled,
        copy_trade_enabled=copy_trade_enabled,
        reserve_enabled=reserve_enabled,
        loss_close_enabled=loss_close_enabled,
        sleep_sec=max(sleep_sec, 5.0),
        dry_run=dry_run,
        check_balance=check_balance,
        default_type=default_type,
        position_mode=position_mode,
        symbols=symbols,
        size_multiplier=size_multiplier,
        set_leverage=set_leverage,
        close_extra_positions=close_extra_positions,
        reserve_usdt=reserve_usdt,
        min_free_usdt_after_reserve=min_free_usdt_after_reserve,
        loss_close_usdt=loss_close_usdt,
        max_exchanges=max_exchanges,
        max_accounts_per_exchange=max_accounts_per_exchange,
        accounts=accounts,
    )


def create_client(account: CrossAccountConfig):
    if ccxt is None:
        raise RuntimeError("ccxt is not installed")

    if not hasattr(ccxt, account.exchange_name):
        raise RuntimeError(f"ccxt exchange not found: {account.exchange_name}")

    exchange_class = getattr(ccxt, account.exchange_name)

    options = {
        "defaultType": account.default_type,
    }

    if account.exchange_name == "binance":
        options["adjustForTimeDifference"] = True

    client = exchange_class(
        {
            "apiKey": account.api_key,
            "secret": account.api_secret,
            "enableRateLimit": True,
            "options": options,
        }
    )

    try:
        client.set_sandbox_mode(account.sandbox)
    except Exception:
        pass

    return client


class CrossExchangeCopier:
    def __init__(
        self,
        app_module: Any,
        config_path: str = "live_exchanges.json",
        log_func: Optional[Callable[[str], None]] = None,
    ):
        self.app = app_module
        self.config_path = config_path
        self.log_func = log_func or print
        self.clients: Dict[str, Any] = {}
        self.fingerprints: Dict[str, str] = {}
        self.markets_loaded: Dict[str, bool] = {}

    def log(self, message: str):
        try:
            self.log_func(message)
        except Exception:
            print(message, flush=True)

    def client_key(self, account: CrossAccountConfig) -> str:
        return f"{account.exchange_name}:{account.label}"

    def fingerprint(self, account: CrossAccountConfig) -> str:
        return "|".join(
            [
                account.exchange_name,
                account.label,
                account.api_key[-6:],
                account.api_secret[-6:],
                str(account.sandbox),
                account.default_type,
                account.position_mode,
                str(account.size_multiplier),
                str(account.reserve_enabled),
                str(account.loss_close_enabled),
            ]
        )

    def sync_clients(self, config: CrossCopyConfig):
        active = set()

        for account in config.accounts:
            key = self.client_key(account)
            active.add(key)

            fp = self.fingerprint(account)

            if self.fingerprints.get(key) == fp and key in self.clients:
                continue

            try:
                client = create_client(account)
                self.clients[key] = client
                self.fingerprints[key] = fp
                self.markets_loaded.pop(key, None)

                self.log(
                    f"CROSS-COPY {account.label}: client loaded "
                    f"exchange={account.exchange_name} "
                    f"defaultType={account.default_type} "
                    f"positionMode={account.position_mode} "
                    f"sandbox={account.sandbox}"
                )
            except Exception as e:
                self.log(f"CROSS-COPY {account.label}: client load failed: {e}")
                traceback.print_exc()

        for key in list(self.clients.keys()):
            if key not in active:
                self.clients.pop(key, None)
                self.fingerprints.pop(key, None)
                self.markets_loaded.pop(key, None)
                self.log(f"CROSS-COPY {key}: removed from clients")

    def ensure_markets_loaded(self, account: CrossAccountConfig, client: Any):
        key = self.client_key(account)

        if self.markets_loaded.get(key):
            return

        try:
            client.load_markets()
            self.markets_loaded[key] = True
        except Exception as e:
            self.log(f"CROSS-COPY {account.label}: load_markets failed: {e}")

    def map_symbol(self, client: Any, bybit_symbol: str) -> Optional[str]:
        base, quote = split_usdt_symbol(bybit_symbol)

        candidates = [
            f"{base}/{quote}:{quote}",
            f"{base}/{quote}",
            f"{base}{quote}",
        ]

        markets = getattr(client, "markets", None) or {}

        for candidate in candidates:
            if candidate in markets:
                return candidate

        for market_symbol, market in markets.items():
            market_id = str(market.get("id", "")).upper()
            normalized_market_id = market_id.replace("_", "").replace("-", "")
            normalized_symbol = str(market_symbol).upper().replace("/", "").replace(":USDT", "")

            if normalized_market_id == bybit_symbol or normalized_symbol == bybit_symbol:
                return market_symbol

        return candidates[0]

    def get_master_positions(self, config: CrossCopyConfig) -> List[Dict[str, Any]]:
        try:
            positions = self.app.copy_get_open_positions_for_session(
                self.app.session,
                "CROSS-MASTER/API#01",
            )
        except Exception as e:
            self.log(f"CROSS-COPY MASTER: failed to read Bybit master positions: {e}")
            traceback.print_exc()
            return []

        allowed = set(config.symbols)
        filtered = []

        for pos in positions:
            symbol = normalize_bybit_symbol(pos.get("symbol"))
            if symbol in allowed:
                filtered.append(pos)

        return filtered

    def fetch_balance_usdt(self, account: CrossAccountConfig, client: Any) -> Tuple[Decimal, Decimal]:
        try:
            balance = client.fetch_balance()
        except Exception as e:
            self.log(f"CROSS-COPY {account.label}: fetch_balance failed: {e}")
            return Decimal("0"), Decimal("0")

        free = Decimal("0")
        total = Decimal("0")

        if isinstance(balance, dict):
            free_map = balance.get("free", {}) or {}
            total_map = balance.get("total", {}) or {}

            free = D(free_map.get("USDT"))
            total = D(total_map.get("USDT"))

            if free <= 0:
                usdt_row = balance.get("USDT", {}) or {}
                free = D(usdt_row.get("free"))
                total = D(usdt_row.get("total"))

        return free, total

    def reserve_allows_new_order(
        self,
        account: CrossAccountConfig,
        client: Any,
        config: CrossCopyConfig,
    ) -> bool:
        if not account.reserve_enabled:
            return True

        free_usdt, total_usdt = self.fetch_balance_usdt(account, client)
        required_free = config.reserve_usdt + config.min_free_usdt_after_reserve

        self.log(
            f"CROSS-COPY {account.label}: reserve check "
            f"freeUSDT={free_usdt} totalUSDT={total_usdt} "
            f"reserve={config.reserve_usdt} requiredFree={required_free}"
        )

        if free_usdt <= required_free:
            self.log(
                f"CROSS-COPY {account.label}: SKIP OPEN/ADD, reserve protected "
                f"({free_usdt} <= {required_free})"
            )
            return False

        return True

    def fetch_follower_positions(
        self,
        account: CrossAccountConfig,
        client: Any,
        exchange_symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            if exchange_symbol:
                try:
                    return client.fetch_positions([exchange_symbol])
                except Exception:
                    pass

            return client.fetch_positions()
        except Exception as e:
            self.log(f"CROSS-COPY {account.label}: fetch_positions failed: {e}")
            return []

    def position_matches_symbol(
        self,
        position: Dict[str, Any],
        exchange_symbol: str,
        bybit_symbol: str,
    ) -> bool:
        pos_symbol = str(position.get("symbol") or "")
        if pos_symbol == exchange_symbol:
            return True

        info = position.get("info", {}) or {}
        info_symbol = str(
            info.get("symbol")
            or info.get("contract")
            or info.get("instId")
            or info.get("instrument_id")
            or ""
        ).upper()

        normalized_exchange_symbol = normalize_bybit_symbol(exchange_symbol)
        normalized_position_symbol = normalize_bybit_symbol(pos_symbol)

        return (
            normalized_position_symbol == bybit_symbol
            or normalized_exchange_symbol == bybit_symbol
            or info_symbol.replace("-", "").replace("_", "") == bybit_symbol
        )

    def extract_position_qty_side(self, position: Dict[str, Any]) -> Tuple[Decimal, str]:
        side_raw = str(position.get("side") or "").lower()
        info = position.get("info", {}) or {}

        raw_contracts = (
            position.get("contracts")
            or position.get("amount")
            or info.get("positionAmt")
            or info.get("size")
            or info.get("contracts")
            or info.get("positionSize")
            or "0"
        )

        qty = D(raw_contracts)

        if qty < 0:
            side = "Sell"
            qty = abs(qty)
        elif side_raw in ("short", "sell"):
            side = "Sell"
            qty = abs(qty)
        elif side_raw in ("long", "buy"):
            side = "Buy"
            qty = abs(qty)
        else:
            side = "Buy" if qty >= 0 else "Sell"
            qty = abs(qty)

        return qty, side

    def extract_unrealized_pnl(self, position: Dict[str, Any]) -> Decimal:
        info = position.get("info", {}) or {}

        return D(
            position.get("unrealizedPnl")
            or position.get("unrealisedPnl")
            or position.get("unrealizedProfit")
            or info.get("unRealizedProfit")
            or info.get("unrealisedPnl")
            or info.get("unrealizedPnl")
            or info.get("upl")
            or info.get("pnl")
            or "0"
        )

    def find_follower_position(
        self,
        account: CrossAccountConfig,
        client: Any,
        exchange_symbol: str,
        bybit_symbol: str,
        side: str,
    ) -> Optional[Dict[str, Any]]:
        positions = self.fetch_follower_positions(account, client, exchange_symbol)

        for position in positions:
            if not self.position_matches_symbol(position, exchange_symbol, bybit_symbol):
                continue

            qty, pos_side = self.extract_position_qty_side(position)

            if qty <= 0:
                continue

            if pos_side == side:
                return position

        return None

    def get_extra_positions(
        self,
        account: CrossAccountConfig,
        client: Any,
        exchange_symbols: Dict[str, str],
        master_keys: set,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        extra = []

        positions = self.fetch_follower_positions(account, client)

        for position in positions:
            qty, pos_side = self.extract_position_qty_side(position)
            if qty <= 0:
                continue

            matched_bybit_symbol = None

            for bybit_symbol, exchange_symbol in exchange_symbols.items():
                if self.position_matches_symbol(position, exchange_symbol, bybit_symbol):
                    matched_bybit_symbol = bybit_symbol
                    break

            if not matched_bybit_symbol:
                continue

            key = (matched_bybit_symbol, pos_side)
            if key not in master_keys:
                extra.append((matched_bybit_symbol, pos_side, position))

        return extra

    def monitor_and_close_on_loss(
        self,
        account: CrossAccountConfig,
        client: Any,
        config: CrossCopyConfig,
        exchange_symbols: Dict[str, str],
    ):
        if not account.loss_close_enabled:
            return

        trigger_loss = -abs(config.loss_close_usdt)
        positions = self.fetch_follower_positions(account, client)

        for position in positions:
            qty, side = self.extract_position_qty_side(position)

            if qty <= 0:
                continue

            matched_exchange_symbol = None

            for bybit_symbol, exchange_symbol in exchange_symbols.items():
                if self.position_matches_symbol(position, exchange_symbol, bybit_symbol):
                    matched_exchange_symbol = exchange_symbol
                    break

            if not matched_exchange_symbol:
                continue

            pnl = self.extract_unrealized_pnl(position)

            self.log(
                f"CROSS-COPY {account.label}: POSITION CHECK "
                f"{matched_exchange_symbol} {side} qty={qty} unrealizedPnl={pnl}"
            )

            if pnl <= trigger_loss:
                self.log(
                    f"CROSS-COPY {account.label}: LOSS LIMIT HIT "
                    f"{matched_exchange_symbol} {side} qty={qty} pnl={pnl}"
                )

                self.close_position(
                    account=account,
                    client=client,
                    symbol=matched_exchange_symbol,
                    side=side,
                    qty=qty,
                    dry_run=config.dry_run,
                )

    def safe_amount_to_precision(self, client: Any, symbol: str, qty: Decimal) -> str:
        try:
            return client.amount_to_precision(symbol, float(qty))
        except Exception:
            return str(qty.normalize())

    def set_leverage_if_needed(
        self,
        account: CrossAccountConfig,
        client: Any,
        symbol: str,
        leverage: Decimal,
        dry_run: bool,
    ):
        if leverage <= 0:
            return

        try:
            lev = int(leverage)
        except Exception:
            return

        if lev <= 0:
            return

        if dry_run:
            self.log(f"CROSS-COPY {account.label}: DRY RUN set leverage {symbol} {lev}x")
            return

        try:
            client.set_leverage(lev, symbol)
            self.log(f"CROSS-COPY {account.label}: leverage synced {symbol} {lev}x")
        except Exception as e:
            self.log(f"CROSS-COPY {account.label}: set leverage skipped/failed {symbol}: {e}")

    def binance_position_side(
        self,
        order_side: str,
        reduce_only: bool,
    ) -> str:
        if reduce_only:
            return "LONG" if order_side == "Sell" else "SHORT"

        return "LONG" if order_side == "Buy" else "SHORT"

    def build_order_params(
        self,
        account: CrossAccountConfig,
        side: str,
        reduce_only: bool,
        include_position_side: bool,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        if reduce_only:
            params["reduceOnly"] = True

        if account.exchange_name == "binance" and include_position_side:
            params["positionSide"] = self.binance_position_side(
                order_side=side,
                reduce_only=reduce_only,
            )

        return params

    def should_retry_with_binance_position_side(self, error: Exception) -> bool:
        msg = str(error).lower()

        return (
            "positionside" in msg
            or "position side" in msg
            or "hedge mode" in msg
            or "dual side" in msg
        )

    def place_market_order(
        self,
        account: CrossAccountConfig,
        client: Any,
        symbol: str,
        side: str,
        qty: Decimal,
        reduce_only: bool,
        dry_run: bool,
    ):
        if qty <= 0:
            self.log(f"CROSS-COPY {account.label}: skip order invalid qty {symbol}")
            return

        ccxt_side = "buy" if side == "Buy" else "sell"
        amount = self.safe_amount_to_precision(client, symbol, qty)

        include_position_side = (
            account.exchange_name == "binance"
            and account.position_mode == "hedge"
        )

        params = self.build_order_params(
            account=account,
            side=side,
            reduce_only=reduce_only,
            include_position_side=include_position_side,
        )

        if dry_run:
            self.log(
                f"CROSS-COPY {account.label}: DRY RUN order "
                f"{symbol} {ccxt_side} qty={amount} reduceOnly={reduce_only} params={params}"
            )
            return

        try:
            client.create_order(
                symbol=symbol,
                type="market",
                side=ccxt_side,
                amount=float(amount),
                price=None,
                params=params,
            )
            action = "CLOSE/REDUCE" if reduce_only else "OPEN/ADD"
            self.log(
                f"CROSS-COPY {account.label}: {action} order sent "
                f"{symbol} {ccxt_side} qty={amount} params={params}"
            )
            return

        except Exception as first_error:
            if (
                account.exchange_name == "binance"
                and account.position_mode == "auto"
                and self.should_retry_with_binance_position_side(first_error)
            ):
                retry_params = self.build_order_params(
                    account=account,
                    side=side,
                    reduce_only=reduce_only,
                    include_position_side=True,
                )

                try:
                    self.log(
                        f"CROSS-COPY {account.label}: retry Binance order with "
                        f"positionSide params={retry_params}"
                    )
                    client.create_order(
                        symbol=symbol,
                        type="market",
                        side=ccxt_side,
                        amount=float(amount),
                        price=None,
                        params=retry_params,
                    )
                    action = "CLOSE/REDUCE" if reduce_only else "OPEN/ADD"
                    self.log(
                        f"CROSS-COPY {account.label}: {action} retry order sent "
                        f"{symbol} {ccxt_side} qty={amount} params={retry_params}"
                    )
                    return
                except Exception as retry_error:
                    self.log(
                        f"CROSS-COPY {account.label}: Binance retry with positionSide failed "
                        f"{symbol} {ccxt_side} qty={amount}: {retry_error}"
                    )
                    traceback.print_exc()
                    return

            if reduce_only:
                try:
                    retry_params = dict(params)
                    retry_params.pop("reduceOnly", None)

                    client.create_order(
                        symbol=symbol,
                        type="market",
                        side=ccxt_side,
                        amount=float(amount),
                        price=None,
                        params=retry_params,
                    )
                    self.log(
                        f"CROSS-COPY {account.label}: CLOSE retry without reduceOnly sent "
                        f"{symbol} {ccxt_side} qty={amount} params={retry_params}"
                    )
                    return
                except Exception:
                    pass

            self.log(
                f"CROSS-COPY {account.label}: order failed "
                f"{symbol} {ccxt_side} qty={amount} params={params}: {first_error}"
            )
            traceback.print_exc()

    def close_position(
        self,
        account: CrossAccountConfig,
        client: Any,
        symbol: str,
        side: str,
        qty: Decimal,
        dry_run: bool,
    ):
        close_side = "Sell" if side == "Buy" else "Buy"

        self.place_market_order(
            account=account,
            client=client,
            symbol=symbol,
            side=close_side,
            qty=qty,
            reduce_only=True,
            dry_run=dry_run,
        )

    def sync_one_account(
        self,
        account: CrossAccountConfig,
        config: CrossCopyConfig,
        master_positions: List[Dict[str, Any]],
    ):
        key = self.client_key(account)
        client = self.clients.get(key)

        if client is None:
            return

        self.ensure_markets_loaded(account, client)

        exchange_symbols = {}

        for symbol in config.symbols:
            mapped = self.map_symbol(client, symbol)
            if mapped:
                exchange_symbols[symbol] = mapped

        self.monitor_and_close_on_loss(
            account=account,
            client=client,
            config=config,
            exchange_symbols=exchange_symbols,
        )

        master_by_key = {}

        for master in master_positions:
            bybit_symbol = normalize_bybit_symbol(master.get("symbol"))
            side = str(master.get("side") or "").capitalize()

            if bybit_symbol not in exchange_symbols:
                continue

            if side not in ("Buy", "Sell"):
                continue

            master_by_key[(bybit_symbol, side)] = master

        if account.close_extra_positions:
            extras = self.get_extra_positions(
                account=account,
                client=client,
                exchange_symbols=exchange_symbols,
                master_keys=set(master_by_key.keys()),
            )

            for bybit_symbol, pos_side, position in extras:
                exchange_symbol = exchange_symbols.get(bybit_symbol)
                qty, side = self.extract_position_qty_side(position)

                if not exchange_symbol or qty <= 0:
                    continue

                self.log(
                    f"CROSS-COPY {account.label}: extra position -> close "
                    f"{exchange_symbol} {side} qty={qty}"
                )

                self.close_position(
                    account=account,
                    client=client,
                    symbol=exchange_symbol,
                    side=side,
                    qty=qty,
                    dry_run=config.dry_run,
                )

        for master_key, master in master_by_key.items():
            bybit_symbol, master_side = master_key
            exchange_symbol = exchange_symbols[bybit_symbol]

            desired_qty = D(master.get("size")) * account.size_multiplier
            leverage = D(master.get("leverage"))

            if desired_qty <= 0:
                continue

            if account.set_leverage:
                self.set_leverage_if_needed(
                    account=account,
                    client=client,
                    symbol=exchange_symbol,
                    leverage=leverage,
                    dry_run=config.dry_run,
                )

            actual_position = self.find_follower_position(
                account=account,
                client=client,
                exchange_symbol=exchange_symbol,
                bybit_symbol=bybit_symbol,
                side=master_side,
            )

            if actual_position is None:
                if not self.reserve_allows_new_order(account, client, config):
                    continue

                self.log(
                    f"CROSS-COPY {account.label}: missing position -> open "
                    f"{exchange_symbol} {master_side} qty={desired_qty}"
                )

                self.place_market_order(
                    account=account,
                    client=client,
                    symbol=exchange_symbol,
                    side=master_side,
                    qty=desired_qty,
                    reduce_only=False,
                    dry_run=config.dry_run,
                )
                continue

            actual_qty, actual_side = self.extract_position_qty_side(actual_position)
            diff = desired_qty - actual_qty

            if abs(diff) <= Decimal("0.00000001"):
                self.log(
                    f"CROSS-COPY {account.label}: already matched "
                    f"{exchange_symbol} {master_side} qty={actual_qty}"
                )
                continue

            if diff > 0:
                if not self.reserve_allows_new_order(account, client, config):
                    continue

                self.log(
                    f"CROSS-COPY {account.label}: add qty "
                    f"{exchange_symbol} {master_side} add={diff}"
                )

                self.place_market_order(
                    account=account,
                    client=client,
                    symbol=exchange_symbol,
                    side=master_side,
                    qty=diff,
                    reduce_only=False,
                    dry_run=config.dry_run,
                )
            else:
                reduce_qty = abs(diff)

                self.log(
                    f"CROSS-COPY {account.label}: reduce qty "
                    f"{exchange_symbol} {master_side} reduce={reduce_qty}"
                )

                self.close_position(
                    account=account,
                    client=client,
                    symbol=exchange_symbol,
                    side=master_side,
                    qty=reduce_qty,
                    dry_run=config.dry_run,
                )

    def health_check(self, config: CrossCopyConfig):
        if not config.check_balance:
            return

        for account in config.accounts:
            key = self.client_key(account)
            client = self.clients.get(key)

            if client is None:
                continue

            free_usdt, total_usdt = self.fetch_balance_usdt(account, client)
            self.log(
                f"CROSS-COPY {account.label}: health OK "
                f"freeUSDT={free_usdt} totalUSDT={total_usdt}"
            )

    def run_once(self):
        config = load_config_from_json_and_app(self.app, self.config_path)

        if not config.enabled:
            self.log("CROSS-COPY: disabled in live_exchanges.json")
            return config

        self.sync_clients(config)

        if not config.accounts:
            self.log("CROSS-COPY: no cross-exchange accounts loaded")
            return config

        if not config.copy_trade_enabled:
            self.log("CROSS-COPY: copy_trade_enabled=false")
            self.health_check(config)
            return config

        master_positions = self.get_master_positions(config)

        if master_positions:
            for pos in master_positions:
                self.log(
                    f"CROSS-COPY MASTER: {pos.get('symbol')} {pos.get('side')} "
                    f"qty={pos.get('size')} leverage={pos.get('leverage_raw') or pos.get('leverage')}"
                )
        else:
            self.log("CROSS-COPY MASTER: no monitored open positions")

        for account in config.accounts:
            try:
                self.sync_one_account(
                    account=account,
                    config=config,
                    master_positions=master_positions,
                )
            except Exception as e:
                self.log(f"CROSS-COPY {account.label}: sync error: {e}")
                traceback.print_exc()

        self.health_check(config)

        return config


_THREAD: Optional[threading.Thread] = None
_COPIER: Optional[CrossExchangeCopier] = None


def start_cross_exchange_copy_worker(
    app_module: Any,
    log_func: Optional[Callable[[str], None]] = None,
    config_path: str = "live_exchanges.json",
):
    global _THREAD
    global _COPIER

    if ccxt is None:
        if log_func:
            log_func("CROSS-COPY: ccxt not installed")
        return None

    initial_config = load_config_from_json_and_app(app_module, config_path)

    if not initial_config.enabled:
        if log_func:
            log_func("CROSS-COPY: disabled in live_exchanges.json")
        return None

    if _COPIER is None:
        _COPIER = CrossExchangeCopier(
            app_module=app_module,
            config_path=config_path,
            log_func=log_func,
        )

    if _THREAD is not None and _THREAD.is_alive():
        return _COPIER

    def worker():
        sleep_sec = initial_config.sleep_sec

        while True:
            try:
                latest_config = _COPIER.run_once()
                sleep_sec = latest_config.sleep_sec
                time.sleep(sleep_sec)
            except Exception as e:
                _COPIER.log(f"CROSS-COPY: worker error: {e}")
                traceback.print_exc()
                time.sleep(sleep_sec)

    _THREAD = threading.Thread(
        target=worker,
        name="cross-exchange-copy-reserve",
        daemon=True,
    )
    _THREAD.start()

    _COPIER.log(f"CROSS-COPY: worker started using {config_path}")

    return _COPIER
