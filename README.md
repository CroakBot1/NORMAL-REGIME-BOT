# NORMAL-REGIME-BOT

## Bybit Reserve Bot

This bot monitors and manages the USDT balance in Bybit's UTA wallet, ensuring 501 USDT is always reserved. Any surplus above this amount is transferred to the funding wallet.

### Features
- **Reserve Automation**: Maintains a minimum reserve of 501 USDT in the UTA wallet.
- **Surplus Handling**: Transfers amounts exceeding the reserve to the funding wallet automatically.
- **Customizable Parameters**: Adjust reserve amount, thresholds, and API credentials easily.

### Setup Instructions

#### Prerequisites
1. Install [Termux](https://termux.dev/) on your device.
2. Install Python (version 3.8+).
3. Have Bybit API credentials (API key and secret).

#### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/CroakBot1/NORMAL-REGIME-BOT.git
   cd NORMAL-REGIME-BOT
   ```

2. Update the deployment script with your Bybit API credentials:
   Open `deployment_script.sh` and set the following environment variables:
   - `BYBIT_API_KEY`
   - `BYBIT_API_SECRET`

3. Run the deployment script:
   ```bash
   bash deployment_script.sh
   ```

The bot will now start monitoring and managing the balance.

### Deployment
- This bot can also be deployed on Render.com for persistent background execution.

### Files
- **`bot.js`**: Implements the bot logic.
- **`deployment_script.sh`**: Automates installation and execution.

### License
This project is for educational purposes.
