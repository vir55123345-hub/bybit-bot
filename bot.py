# ============================================
# Bybit INTELLECT_city Alert Bot — Финальная версия
# ============================================

import requests
import pandas as pd
import numpy as np
import time
import threading
from datetime import datetime

TELEGRAM_TOKEN = "8288068435:AAEN8PGNxU4JS0oqNAOcQxpa5AmgvGJ78pQ"
TELEGRAM_ID    = "7495689566"

UPPER_LEVEL  = 92
LOWER_MIN    = 1
LOWER_MAX    = 5
INTERVAL     = "30"
SLEEP_MIN    = 30
CANDLES      = 200
MAX_WORKERS  = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

bot_status = {
    "start_time": datetime.now(),
    "last_check": None,
    "pairs_checked": 0,
    "total_pairs": 0,
    "cycles_done": 0,
    "last_update_id": 0
}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def get_all_pairs():
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"⚠️ Bybit вернул статус {r.status_code}, пробую снова...")
                time.sleep(5)
                continue
            data = r.json()
            if data.get("retCode") != 0:
                time.sleep(5)
                continue
            pairs = []
            for item in data["result"]["list"]:
                if item["quoteCoin"] == "USDT" and item["status"] == "Trading":
                    pairs.append(item["symbol"])
            return pairs
        except Exception as e:
            print(f"⚠️ Ошибка (попытка {attempt+1}/3): {e}")
            time.sleep(5)
    return []

def get_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": INTERVAL, "limit": CANDLES}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("retCode") != 0:
            return None
        candles = data["result"]["list"]
        if not candles:
            return None
        df = pd.DataFrame(candles, columns=["time","open","high","low","close","volume","turnover"])
        df = df.astype({"open":"float","high":"float","low":"float","close":"float","volume":"float"})
        return df.iloc[::-1].reset_index(drop=True)
    except:
        return None

def calc_index(df):
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
        idx=pd.Series(range(len(close)))
        rosc=(close.rolling(n).corr(idx)+1)/2*100
        rank=close.rolling(100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1]*100)
        ema12=close.ewm(span=12).mean(); ema26=close.ewm(span=26).mean()
        macd=ema12-ema26; sig=macd.ewm(span=9).mean()
        diff=macd-sig; dmax=diff.abs().rolling(100).max()
        macd_n=50+(diff/dmax.replace(0,np.nan))*50
        body=(close-df["open"]).abs(); rng=(high-low).replace(0,np.nan)
        jap=pd.Series(np.where(close>df["open"],body/rng*100,(1-body/rng)*100),index=close.index)
        index=(rsi+stoch+rosc+wpr+rank+mfi+macd_n+jap)/8
        return round(index.iloc[-1],2)
    except:
        return None

def check_pair(symbol):
    df = get_candles(symbol)
    if df is None or len(df) < 150:
        return None
    idx = calc_index(df)
    if idx is None:
        return None
    price = df["close"].iloc[-1]
    return (symbol, idx, price)

def listen_commands():
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": bot_status["last_update_id"] + 1, "timeout": 20}
            r = requests.get(url, params=params, timeout=25)
            updates = r.json().get("result", [])
            for upd in updates:
                bot_status["last_update_id"] = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                if text == "/status":
                    uptime = datetime.now() - bot_status["start_time"]
                    hours = uptime.seconds // 3600
                    minutes = (uptime.seconds % 3600) // 60
                    last_check = bot_status["last_check"] or "ещё не было"
                    status_msg = (
                        f"✅ <b>Бот работает!</b>\n\n"
                        f"⏱ Аптайм: {hours}ч {minutes}мин\n"
                        f"🔄 Циклов: {bot_status['cycles_done']}\n"
                        f"📊 Пар проверено: {bot_status['pairs_checked']}/{bot_status['total_pairs']}\n"
                        f"🕐 Последняя проверка: {last_check}\n"
                        f"🔴 Верх: {UPPER_LEVEL}%+ | 🟢 Низ: {LOWER_MIN}-{LOWER_MAX}%"
                    )
                    send_telegram(status_msg)
        except:
            time.sleep(5)

def main():
    listener_thread = threading.Thread(target=listen_commands, daemon=True)
    listener_thread.start()

    send_telegram(f"🤖 Бот запущен на компьютере!\n🔴 Алерт перекупленность: {UPPER_LEVEL}%+\n🟢 Алерт перепроданность: {LOWER_MIN}-{LOWER_MAX}%\n\nНапиши /status для проверки")

    alerted_upper = {}
    alerted_lower = {}

    while True:
        now = datetime.now().strftime("%H:%M")
        start_time = time.time()
        print(f"[{now}] Проверяю пары...")

        pairs = get_all_pairs()
        if not pairs:
            print("⚠️ Не удалось получить список пар, жду 2 минуты...")
            time.sleep(120)
            continue
        bot_status["total_pairs"] = len(pairs)
        print(f"Найдено пар: {len(pairs)}")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        checked = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(check_pair, s): s for s in pairs}
            for future in as_completed(futures):
                result = future.result()
                checked += 1
                bot_status["pairs_checked"] = checked
                if result is None:
                    continue
                symbol, idx, price = result

                prev_upper = alerted_upper.get(symbol, 0)
                if idx >= UPPER_LEVEL and prev_upper < UPPER_LEVEL:
                    msg = (f"🔴 <b>ПЕРЕКУПЛЕННОСТЬ!</b>\n📊 <b>{symbol}</b>\n📈 Индекс: <b>{idx}%</b>\n💰 Цена: {price}\n⏱ 30m\n⚠️ Возможен разворот вниз (SHORT)")
                    send_telegram(msg)
                    print(f"🔴 ВЕРХНИЙ АЛЕРТ: {symbol} = {idx}%")
                alerted_upper[symbol] = idx

                prev_lower = alerted_lower.get(symbol, 100)
                if LOWER_MIN <= idx <= LOWER_MAX and prev_lower > LOWER_MAX:
                    msg = (f"🟢 <b>ПЕРЕПРОДАННОСТЬ!</b>\n📊 <b>{symbol}</b>\n📉 Индекс: <b>{idx}%</b>\n💰 Цена: {price}\n⏱ 30m\n⚠️ Возможен разворот вверх (LONG)")
                    send_telegram(msg)
                    print(f"🟢 НИЖНИЙ АЛЕРТ: {symbol} = {idx}%")
                alerted_lower[symbol] = idx

        elapsed = round(time.time() - start_time, 1)
        bot_status["last_check"] = datetime.now().strftime("%H:%M:%S")
        bot_status["cycles_done"] += 1
        print(f"✅ Завершено за {elapsed} сек. Жду {SLEEP_MIN} минут...\n")
        time.sleep(SLEEP_MIN * 60)

if __name__ == "__main__":
    main()
