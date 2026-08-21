import json
import time
import websocket

URL = "wss://ws.bitget.com/v2/ws/public"


def on_open(ws):
    print("=== WEBSOCKET CONNECTED ===")

    payload = {
        "op": "subscribe",
        "args": [
            {
                "instType": "USDT-FUTURES",
                "channel": "ticker",
                "instId": "BTCUSDT"
            }
        ]
    }

    ws.send(json.dumps(payload))
    print("SUBSCRIPTION SENT")


def on_message(ws, message):
    print("BITGET WS:", message)


def on_error(ws, error):
    print("WS ERROR:", error)


def on_close(ws, close_status_code, close_msg):
    print("WS CLOSED:", close_status_code, close_msg)


while True:
    try:
        ws = websocket.WebSocketApp(
            URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

        ws.run_forever(
            ping_interval=20,
            ping_timeout=10
        )

    except Exception as e:
        print("RECONNECT ERROR:", repr(e))

    print("Reconnecting in 5 seconds...")
    time.sleep(5)
