# NORMAL-REGIME-BOT

## Bybit Reserve + Copy Trade Bot

This worker runs Bybit automation for up to 50 API accounts.

## Behavior

API #01 is the master account.

API #01 runs the original logic:
- reserve USDT in UNIFIED wallet
- transfer surplus UNIFIED -> FUND
- top-up FUND -> UNIFIED when a position exists
- close position when unrealised loss reaches the configured limit

API #02 to API #50 run the same reserve/top-up/loss-close logic with their own API keys and their own lock files.

API #02 to API #50 also copy API #01 trading activity:
- same symbol
- same side
- same BTC quantity
- same leverage
- close when API #01 closes

Default copied symbol is BTCUSDT.

## Environment Variables

Master:
- BYBIT_API_KEY
- BYBIT_API_SECRET

Followers:
- BYBIT_API_KEY_02 / BYBIT_API_SECRET_02
- ...
- BYBIT_API_KEY_50 / BYBIT_API_SECRET_50

Main settings:
- BYBIT_MODE=live
- MAX_API_ACCOUNTS=50
- FOLLOWER_RESERVE_ENABLED=true
- COPY_TRADE_ENABLED=true
- COPY_SYMBOLS=BTCUSDT
- RESERVE_USDT=501
- MIN_TRANSFER_USDT=1
- POSITION_TOPUP_USDT=50
- LOSS_CLOSE_USDT=70
- BOT_SLEEP_SEC=15
- POSITION_LOCK_FILE=/tmp/bybit_position_topup.lock

## Render Deployment

Use `render.yaml`, then add all API keys as secret environment variables in Render.

Do not commit `.env` or real API keys.
