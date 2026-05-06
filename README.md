# NORMAL-REGIME-BOT

## Bybit Reserve + Copy Trade + Telegram Monitor Bot

This project runs the existing Bybit reserve/copy-trade worker and adds a Telegram monitor wrapper.

## Important Structure

The old `app.py` trading logic remains unchanged.

The new `telegram_app.py` imports `app.py` and runs:
1. API #01 old reserve/top-up/loss-close logic
2. API #02-#50 old copy-trade follower logic
3. API #02-#50 old reserve/top-up/loss-close logic
4. Telegram monitor and registration system

## Old Bot Behavior

API #01 is the master account.

API #01 runs:
- reserve USDT in UNIFIED wallet
- transfer surplus UNIFIED -> FUND
- top-up FUND -> UNIFIED when a position exists
- close position when unrealised loss reaches the configured limit

API #02 to API #50 run the same reserve/top-up/loss-close logic.

API #02 to API #50 copy API #01 trading activity:
- same symbol
- same side
- same BTC quantity
- same leverage
- close when API #01 closes

Default copied symbol is BTCUSDT.

## Telegram Monitor Behavior

Users can open the Telegram bot and use:

- /start
- /menu
- /paymentMethod
- /redeemAccessCode CODE
- /registerApi
- /connectApi
- /walletUtaBalance
- /apiStatus
- /positions
- /1monthlyIncome
- /1Monthexpiration
- /monitorNow
- /myAccount
- /deleteApi

## Payment and Access Flow

1. User opens bot with /start
2. User checks /paymentMethod
3. User pays/contact admin at https://t.me/Assistant_quantum
4. Admin gives one access code
5. User activates code with /redeemAccessCode CODE
6. The 1-month countdown starts when code is activated
7. User registers API with /registerApi
8. Bot monitors API key/secret connectivity and UTA wallet balance

## Security

Use a new Telegram bot token. Revoke any token that was shared publicly.

Use a new Bybit API key/secret if old keys were shared.

For Telegram monitor users, use read-only Bybit API keys when possible.

Set DATA_ENCRYPTION_KEY in Render and never change it. Saved API keys are encrypted using this key.

Do not commit `.env`.

## Render Deployment

Use `render.yaml`.

Make sure start command is:

```bash
python telegram_app.py
