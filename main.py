import ccxt
import time

exchange = ccxt.bitget({
    "enableRateLimit": True,
    "timeout": 10000,
    "options": {
        "defaultType": "swap",
        "defaultSettle": "usdt",
        "subType": "linear"
    }
})

print("=== BITGET CONNECTION TEST ===")

while True:
    try:
        ticker = exchange.fetch_ticker("BTC/USDT:USDT")

        print(
            f"BITGET OK | "
            f"BTCUSDT = {ticker['last']} | "
            f"Timestamp = {ticker['timestamp']}"
        )

    except Exception as e:
        print("BITGET ERROR:", repr(e))

    time.sleep(30)
