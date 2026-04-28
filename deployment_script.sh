```
pkg update -y && \
pkg upgrade -y && \
dpkg --configure -a && \
apt --fix-broken install -y && \
pkg install python termux-api -y && \
hash -r && \
python --version && \
python -m ensurepip --upgrade && \
python -m pip install -U pip setuptools wheel && \
python -m pip install --force-reinstall pybit requests && \
termux-wake-lock && \
termux-notification \
  --id botnotif \
  --title "Bybit Reserve Bot Running" \
  --content "LIVE reserve watcher active" \
  --ongoing && \

export BYBIT_MODE="live" && \
export BYBIT_API_KEY="Ln6blvijhGZbDvYnzu" && \
export BYBIT_API_SECRET="hKlT7HDpDrkkjRKVXyoCj2CboV8DYELBnFGv" && \
export RESERVE_USDT="501" && \
export MIN_TRANSFER_USDT="1" && \
export POSITION_TOPUP_USDT="50" && \
export LOSS_CLOSE_USDT="70" && \
export POSITION_LOCK_FILE="$HOME/.bybit_position_topup.lock" && \
export BOT_SLEEP_SEC="15" && \

while true; do
python - << 'EOF'
# Python script logic here (same as provided earlier)...
EOF

sleep "${BOT_SLEEP_SEC:-15}"
done
```