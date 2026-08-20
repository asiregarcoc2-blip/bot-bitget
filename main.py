import os
import time
import math
import logging
import requests
import ccxt
import pandas as pd
import pandas_ta as ta
from threading import Thread
from flask import Flask

# Konfigurasi Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === 1. MINI WEB SERVER UNTUK RENDER (KEEP-ALIVE) ===
app = Flask('')

@app.route('/')
def home():
    return "Bot Bitget Autotrade Mandiri Aktif & Running!", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# === 2. KONFIGURASI ENVIRONMENT VARIABLES ===
API_KEY = os.getenv('BITGET_API_KEY')
SECRET_KEY = os.getenv('BITGET_SECRET_KEY')
PASSPHRASE = os.getenv('BITGET_PASSPHRASE')

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = 'BTW/USDT:USDT'  # Pasangan Futures Bitget
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

POSITION_SIZE_USDT = 10
LEVERAGE = 5

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

# === 4. INISIALISASI EXCHANGE (BITGET FUTURES VIA CCXT) ===
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
        'fetchCoins': False,  # Mematikan pemanggilan endpoint Spot Bitget
    },
})

# Bypass pemanggilan endpoint Spot dan muat pasar Futures saja
try:
    exchange.load_markets(reload=True, params={'type': 'swap'})
except Exception as e:
    logging.warning(f"Warning load markets: {e}")

def set_leverage():
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL)
        logging.info(f"Leverage disetel ke {LEVERAGE}x untuk {SYMBOL}")
    except Exception as e:
        logging.warning(f"Gagal set leverage: {e}")

# === 5. STRATEGI SWEEP & RECLAIM (PEMBACAAN DATA) ===
def fetch_ohlcv(symbol, timeframe, limit=300):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, params={'type': 'swap'})
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def analyze_market():
    df_5m = fetch_ohlcv(SYMBOL, TIMEFRAME_ENTRY, limit=300)
    df_1h = fetch_ohlcv(SYMBOL, TIMEFRAME_HTF_1, limit=250)
    df_4h = fetch_ohlcv(SYMBOL, TIMEFRAME_HTF_2, limit=50)

    # Indicator EMA 200 (1H)
    df_1h['ema200'] = ta.ema(df_1h['close'], length=EMA_PERIOD)
    htf_ema = df_1h['ema200'].iloc[-2]

    # Level Support/Resistance (4H)
    ref_high = df_4h['high'].iloc[-2]
    ref_low = df_4h['low'].iloc[-2]

    curr_candle = df_5m.iloc[-2]
    prev_candle = df_5m.iloc[-3]
    
    close_p = curr_candle['close']

    # Filter Trend EMA
    trend_long_valid = not USE_EMA_FILTER or (close_p > htf_ema)
    trend_short_valid = not USE_EMA_FILTER or (close_p < htf_ema)

    # Deteksi Sweep (48 candle 5M = 4 jam terakhir)
    sweep_5m_bars = df_5m.tail(48)
    lowest_sweep_price = sweep_5m_bars['low'].min()
    highest_sweep_price = sweep_5m_bars['high'].max()

    long_break_occurred = lowest_sweep_price < ref_low
    short_break_occurred = highest_sweep_price > ref_high

    # Deteksi Reclaim
    long_reclaim = (prev_candle['close'] < ref_low) and (close_p > ref_low)
    short_reclaim = (prev_candle['close'] > ref_high) and (close_p < ref_high)

    raw_long = long_break_occurred and long_reclaim and trend_long_valid
    raw_short = short_break_occurred and short_reclaim and trend_short_valid

    # Perhitungan Swing High/Low untuk Target TP
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
def execute_trade(signal, entry_price, sl_price, tp_price):
    positions = exchange.fetch_positions([SYMBOL])
    active_position = any(float(pos['contracts']) > 0 for pos in positions)

    if active_position:
        logging.info("Posisi masih terbuka. Melewati sinyal baru.")
        return

    amount = (POSITION_SIZE_USDT * LEVERAGE) / entry_price

    if signal == 'LONG':
        msg = (
            f"🚀 *BITGET AUTOTRADE: LONG*\n\n"
            f"• *Symbol:* `{SYMBOL}`\n"
            f"• *Entry:* `${entry_price:,.4f}`\n"
            f"• *SL:* `${sl_price:,.4f}`\n"
            f"• *TP:* `${tp_price:,.4f}`\n"
            f"• *Margin:* `${POSITION_SIZE_USDT}` ({LEVERAGE}x)"
        )
        send_telegram(msg)
        exchange.create_market_buy_order(SYMBOL, amount)
        exchange.create_order(SYMBOL, 'market', 'sell', amount, params={'triggerPrice': sl_price, 'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(SYMBOL, 'market', 'sell', amount, params={'triggerPrice': tp_price, 'takeProfitPrice': tp_price, 'reduceOnly': True})

    elif signal == 'SHORT':
        msg = (
            f"🔻 *BITGET AUTOTRADE: SHORT*\n\n"
            f"• *Symbol:* `{SYMBOL}`\n"
            f"• *Entry:* `${entry_price:,.4f}`\n"
            f"• *SL:* `${sl_price:,.4f}`\n"
            f"• *TP:* `${tp_price:,.4f}`\n"
            f"• *Margin:* `${POSITION_SIZE_USDT}` ({LEVERAGE}x)"
        )
        send_telegram(msg)
        exchange.create_market_sell_order(SYMBOL, amount)
        exchange.create_order(SYMBOL, 'market', 'buy', amount, params={'triggerPrice': sl_price, 'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(SYMBOL, 'market', 'buy', amount, params={'triggerPrice': tp_price, 'takeProfitPrice': tp_price, 'reduceOnly': True})

# === 7. LOOP UTAMA BOT ===
def run_bot():
    logging.info(f"Memeriksa kondisi chart untuk {SYMBOL}...")
    try:
        signal, entry_p, sl_p, tp_p = analyze_market()
        if signal:
            execute_trade(signal, entry_p, sl_p, tp_p)
        else:
            logging.info("Tidak ada sinyal yang memenuhi kriteria.")
    except Exception as e:
        logging.error(f"Terjadi kesalahan saat mengeksekusi bot: {e}")

if __name__ == '__main__':
    set_leverage()
    send_telegram(f"🤖 *Bot Autotrade Bitget Active!*\nSedang memantau koin `{SYMBOL}`...")
    
    # Jalankan Server Web Mini untuk Render Keep-Alive
    Thread(target=run_web_server).start()
    
    # Loop Pengecekan Setiap 5 Menit (300 Detik)
    while True:
        run_bot()
        time.sleep(300)
