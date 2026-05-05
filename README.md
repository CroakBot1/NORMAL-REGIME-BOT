# NORMAL-REGIME-BOT

## Bybit Reserve Bot - 50 Account Copy Trade Version

This bot keeps the old reserve logic and adds optional copy-trade behavior.

## Old Logic Preserved

The original flow remains:

1. Check open positions.
2. Close positions if unrealised loss reaches `LOSS_CLOSE_USDT`.
3. If no open position, transfer surplus from UNIFIED wallet to FUND wallet.
4. If there is an open position, transfer one-time top-up from FUND wallet to UNIFIED wallet.
5. Use per-account lock files for position top-up cycles.

## New Copy Trade Logic

When `COPY_TRADE_ENABLED=true`, account #1 becomes the leader.

If account #1 has an open position, accounts #2 to #50 can open the same position using:

```env
COPY_TRADE_LEVERAGE=60
COPY_TRADE_WALLET_PCT=0.90
```

Formula:

```text
margin_to_use = UNIFIED_wallet_balance * COPY_TRADE_WALLET_PCT
notional_order_value = margin_to_use * COPY_TRADE_LEVERAGE
qty = notional_order_value / current_market_price
```

Example:

```text
Wallet: 100 USDT
Wallet percent: 90%
Margin used: 90 USDT
Leverage: 60x
Position notional: 5,400 USDT
```

## Environment Variables

```env
BYBIT_MODE=live

RESERVE_USDT=501
MIN_TRANSFER_USDT=1
POSITION_TOPUP_USDT=50
LOSS_CLOSE_USDT=70
BOT_SLEEP_SEC=15
POSITION_LOCK_FILE=/tmp/bybit_position_topup.lock

COPY_TRADE_ENABLED=true
COPY_TRADE_LEADER_ACCOUNT=1
COPY_TRADE_FOLLOWERS_START=2
COPY_TRADE_FOLLOWERS_END=50
COPY_TRADE_LEVERAGE=60
COPY_TRADE_WALLET_PCT=0.90
COPY_TRADE_MIN_ORDER_USDT=5
COPY_TRADE_REQUIRE_NO_FOLLOWER_POSITION=true
COPY_TRADE_LOCK_PREFIX=/tmp/bybit_copy_trade.lock
```

## API Key Format

Use continuous numbering:

```env
BYBIT_API_KEY_1=your_first_api_key
BYBIT_API_SECRET_1=your_first_api_secret

BYBIT_API_KEY_2=your_second_api_key
BYBIT_API_SECRET_2=your_second_api_secret

BYBIT_API_KEY_50=your_fiftieth_api_key
BYBIT_API_SECRET_50=your_fiftieth_api_secret
```

Do not skip numbers.

Good:

```env
BYBIT_API_KEY_1=...
BYBIT_API_SECRET_1=...
BYBIT_API_KEY_2=...
BYBIT_API_SECRET_2=...
BYBIT_API_KEY_3=...
BYBIT_API_SECRET_3=...
```

Bad:

```env
BYBIT_API_KEY_1=...
BYBIT_API_SECRET_1=...
BYBIT_API_KEY_3=...
BYBIT_API_SECRET_3=...
```

If account 2 is missing, account 3 to 50 will not be loaded.

## Local Run

```bash
pip install -r requirements.txt
python app.py
```

## Render Deployment

Use `render.yaml`, then add real API keys and secrets in the Render dashboard.

Do not commit `.env` or real API credentials to GitHub.

## Security Reminder

If an API key or secret was pasted in chat, committed to GitHub, or shared publicly, rotate/delete it immediately in Bybit.

## Risk Reminder

Using 90% of wallet balance with 60x leverage is extremely risky and can liquidate accounts quickly. Test on testnet before live.
