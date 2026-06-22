# ============================================
# Bybit Bot — Финальная версия
# Пик 93-98% → Откат 70-79% + EMA200 + 24ч рост
# ============================================

import requests
import pandas as pd
import numpy as np
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

TELEGRAM_TOKEN = "ВАШ_ТОКЕН_ЗДЕСЬ"
TELEGRAM_ID    = "7495689566"

PEAK_MIN      = 93
PEAK_MAX      = 98
PEAK_LOOKBACK = 6
ZONE_LOW      = 70
ZONE_HIGH     = 79
EMA_PERIOD    = 200
EMA_MIN_DIST  = 1.5
EMA_MAX_DIST  = 12.0
INTERVAL      = "30"
SLEEP_MIN     = 30
CANDLES       = 250
MAX_WORKERS   = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

bot_status = {
    "start_time": datetime.now(), "last_check": None,
    "pairs_checked": 0, "total_pairs": 0,
    "cycles_done": 0, "last_update_id": 0,
    "signals_sent": 0
}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_ID, "text": message, "parse_mode": "HTML"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def get_all_pairs():
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200: time.sleep(5); continue
            data = r.json()
            if data.get("retCode") != 0: time.sleep(5); continue
            return [i["symbol"] for i in data["result"]["list"]
                    if i["quoteCoin"] == "USDT" and i["status"] == "Trading"]
        except: time.sleep(5)
    return []

def get_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category":"linear","symbol":symbol,"interval":INTERVAL,"limit":CANDLES}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        if data.get("retCode") != 0: return None
        candles = data["result"]["list"]
        if not candles: return None
        df = pd.DataFrame(candles, columns=["time","open","high","low","close","volume","turnover"])
        df = df.astype({"open":"float","high":"float","low":"float","close":"float","volume":"float"})
        return df.iloc[::-1].reset_index(drop=True)
    except: return None

def get_24h_change(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category":"linear","symbol":symbol}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        if data.get("retCode") != 0: return None
        item = data["result"]["list"][0]
        price24h = float(item.get("prevPrice24h", 0))
        last = float(item.get("lastPrice", 0))
        if price24h > 0:
            return round((last - price24h) / price24h * 100, 2)
        return None
    except: return None

def calc_index_series(df):
    try:
        close=df["close"]; high=df["high"]; low=df["low"]; volume=df["volume"]; n=14
        delta=close.diff()
        gain=delta.clip(lower=0).rolling(n).mean()
        loss=(-delta.clip(upper=0)).rolling(n).mean()
        rsi=100-(100/(1+gain/loss.replace(0,np.nan)))
        low_n=low.rolling(n).min(); high_n=high.rolling(n).max()
        stoch=100*(close-low_n)/(high_n-low_n).replace(0,np.nan)
        wpr=100+(100*(close-high_n)/(high_n-low_n).replace(0,np.nan))
        hlc3=(high+low+close)/3; mf=hlc3*volume
        pos_mf=mf.where(hlc3>hlc3.shift(1),0).rolling(n).sum()
        neg_mf=mf.where(hlc3<hlc3.shift(1),0).rolling(n).sum()
        mfi=100-(100/(1+pos_mf/neg_mf.replace(0,np.nan)))
        idx_s=pd.Series(range(len(close)))
        rosc=(close.rolling(n).corr(idx_s)+1)/2*100
        rank=close.rolling(100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1]*100)
        ema12=close.ewm(span=12).mean(); ema26=close.ewm(span=26).mean()
        macd=ema12-ema26; sig=macd.ewm(span=9).mean()
        diff=macd-sig; dmax=diff.abs().rolling(100).max()
        macd_n=50+(diff/dmax.replace(0,np.nan))*50
        body=(close-df["open"]).abs(); rng=(high-low).replace(0,np.nan)
        jap=pd.Series(np.where(close>df["open"],body/rng*100,(1-body/rng)*100),index=close.index)
        return (rsi+stoch+rosc+wpr+rank+mfi+macd_n+jap)/8
    except: return None

def check_pair(symbol):
    df = get_candles(symbol)
    if df is None or len(df) < 210: return None

    index_series = calc_index_series(df)
    if index_series is None: return None

    idx_current = round(index_series.iloc[-1], 1)
    price = df["close"].iloc[-1]

    if not (ZONE_LOW <= idx_current <= ZONE_HIGH): return None

    recent = index_series.iloc[-(PEAK_LOOKBACK+1):-1]
    peak_mask = (recent >= PEAK_MIN) & (recent <= PEAK_MAX)
    if not peak_mask.any(): return None

    peak_value = recent[peak_mask].max()
    peak_positions = recent[peak_mask].index.tolist()
    last_peak_pos = peak_positions[-1]
    candles_ago = len(df) - 1 - last_peak_pos

    if idx_current >= peak_value: return None

    ema200 = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean().iloc[-1]
    if price <= ema200: return None

    ema_dist = (price - ema200) / ema200 * 100
    if ema_dist < EMA_MIN_DIST or ema_dist > EMA_MAX_DIST: return None

    if ema_dist < 3:
        ema_label = "Слабый 🟡"
    elif ema_dist < 7:
        ema_label = "Хороший 🟢"
    else:
        ema_label = "Сильный 🟢"

    change_24h = get_24h_change(symbol)
    change_str = f"+{change_24h}%" if change_24h and change_24h > 0 else (f"{change_24h}%" if change_24h else "н/д")

    return {
        "symbol": symbol,
        "price": price,
        "idx_current": idx_current,
        "peak_value": round(peak_value, 1),
        "candles_ago": candles_ago,
        "ema_dist": round(ema_dist, 1),
        "ema_label": ema_label,
        "change_24h": change_str,
    }

def listen_commands():
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": bot_status["last_update_id"] + 1, "timeout": 20}
            r = requests.get(url, params=params, timeout=25)
            updates = r.json().get("result", [])
            for upd in updates:
                bot_status["last_update_id"] = upd["update_id"]
                text = upd.get("message", {}).get("text", "")
                if text == "/status":
                    uptime = datetime.now() - bot_status["start_time"]
                    h = uptime.seconds // 3600; m = (uptime.seconds % 3600) // 60
                    send_telegram(
                        f"✅ <b>Бот работает!</b>\n\n"
                        f"⏱ Аптайм: {h}ч {m}мин\n"
                        f"🔄 Циклов: {bot_status['cycles_done']}\n"
                        f"📊 Пар: {bot_status['pairs_checked']}/{bot_status['total_pairs']}\n"
                        f"🔥 Сигналов: {bot_status['signals_sent']}\n"
                        f"🕐 Последняя проверка: {bot_status['last_check'] or 'ещё не было'}"
                    )
        except: time.sleep(5)

def main():
    threading.Thread(target=listen_commands, daemon=True).start()
    send_telegram(
        "🔥 Бот запущен!\n"
        "Стратегия: Пик 93-98% → Откат 70-79% + EMA200\n"
        "Таймфрейм: 30 минут\n\n"
        "Напиши /status для проверки"
    )

    alerted = {}

    while True:
        now = datetime.now().strftime("%H:%M")
        start = time.time()
        print(f"[{now}] Проверяю пары...")

        pairs = get_all_pairs()
        if not pairs: time.sleep(120); continue
        bot_status["total_pairs"] = len(pairs)
        print(f"Найдено пар: {len(pairs)}")

        checked = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(check_pair, s): s for s in pairs}
            for future in as_completed(futures):
                result = future.result()
                checked += 1
                bot_status["pairs_checked"] = checked
                if result is None: continue

                symbol = result["symbol"]
                prev = alerted.get(symbol, 100)
                if prev <= ZONE_HIGH: continue

                msg = (
                    f"🔥 ТОП LONG SIGNAL\n\n"
                    f"Монета: <b>{symbol}</b>\n\n"
                    f"Рост за 24 часа: <b>{result['change_24h']}</b>\n\n"
                    f"Пик индекса: <b>{result['peak_value']}%</b>\n"
                    f"Текущий индекс: <b>{result['idx_current']}%</b>\n\n"
                    f"Пик был: <b>{result['candles_ago']} свечи назад</b>\n\n"
                    f"EMA200: <b>{result['ema_label']}</b>\n"
                    f"Цена выше EMA200 на <b>{result['ema_dist']}%</b>\n\n"
                    f"Текущая цена: <b>{result['price']}</b>\n\n"
                    f"Таймфрейм: 30 минут"
                )
                send_telegram(msg)
                bot_status["signals_sent"] += 1
                alerted[symbol] = result["idx_current"]
                print(f"🔥 СИГНАЛ: {symbol} пик={result['peak_value']}% текущий={result['idx_current']}%")

        elapsed = round(time.time() - start, 1)
        bot_status["last_check"] = datetime.now().strftime("%H:%M:%S")
        bot_status["cycles_done"] += 1
        print(f"✅ Завершено за {elapsed} сек. Жду {SLEEP_MIN} минут...\n")
        time.sleep(SLEEP_MIN * 60)

if __name__ == "__main__":
    main()
