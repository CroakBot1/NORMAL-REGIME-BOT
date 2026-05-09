import json
import os
import time
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

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
)


SUPPORTED_EXCHANGES = {
    "bybit",
    "binance",
    "gateio",
    "mexc",
    "bitmart",
    "bingx",
    "phemex",
    "kraken",
    "coinbase",
    "bitstamp",
    "htx",
    "bitfinex",
    "cryptocom",
    "deribit",
    "woo",
    "ascendex",
    "bitmex",
    "coinex",
    "lbank",
    "poloniex",
}


@dataclass
class ExchangeConfig:
    name: str
    api_key: str
    api_secret: str
    sandbox: bool = False
    default_type: str = "swap"


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


def normalize_exchange_name(name: str) -> str:
    name = str(name or "").strip().lower()

    aliases = {
        "huobi": "htx",
        "crypto.com": "cryptocom",
        "crypto_com": "cryptocom",
        "binanceusdm": "binance",
        "binancecoinm": "binance",
    }

    return aliases.get(name, name)


def read_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_exchange_configs(path: str = "live_exchanges.json") -> Dict[str, Any]:
    data = read_json_file(path)

    enabled = bool(data.get("enabled", True))
    sleep_sec = float(data.get("sleep_sec", 60))
    check_balance = bool(data.get("check_balance", True))
    default_type = clean_value(data.get("default_type")) or "swap"

    configs: List[ExchangeConfig] = []

    raw_exchanges = data.get("exchanges", [])

    if not isinstance(raw_exchanges, list):
        raw_exchanges = []

    for item in raw_exchanges[:20]:
        if not isinstance(item, dict):
            continue

        name = normalize_exchange_name(item.get("name"))
        api_key = clean_value(item.get("api_key"))
        api_secret = clean_value(item.get("api_secret"))
        sandbox = bool(item.get("sandbox", False))
        item_default_type = clean_value(item.get("default_type")) or default_type

        if not name:
            continue

        if name not in SUPPORTED_EXCHANGES:
            continue

        if not api_key or not api_secret:
            continue

        configs.append(
            ExchangeConfig(
                name=name,
                api_key=api_key,
                api_secret=api_secret,
                sandbox=sandbox,
                default_type=item_default_type,
            )
        )

    return {
        "enabled": enabled,
        "sleep_sec": sleep_sec,
        "check_balance": check_balance,
        "configs": configs,
    }


def create_exchange_client(config: ExchangeConfig):
    if ccxt is None:
        raise RuntimeError("ccxt is not installed")

    if not hasattr(ccxt, config.name):
        raise RuntimeError(f"ccxt exchange not found: {config.name}")

    exchange_class = getattr(ccxt, config.name)

    client = exchange_class(
        {
            "apiKey": config.api_key,
            "secret": config.api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": config.default_type,
            },
        }
    )

    try:
        client.set_sandbox_mode(config.sandbox)
    except Exception:
        pass

    return client


class MultiExchangeRegistry:
    def __init__(
        self,
        config_path: str = "live_exchanges.json",
        log_func: Optional[Callable[[str], None]] = None,
    ):
        self.config_path = config_path
        self.log_func = log_func or print
        self.clients: Dict[str, Any] = {}
        self.fingerprints: Dict[str, str] = {}

    def log(self, message: str):
        try:
            self.log_func(message)
        except Exception:
            print(message, flush=True)

    def fingerprint(self, config: ExchangeConfig) -> str:
        return "|".join(
            [
                config.name,
                config.api_key[-6:],
                config.api_secret[-6:],
                str(config.sandbox),
                config.default_type,
            ]
        )

    def sync_from_file(self):
        loaded = load_exchange_configs(self.config_path)
        configs: List[ExchangeConfig] = loaded["configs"]

        active_keys = set()

        for config in configs:
            key = config.name
            active_keys.add(key)

            fp = self.fingerprint(config)

            if self.fingerprints.get(key) == fp and key in self.clients:
                continue

            try:
                client = create_exchange_client(config)

                self.clients[key] = client
                self.fingerprints[key] = fp

                self.log(
                    f"MULTI-EXCHANGE {config.name}: connected "
                    f"defaultType={config.default_type} sandbox={config.sandbox}"
                )

            except Exception as e:
                self.log(f"MULTI-EXCHANGE {config.name}: connect failed: {e}")
                traceback.print_exc()

        for key in list(self.clients.keys()):
            if key not in active_keys:
                self.clients.pop(key, None)
                self.fingerprints.pop(key, None)
                self.log(f"MULTI-EXCHANGE {key}: removed from registry")

        if not configs:
            self.log("MULTI-EXCHANGE: no valid exchanges loaded from live_exchanges.json")

        return loaded

    def health_check(self):
        try:
            loaded = load_exchange_configs(self.config_path)
            check_balance = bool(loaded.get("check_balance", True))
        except Exception:
            check_balance = True

        if not check_balance:
            return

        for key, client in list(self.clients.items()):
            try:
                balance = client.fetch_balance()
                total = balance.get("total", {}) if isinstance(balance, dict) else {}
                usdt = total.get("USDT")

                if usdt is None:
                    self.log(f"MULTI-EXCHANGE {key}: health OK")
                else:
                    self.log(f"MULTI-EXCHANGE {key}: health OK total USDT={usdt}")

            except Exception as e:
                self.log(f"MULTI-EXCHANGE {key}: health failed: {e}")

    def get_clients(self) -> Dict[str, Any]:
        return dict(self.clients)


_THREAD: Optional[threading.Thread] = None
_REGISTRY: Optional[MultiExchangeRegistry] = None


def start_multi_exchange_background_worker(
    log_func: Optional[Callable[[str], None]] = None,
    config_path: str = "live_exchanges.json",
):
    global _THREAD
    global _REGISTRY

    if ccxt is None:
        if log_func:
            log_func("MULTI-EXCHANGE: ccxt not installed")
        return None

    loaded = load_exchange_configs(config_path)

    if not loaded.get("enabled", True):
        if log_func:
            log_func("MULTI-EXCHANGE: disabled from live_exchanges.json")
        return None

    sleep_sec = max(float(loaded.get("sleep_sec", 60)), 10.0)

    if _REGISTRY is None:
        _REGISTRY = MultiExchangeRegistry(
            config_path=config_path,
            log_func=log_func,
        )

    if _THREAD is not None and _THREAD.is_alive():
        return _REGISTRY

    def worker():
        while True:
            try:
                latest = _REGISTRY.sync_from_file()
                _REGISTRY.health_check()

                new_sleep = max(float(latest.get("sleep_sec", sleep_sec)), 10.0)
                time.sleep(new_sleep)

            except Exception as e:
                _REGISTRY.log(f"MULTI-EXCHANGE: worker error: {e}")
                traceback.print_exc()
                time.sleep(sleep_sec)

    _THREAD = threading.Thread(
        target=worker,
        name="multi-exchange-connector",
        daemon=True,
    )
    _THREAD.start()

    _REGISTRY.log(f"MULTI-EXCHANGE: started using {config_path}")

    return _REGISTRY
