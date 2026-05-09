import traceback

from multi_exchange import start_multi_exchange_background_worker

import app


def main():
    try:
        start_multi_exchange_background_worker(
            log_func=app.log,
            config_path="live_exchanges.json",
        )
    except Exception as e:
        app.log(f"MULTI-EXCHANGE: failed to start: {e}")
        traceback.print_exc()

    app.main()


if __name__ == "__main__":
    main()
