import traceback

import app
from cross_exchange_copy import start_cross_exchange_copy_worker


def main():
    try:
        start_cross_exchange_copy_worker(
            app_module=app,
            log_func=app.log,
            config_path="live_exchanges.json",
        )
    except Exception as e:
        app.log(f"CROSS-COPY: failed to start: {e}")
        traceback.print_exc()

    app.main()


if __name__ == "__main__":
    main()
