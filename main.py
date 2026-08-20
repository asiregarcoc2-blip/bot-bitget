import os
import logging
from flask import Flask, request, jsonify
from bitget.mix.order_api import OrderApi

# Konfigurasi Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Membaca Data Credentials Bitget dari Environment Variables Render
BITGET_API_KEY = os.getenv("BITGET_API_KEY")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE")

# Token Rahasia untuk Keamanan Webhook (Bisa Diatur di Render & TradingView)
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "MY_SECRET_TOKEN_123")

# Inisialisasi API Bitget Mix (Futures)
order_api = OrderApi(BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)

@app.route('/', methods=['GET'])
def home():
    return "Bitget Webhook Bot is Running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        logging.info(f"Menerima Sinyal: {data}")

        # 1. Validasi Token Keamanan
        incoming_token = data.get("token")
        if incoming_token != WEBHOOK_SECRET_TOKEN:
            logging.warning("Sinyal ditolak: Token tidak valid!")
            return jsonify({"status": "error", "message": "Unauthorized token"}), 401

        # 2. Ambil Parameter Order
        action = data.get("action")  # 'open_long' atau 'open_short'
        symbol = data.get("symbol", "CYSUSDT_UMCBL")  # Simbol USDT-M Futures Bitget
        margin_coin = "USDT"
        
        # Pengaturan Ukuran Posisi
        # Catatan: Sesuaikan 'size' dengan jumlah kontrak/koin minimum yang diizinkan Bitget untuk pair terkait
        size = str(data.get("size", "1")) 

        # 3. Eksekusi Order Berdasarkan Aksi
        if action == "open_long":
            response = order_api.place_order(
                symbol=symbol,
                marginCoin=margin_coin,
                size=size,
                side="open_long",
                orderType="market",
                timeInForceValue="normal"
            )
            logging.info(f"Order Long Dieksekusi: {response}")
            return jsonify({"status": "success", "response": response}), 200

        elif action == "open_short":
            response = order_api.place_order(
                symbol=symbol,
                marginCoin=margin_coin,
                size=size,
                side="open_short",
                orderType="market",
                timeInForceValue="normal"
            )
            logging.info(f"Order Short Dieksekusi: {response}")
            return jsonify({"status": "success", "response": response}), 200

        else:
            return jsonify({"status": "error", "message": "Aksi tidak dikenal"}), 400

    except Exception as e:
        logging.error(f"Terjadi kesalahan saat memproses webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
