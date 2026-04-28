const axios = require('axios');

// Configuration
const BYBIT_API_BASE_URL = "https://api.bybit.com"; // Replace with actual Bybit API URL
const API_KEY = "your-api-key"; // Add your API Key
const API_SECRET = "your-api-secret"; // Add your API Secret
const UTA_WALLET_ID = "your-uta-wallet-id";
const FUNDING_WALLET_ID = "your-funding-wallet-id";
const RESERVED_AMOUNT = 501;

// Function to get wallet balance
async function getWalletBalance(walletId) {
    try {
        const response = await axios.get(`${BYBIT_API_BASE_URL}/wallet/balance`, {
            headers: {
                'Authorization': `Bearer ${API_KEY}`,
            },
            params: {
                walletId: walletId,
            },
        });
        return response.data.balance; // Assuming balance is returned in response
    } catch (error) {
        console.error("Error fetching wallet balance:", error.response.data);
        throw new Error("Failed to fetch wallet balance.");
    }
}

// Function to transfer funds
async function transferFunds(fromWallet, toWallet, amount) {
    try {
        const response = await axios.post(`${BYBIT_API_BASE_URL}/wallet/transfer`, {
            fromWalletId: fromWallet,
            toWalletId: toWallet,
            amount: amount,
        }, {
            headers: {
                'Authorization': `Bearer ${API_KEY}`,
            },
        });
        return response.data;
    } catch (error) {
        console.error("Error during fund transfer:", error.response.data);
        throw new Error("Fund transfer failed.");
    }
}

// Bot Logic
async function reserveAndTransfer() {
    try {
        const balance = await getWalletBalance(UTA_WALLET_ID);
        console.log(`Current Balance: ${balance} USDT`);

        if (balance > RESERVED_AMOUNT) {
            const surplus = balance - RESERVED_AMOUNT;
            console.log(`Transferring ${surplus} USDT to Funding Wallet.`);
            const transferResult = await transferFunds(UTA_WALLET_ID, FUNDING_WALLET_ID, surplus);
            console.log("Transfer Successful:", transferResult);
        } else {
            console.log("Balance is within the reserved amount. No action required.");
        }
    } catch (error) {
        console.error("Error in reserve and transfer bot:", error.message);
    }
}

// Run the bot periodically
setInterval(reserveAndTransfer, 60000); // Run every minute (60000 ms)