import os
import time
import math
import requests
import ccxt
import pandas as pd
import pandas_ta as ta

# =====================================================
# 1. KONFIGURASI AMAN (MAMBACA DARI RENDER ENV)
# =====================================================

API_KEY = os.getenv('BITGET_API_KEY')
SECRET_KEY = os.getenv('BITGET_SECRET_KEY')
PASSPHRASE = os.getenv('BITGET_PASSPHRASE')

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = 'BTC/USDT:USDT'  # Pasangan Futures Bitget
TIMEFRAME_ENTRY = '5m'
TIMEFRAME_HTF_1 = '1h'
TIMEFRAME_HTF_2 = '4h'

# Parameter Strategi V4
EMA_PERIOD = 200
USE_EMA_FILTER = True

USE_DYNAMIC_SL = True
FIXED_SL_PERC = 0.03       # 3.0%
SL_BUFFER_PERC = 0.002     # 0.2%
SWING_LOOKBACK = 10
MIN_RR_REQUIRED = 1.0

USE_SWEEP_FILTER = True
MIN_SWEEP_DEPTH = 0.0030   # 0.30%

USE_RECLAIM_FILTER = True
RECLAIM_CLOSE_BUFFER = 0.0010  # 0.10%
MIN_BODY_RATIO = 0.50      # 50%

USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20
VOLUME_MULTIPLIER = 1.20

POSITION_SIZE_USDT = 10    # Modal Margin per Order ($)
LEVERAGE = 5               # Leverage Futures

# =====================================================
# 2. FUNGSI TELEGRAM NOTIFIKASI
# =====================================================

def send_telegram(message):
    """Mengirimkan notifikasi ke aplikasi Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARNING] Token/Chat ID Telegram belum diatur di Environment Variables.")
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
        print(f"[ERROR Telegram] Gagal mengirim pesan: {e}")

# =====================================================
# 3. INISIALISASI BITGET CLIENT (CCXT)
# =====================================================

exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'password': PASSPHRASE,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap', # Market Futures/Swap
    }
})

def set_leverage():
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL)
        print(f"[INIT] Leverage disetel ke {LEVERAGE}x")
    except Exception as e:
        print(f"[WARNING] Gagal set leverage: {e}")

# =====================================================
# 4. FUNGSI DATA CANDLESTICK
# =====================================================

def fetch_ohlcv(symbol, timeframe, limit=300):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# =====================================================
# 5. PEMPROSESAN STRATEGI & INDIKATOR
# =====================================================

def analyze_market():
    # Ambil Data Candlestick
    df_5m = fetch_ohlcv(SYMBOL, TIMEFRAME_ENTRY, limit=300)
    df_1h = fetch_ohlcv(SYMBOL, TIMEFRAME_HTF_1, limit=250)
    df_4h = fetch_ohlcv(SYMBOL, TIMEFRAME_HTF_2, limit=50)

    # 1. Calculate 1H EMA 200
    df_1h['ema200'] = ta.ema(df_1h['close'], length=EMA_PERIOD)
    htf_ema = df_1h['ema200'].iloc[-2]

    # 2. Calculate 4H High/Low Resistance/Support (Completed 4H Bar)
    ref_high = df_4h['high'].iloc[-2]
    ref_low = df_4h['low'].iloc[-2]

    # Data 5M Terbaru (Candle yang baru saja CLOSE)
    curr_candle = df_5m.iloc[-2]
    prev_candle = df_5m.iloc[-3]
    
    close_p = curr_candle['close']
    open_p = curr_candle['open']
    high_p = curr_candle['high']
    low_p = curr_candle['low']
    vol_p = curr_candle['volume']

    # 3. Filter Volume
    df_5m['vol_sma'] = ta.sma(df_5m['volume'], length=VOLUME_LENGTH)
    vol_sma = df_5m['vol_sma'].iloc[-2]
    volume_valid = not USE_VOLUME_FILTER or (vol_p >= vol_sma * VOLUME_MULTIPLIER)

    # 4. Filter Trend EMA
    trend_long_valid = not USE_EMA_FILTER or (close_p > htf_ema)
    trend_short_valid = not USE_EMA_FILTER or (close_p < htf_ema)

    # 5. Reclaim & Body Ratio Filter
    candle_range = high_p - low_p
    candle_body = abs(close_p - open_p)
    body_ratio = candle_body / candle_range if candle_range > 0 else 0

    long_reclaim = (prev_candle['close'] < ref_low) and (close_p > ref_low)
    short_reclaim = (prev_candle['close'] > ref_high) and (close_p < ref_high)

    long_reclaim_candle = (close_p > open_p) and (body_ratio >= MIN_BODY_RATIO) and (close_p >= ref_low * (1 + RECLAIM_CLOSE_BUFFER))
    short_reclaim_candle = (close_p < open_p) and (body_ratio >= MIN_BODY_RATIO) and (close_p <= ref_high * (1 - RECLAIM_CLOSE_BUFFER))

    # 6. Sweep Detection
    sweep_5m_bars = df_5m.tail(48) # 4 jam terakhir (48 bar 5M)
    
    lowest_sweep_price = sweep_5m_bars[sweep_5m_bars['low'] < ref_low]['low'].min()
    highest_sweep_price = sweep_5m_bars[sweep_5m_bars['high'] > ref_high]['high'].max()

    long_break_occurred = not math.isnan(lowest_sweep_price)
    short_break_occurred = not math.isnan(highest_sweep_price)

    long_sweep_depth = (ref_low - lowest_sweep_price) / ref_low if long_break_occurred else 0
    short_sweep_depth = (highest_sweep_price - ref_high) / ref_high if short_break_occurred else 0

    long_sweep_valid = not USE_SWEEP_FILTER or (long_sweep_depth >= MIN_SWEEP_DEPTH)
    short_sweep_valid = not USE_SWEEP_FILTER or (short_sweep_depth >= MIN_SWEEP_DEPTH)

    # Raw Condition Checks
    raw_long = long_break_occurred and long_reclaim and long_sweep_valid and long_reclaim_candle and trend_long_valid and volume_valid
    raw_short = short_break_occurred and short_reclaim and short_sweep_valid and short_reclaim_candle and trend_short_valid and volume_valid

    # 7. Swing High / Low 5M TP Calculation
    swing_high_5m = df_5m['high'].iloc[-SWING_LOOKBACK-1:-1].max()
    swing_low_5m = df_5m['low'].iloc[-SWING_LOOKBACK-1:-1].min()

    signal = None
    sl_price = None
    tp_price = None

    if raw_long:
        entry_p = close_p
        sl_p = (lowest_sweep_price * (1 - SL_BUFFER_PERC)) if USE_DYNAMIC_SL else (entry_p * (1 - FIXED_SL_PERC))
        tp_p = swing_high_5m
        
        risk_dist = entry_p - sl_p
        reward_dist = tp_p - entry_p
        rr = reward_dist / risk_dist if risk_dist > 0 else 0

        if risk_dist > 0 and reward_dist > 0 and rr >= MIN_RR_REQUIRED:
            signal = 'LONG'
            sl_price = sl_p
            tp_price = tp_p

    elif raw_short:
        entry_p = close_p
        sl_p = (highest_sweep_price * (1 + SL_BUFFER_PERC)) if USE_DYNAMIC_SL else (entry_p * (1 + FIXED_SL_PERC))
        tp_p = swing_low_5m
        
        risk_dist = sl_p - entry_p
        reward_dist = entry_p - tp_p
        rr = reward_dist / risk_dist if risk_dist > 0 else 0

        if risk_dist > 0 and reward_dist > 0 and rr >= MIN_RR_REQUIRED:
            signal = 'SHORT'
            sl_price = sl_p
            tp_price = tp_p

    return signal, close_p, sl_price, tp_price

# =====================================================
# 6. EKSEKUSI ORDER DI BITGET
# =====================================================

def execute_trade(signal, entry_price, sl_price, tp_price):
    positions = exchange.fetch_positions([SYMBOL])
    active_position = any(float(pos['contracts']) > 0 for pos in positions)

    if active_position:
        print("[INFO] Posisi masih terbuka. Melewati sinyal baru.")
        return

    amount = (POSITION_SIZE_USDT * LEVERAGE) / entry_price

    if signal == 'LONG':
        msg = (
            f"🚀 *BITGET SIGNAL: LONG*\n\n"
            f"• *Symbol:* `{SYMBOL}`\n"
            f"• *Entry Price:* `${entry_price:,.2f}`\n"
            f"• *Stop Loss:* `${sl_price:,.2f}`\n"
            f"• *Take Profit:* `${tp_price:,.2f}`\n"
            f"• *Margin:* `${POSITION_SIZE_USDT}` ({LEVERAGE}x)"
        )
        print(msg)
        send_telegram(msg)
        
        # Open Position & Order SL/TP
        exchange.create_market_buy_order(SYMBOL, amount)
        exchange.create_order(SYMBOL, 'market', 'sell', amount, params={'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(SYMBOL, 'market', 'sell', amount, params={'takeProfitPrice': tp_price, 'reduceOnly': True})

    elif signal == 'SHORT':
        msg = (
            f"🔻 *BITGET SIGNAL: SHORT*\n\n"
            f"• *Symbol:* `{SYMBOL}`\n"
            f"• *Entry Price:* `${entry_price:,.2f}`\n"
            f"• *Stop Loss:* `${sl_price:,.2f}`\n"
            f"• *Take Profit:* `${tp_price:,.2f}`\n"
            f"• *Margin:* `${POSITION_SIZE_USDT}` ({LEVERAGE}x)"
        )
        print(msg)
        send_telegram(msg)
        
        # Open Position & Order SL/TP
        exchange.create_market_sell_order(SYMBOL, amount)
        exchange.create_order(SYMBOL, 'market', 'buy', amount, params={'stopLossPrice': sl_price, 'reduceOnly': True})
        exchange.create_order(SYMBOL, 'market', 'buy', amount, params={'takeProfitPrice': tp_price, 'reduceOnly': True})

# =====================================================
# 7. MAIN LOOP
# =====================================================

def run_bot():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Memeriksa kondisi chart...")
    try:
        signal, entry_p, sl_p, tp_p = analyze_market()
        if signal:
            execute_trade(signal, entry_p, sl_p, tp_p)
        else:
            print("[STATUS] Tidak ada sinyal yang memenuhi kriteria.")
    except Exception as e:
        print(f"[ERROR] Terjadi kesalahan: {e}")

if __name__ == '__main__':
    set_leverage()
    send_telegram("🤖 *Bot Bitget Autotrade Aktif di Render!* \nSedang memantau chart 5M...")
    
    while True:
        run_bot()
        time.sleep(300) # Cek setiap 5 menit (300 detik)
