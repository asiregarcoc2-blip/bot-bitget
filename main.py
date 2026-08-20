import os
import logging
import requests
from flask import Flask, request, jsonify
from bitget.mix.order_api import OrderApi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Credentials Bitget
BITGET_API_KEY = os.getenv("BITGET_API_KEY")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE")

# Token & Chat ID Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Token Keamanan Webhook
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "MY_SECRET_TOKEN_123")

order_api = OrderApi(BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)

# Fungsi Kirim Notifikasi Telegram
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Token atau Chat ID Telegram belum diatur.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Gagal mengirim pesan Telegram: {e}")

@app.route('/', methods=['GET'])
def home():
    return "Bitget Webhook Bot + Telegram Active!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        logging.info(f"Menerima Sinyal: {data}")

        if data.get("token") != WEBHOOK_SECRET_TOKEN:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        action = data.get("action")
        symbol = data.get("symbol", "CYSUSDT_UMCBL")
        size = str(data.get("size", "1"))

        if action == "open_long":
            response = order_api.place_order(
                symbol=symbol, marginCoin="USDT", size=size,
                side="open_long", orderType="market", timeInForceValue="normal"
            )
            # Notifikasi Telegram
            msg = f"🚀 *EKSEKUSI LONG SUCCESS*\n\n• *Symbol:* `{symbol}`\n• *Size:* `{size}`\n• *Status:* Order Market Terpasang"
            send_telegram(msg)
            return jsonify({"status": "success", "response": response}), 200

        elif action == "open_short":
            response = order_api.place_order(
                symbol=symbol, marginCoin="USDT", size=size,
                side="open_short", orderType="market", timeInForceValue="normal"
            )
            # Notifikasi Telegram
            msg = f"🔻 *EKSEKUSI SHORT SUCCESS*\n\n• *Symbol:* `{symbol}`\n• *Size:* `{size}`\n• *Status:* Order Market Terpasang"
            send_telegram(msg)
            return jsonify({"status": "success", "response": response}), 200

        else:
            return jsonify({"status": "error", "message": "Aksi tidak dikenal"}), 400

    except Exception as e:
        error_msg = f"❌ *ORDER FAILED*\n\n• *Error:* `{str(e)}`"
        send_telegram(error_msg)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
