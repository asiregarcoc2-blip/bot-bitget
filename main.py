import os
import time
import json
import logging
import requests
import ccxt
import pandas as pd
import pandas_ta as ta
from threading import Thread
from flask import Flask

# Konfigurasi Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === 1. MINI WEB SERVER UNTUK RENDER / KEEP-ALIVE ===
app = Flask('')

@app.route('/')
def home():
    return "Bot Bitget Autotrade Interaktif (Demo Mode) Aktif & Running!", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# === 2. KONFIGURASI ENVIRONMENT VARIABLES & PERSIAPAN CONFIG ===
API_KEY = os.getenv('BITGET_API_KEY')
SECRET_KEY = os.getenv('BITGET_SECRET_KEY')
PASSPHRASE = os.getenv('BITGET_PASSPHRASE')

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "symbols": ["DOGE/USDT:USDT", "SOL/USDT:USDT", "LINK/USDT:USDT", "ADA/USDT:USDT"],
    "position_size_usdt": 10,
    "leverage": 5
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Gagal membaca config file, menggunakan default: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        logging.error(f"Gagal menyimpan config file: {e}")

# Memuat konfigurasi awal
config = load_config()

TIMEFRAME_ENTRY = '5m'
TIMEFRAME_HTF_1 = '1h'
TIMEFRAME_HTF_2 = '4h'

EMA_PERIOD = 200
USE_EMA_FILTER = True

USE_DYNAMIC_SL = True
FIXED_SL_PERC = 0.03
SL_BUFFER_PERC = 0.002
SWING_LOOKBACK = 10
MIN_RR_REQUIRED = 1.0

# === 3. FUNGSI TELEGRAM ===
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Token/Chat ID Telegram belum diatur.")
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

# === 4. INISIALISASI EXCHANGE (BITGET DEMO FUTURES VIA CCXT) ===
exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'password': PASSPHRASE,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
        'defaultSettle': 'usdt',
        'subType': 'linear',
        'fetchMarkets': ['swap'],
        'fetchCoins': False,
    },
})

exchange.set_sandbox_mode(True)

try:
    exchange.load_markets(reload=True, params={'type': 'swap'})
except Exception as e:
    logging.warning(f"Warning load markets demo: {e}")

def set_leverage(symbol, lev_val):
    try:
        exchange.set_leverage(lev_val, symbol)
        logging.info(f"[DEMO] Leverage disetel ke {lev_val}x untuk {symbol}")
    except Exception as e:
        logging.warning(f"Gagal set leverage {symbol}: {e}")

def set_all_leverage():
    for sym in config["symbols"]:
        set_leverage(sym, config["leverage"])

# === 5. STRATEGI SWEEP & RECLAIM (PEMBACAAN DATA) ===
def fetch_ohlcv(symbol, timeframe, limit=300):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, params={'type': 'swap'})
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def analyze_market(symbol):
    df_5m = fetch_ohlcv(symbol, TIMEFRAME_ENTRY, limit=300)
    df_1h = fetch_ohlcv(symbol, TIMEFRAME_HTF_1, limit=250)
    df_4h = fetch_ohlcv(symbol, TIMEFRAME_HTF_2, limit=50)

    df_1h['ema200'] = ta.ema(df_1h['close'], length=EMA_PERIOD)
    htf_ema = df_1h['ema200'].iloc[-2]

    ref_high = df_4h['high'].iloc[-2]
    ref_low = df_4h['low'].iloc[-2]

    curr_candle = df_5m.iloc[-2]
    prev_candle = df_5m.iloc[-3]
    close_p = curr_candle['close']

    trend_long_valid = not USE_EMA_FILTER or (close_p > htf_ema)
    trend_short_valid = not USE_EMA_FILTER or (close_p < htf_ema)

    sweep_5m_bars = df_5m.tail(48)
    lowest_sweep_price = sweep_5m_bars['low'].min()
    highest_sweep_price = sweep_5m_bars['high'].max()

    long_break_occurred = lowest_sweep_price < ref_low
    short_break_occurred = highest_sweep_price > ref_high

    long_reclaim = (prev_candle['close'] < ref_low) and (close_p > ref_low)
    short_reclaim = (prev_candle['close'] > ref_high) and (close_p < ref_high)

    raw_long = long_break_occurred and long_reclaim and trend_long_valid
    raw_short = short_break_occurred and short_reclaim and trend_short_valid

    swing_high_5m = df_5m['high'].iloc[-SWING_LOOKBACK-1:-1].max()
    swing_low_5m = df_5m['low'].iloc[-SWING_LOOKBACK-1:-1].min()

    signal, sl_price, tp_price = None, None, None

    if raw_long:
        entry_p = close_p
        sl_p = (lowest_sweep_price * (1 - SL_BUFFER_PERC)) if USE_DYNAMIC_SL else (entry_p * (1 - FIXED_SL_PERC))
        tp_p = swing_high_5m
        
        risk_dist = entry_p - sl_p
        reward_dist = tp_p - entry_p
        rr = reward_dist / risk_dist if risk_dist > 0 else 0

        if risk_dist > 0 and reward_dist > 0 and rr >= MIN_RR_REQUIRED:
            signal, sl_price, tp_price = 'LONG', sl_p, tp_p

    elif raw_short:
        entry_p = close_p
        sl_p = (highest_sweep_price * (1 + SL_BUFFER_PERC)) if USE_DYNAMIC_SL else (entry_p * (1 + FIXED_SL_PERC))
        tp_p = swing_low_5m
        
        risk_dist = sl_p - entry_p
        reward_dist = entry_p - tp_p
        rr = reward_dist / risk_dist if risk_dist > 0 else 0

        if risk_dist > 0 and reward_dist > 0 and rr >= MIN_RR_REQUIRED:
            signal, sl_price, tp_price = 'SHORT', sl_p, tp_p

    return signal, close_p, sl_price, tp_price

# === 6. EKSEKUSI ORDER ===
def execute_trade(symbol, signal, entry_price, sl_price, tp_price):
    positions = exchange.fetch_positions([symbol])
    active_position = any(float(pos['contracts']) > 0 for pos in positions)

    if active_position:
        logging.info(f"[DEMO] Posisi untuk {symbol} masih terbuka. Melewati sinyal.")
        return

    margin = config["position_size_usdt"]
    lev = config["leverage"]
    amount = (margin * lev) / entry_price

    if signal == 'LONG':
        msg = (
            f"🚀 *BITGET AUTOTRADE [DEMO]: LONG*\n\n"
            f"• *Symbol:* `{symbol}`\n"
            f"• *Entry:* `${entry_price:,.4f}`\n"
            f"• *SL:* `${sl_price:,.4f}`\n"
            f"• *TP:* `${tp_price:,.4f}`\n"
            f"• *Margin:* `${margin}` ({lev}x)"
        )
        send_telegram(msg)
        exchange.create_market_buy_order(symbol, amount)
        exchange.create_order(symbol, 'market', 'sell', amount, params={'triggerPrice': sl_price, 'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(symbol, 'market', 'sell', amount, params={'triggerPrice': tp_price, 'takeProfitPrice': tp_price, 'reduceOnly': True})

    elif signal == 'SHORT':
        msg = (
            f"🔻 *BITGET AUTOTRADE [DEMO]: SHORT*\n\n"
            f"• *Symbol:* `{symbol}`\n"
            f"• *Entry:* `${entry_price:,.4f}`\n"
            f"• *SL:* `${sl_price:,.4f}`\n"
            f"• *TP:* `${tp_price:,.4f}`\n"
            f"• *Margin:* `${margin}` ({lev}x)"
        )
        send_telegram(msg)
        exchange.create_market_sell_order(symbol, amount)
        exchange.create_order(symbol, 'market', 'buy', amount, params={'triggerPrice': sl_price, 'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(symbol, 'market', 'buy', amount, params={'triggerPrice': tp_price, 'takeProfitPrice': tp_price, 'reduceOnly': True})

# === 7. LISTENER PERINTAH INTERAKTIF TELEGRAM ===
def process_telegram_commands():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=10"
            res = requests.get(url).json()

            for result in res.get("result", []):
                last_update_id = result["update_id"]
                message = result.get("message", {})
                text = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id"))

                if chat_id != TELEGRAM_CHAT_ID:
                    continue

                # Perintah: /start atau /help
                if text in ["/start", "/help"]:
                    help_msg = (
                        "🤖 *KONTROL BOT TELEGRAM*\n\n"
                        "• `/list` : Lihat koin dipantau & pengaturan\n"
                        "• `/price KOIN` : Cek harga Futures terkini (misal: `/price ETH` atau `/p BTC`)\n"
                        "• `/add KOIN1 KOIN2` : Tambah koin (misal: `/add XRP` atau `/add XRP BTC`)\n"
                        "• `/del KOIN1 KOIN2` : Hapus koin (misal: `/del DOGE`)\n"
                        "• `/margin NOMINAL` : Ubah modal/posisi dalam USDT (misal: `/margin 20`)\n"
                        "• `/leverage ANGKA` : Ubah leverage (misal: `/leverage 10`)"
                    )
                    send_telegram(help_msg)

                # Perintah: /list
                elif text == "/list":
                    coins_fmt = "\n".join([f"• `{s}`" for s in config["symbols"]])
                    status_msg = (
                        f"📋 *STATUS & PENGATURAN BOT*\n\n"
                        f"• *Modal per Posisi:* `${config['position_size_usdt']} USDT`\n"
                        f"• *Leverage:* `{config['leverage']}x`\n"
                        f"• *Total Koin ({len(config['symbols'])}):*\n{coins_fmt}"
                    )
                    send_telegram(status_msg)

                # Perintah: /price KOIN atau /p KOIN
                elif text.startswith("/price") or text.startswith("/p "):
                    parts = text.split()
                    if len(parts) > 1:
                        coin = parts[1].upper().replace("USDT", "").replace("/", "")
                        target_symbol = f"{coin}/USDT:USDT"
                        try:
                            ticker = exchange.fetch_ticker(target_symbol, params={'type': 'swap'})
                            last_price = ticker['last']
                            high_24h = ticker.get('high', 0)
                            low_24h = ticker.get('low', 0)
                            change_24h = ticker.get('percentage', 0)

                            change_icon = "📈" if change_24h >= 0 else "📉"
                            
                            price_msg = (
                                f"📊 *HARGA FUTURES: {target_symbol}*\n\n"
                                f"• *Harga Saat Ini:* `${last_price:,.4f}`\n"
                                f"• *Perubahan 24j:* `{change_24h:+.2f}%` {change_icon}\n"
                                f"• *Tertinggi 24j:* `${high_24h:,.4f}`\n"
                                f"• *Terendah 24j:* `${low_24h:,.4f}`"
                            )
                            send_telegram(price_msg)
                        except Exception as e:
                            send_telegram(f"❌ Gagal mengambil harga untuk `{target_symbol}`. Pastikan koin tersedia di Futures.")
                    else:
                        send_telegram("⚠️ Format salah. Contoh: `/price ETH` atau `/p BTC`")

                # Perintah: /add (Bisa satu atau banyak koin sekaligus)
                elif text.startswith("/add"):
                    parts = text.split()[1:]
                    if parts:
                        added, exists = [], []
                        for coin in parts:
                            formatted = f"{coin.upper().replace('USDT', '').replace('/', '')}/USDT:USDT"
                            if formatted not in config["symbols"]:
                                config["symbols"].append(formatted)
                                set_leverage(formatted, config["leverage"])
                                added.append(formatted)
                            else:
                                exists.append(formatted)
                        
                        save_config(config)
                        reply = ""
                        if added:
                            reply += f"✅ *Ditambahkan:* {', '.join(added)}\n"
                        if exists:
                            reply += f"⚠️ *Sudah Ada:* {', '.join(exists)}"
                        send_telegram(reply)
                    else:
                        send_telegram("⚠️ Format salah. Contoh: `/add XRP` atau `/add XRP ETH BTC`")

                # Perintah: /del (Bisa satu atau banyak koin sekaligus)
                elif text.startswith("/del"):
                    parts = text.split()[1:]
                    if parts:
                        removed, not_found = [], []
                        for coin in parts:
                            formatted = f"{coin.upper().replace('USDT', '').replace('/', '')}/USDT:USDT"
                            if formatted in config["symbols"]:
                                config["symbols"].remove(formatted)
                                removed.append(formatted)
                            else:
                                not_found.append(formatted)

                        save_config(config)
                        reply = ""
                        if removed:
                            reply += f"🗑️ *Dihapus:* {', '.join(removed)}\n"
                        if not_found:
                            reply += f"⚠️ *Tidak Ditemukan:* {', '.join(not_found)}"
                        send_telegram(reply)
                    else:
                        send_telegram("⚠️ Format salah. Contoh: `/del DOGE` atau `/del DOGE ADA`")

                # Perintah: /margin NOMINAL
                elif text.startswith("/margin"):
                    parts = text.split()
                    if len(parts) > 1 and parts[1].replace('.', '', 1).isdigit():
                        new_margin = float(parts[1])
                        config["position_size_usdt"] = new_margin
                        save_config(config)
                        send_telegram(f"💵 *Modal per posisi diubah menjadi:* `${new_margin} USDT`")
                    else:
                        send_telegram("⚠️ Format salah. Contoh: `/margin 20` untuk $20 USDT.")

                # Perintah: /leverage LEVERAGE
                elif text.startswith("/leverage"):
                    parts = text.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        new_lev = int(parts[1])
                        config["leverage"] = new_lev
                        save_config(config)
                        set_all_leverage()
                        send_telegram(f"⚡ *Leverage diubah menjadi:* `{new_lev}x` untuk semua koin.")
                    else:
                        send_telegram("⚠️ Format salah. Contoh: `/leverage 10` untuk 10x.")

        except Exception as e:
            logging.error(f"Error pada listener Telegram: {e}")

        time.sleep(2)

# === 8. LOOP UTAMA BOT ===
def run_bot():
    for symbol in config["symbols"]:
        logging.info(f"[DEMO] Memeriksa kondisi chart untuk {symbol}...")
        try:
            signal, entry_p, sl_p, tp_p = analyze_market(symbol)
            if signal:
                execute_trade(symbol, signal, entry_p, sl_p, tp_p)
            else:
                logging.info(f"[DEMO] {symbol}: Tidak ada sinyal.")
        except Exception as e:
            logging.error(f"[DEMO] Kesalahan pada {symbol}: {e}")

        time.sleep(1)

if __name__ == '__main__':
    set_all_leverage()

    # Thread Keep-Alive Server
    Thread(target=run_web_server).start()

    # Thread Listener Perintah Telegram Interaktif
    Thread(target=process_telegram_commands, daemon=True).start()

    send_telegram("🤖 *Bot Interaktif Ready!*\nKirim `/help` untuk melihat daftar perintah.")

    # Loop Strategi Utama
    while True:
        run_bot()
        time.sleep(300)
