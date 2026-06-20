# ============================================
# Bybit Bot — Пик (93-98%) → Откат → Лонг (70-79%)
# + Информация о зонах ликвидаций
# ============================================

import requests
import pandas as pd
import numpy as np
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ⚠️ ВСТАВЬ СВОЙ ТОКЕН
TELEGRAM_TOKEN = "8288068435:AAEN8PGNxU4JS0oqNAOcQxpa5AmgvGJ78pQ"
TELEGRAM_ID    = "7495689566"

# Настройки зон
PEAK_MIN     = 93   # минимальный пик для фиксации
PEAK_MAX     = 98   # максимальный пик
PEAK_LOOKBACK = 10  # за сколько свечей искать пик (10 свечей = 5 часов)
ZONE_LOW     = 70   # нижняя граница зоны входа
ZONE_HIGH    = 79   # верхняя граница зоны входа
INTERVAL     = "30"
SLEEP_MIN    = 30
CANDLES      = 200
MAX_WORKERS  = 15
# Порог близости ликвидаций к цене (в %)
LIQ_PROXIMITY_PCT = 3.0

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
    params = {"category":"linear", "symbol":symbol, "interval":INTERVAL, "limit":CANDLES}
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

def get_liquidation_info(symbol, current_price):
    # Получаем данные об открытом интересе и тикере
    # для оценки зон вероятных ликвидаций
    try:
        url = "https://api.bybit.com/v5/market/tickers"
        r = requests.get(url, params={"category":"linear","symbol":symbol}, headers=HEADERS, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        if data.get("retCode") != 0: return None
        item = data["result"]["list"][0]

        high_24h = float(item.get("highPrice24h", 0))
        low_24h  = float(item.get("lowPrice24h", 0))
        oi       = float(item.get("openInterestValue", 0))
        funding  = float(item.get("fundingRate", 0))

        # Оцениваем зоны ликвидаций по High/Low 24ч
        # Лонг-ликвидации обычно скапливаются ниже Low 24ч
        # Шорт-ликвидации — выше High 24ч
        liq_info = []

        dist_to_high = abs(high_24h - current_price) / current_price * 100
        dist_to_low  = abs(current_price - low_24h) / current_price * 100

        if dist_to_high <= LIQ_PROXIMITY_PCT:
            liq_info.append(f"⚠️ Шорт-ликвидации близко сверху (High 24ч: {high_24h}, расст. {round(dist_to_high,1)}%)")
        else:
            liq_info.append(f"✅ Шорт-ликвидации далеко (High 24ч: {high_24h}, расст. {round(dist_to_high,1)}%)")

        if dist_to_low <= LIQ_PROXIMITY_PCT:
            liq_info.append(f"⚠️ Лонг-ликвидации близко снизу (Low 24ч: {low_24h}, расст. {round(dist_to_low,1)}%)")
        else:
            liq_info.append(f"✅ Лонг-ликвидации далеко (Low 24ч: {low_24h}, расст. {round(dist_to_low,1)}%)")

        oi_str = f"${round(oi/1_000_000,1)}M" if oi > 1_000_000 else f"${round(oi/1000)}K"
        liq_info.append(f"📊 Открытый интерес: {oi_str}")

        funding_pct = round(funding * 100, 4)
        if funding > 0.0005:
            liq_info.append(f"📡 Funding: {funding_pct}% (🔴 Лонги перегружены — риск слива)")
        elif funding < -0.0005:
            liq_info.append(f"📡 Funding: {funding_pct}% (🟢 Шорты перегружены — поддержка для лонга)")
        else:
            liq_info.append(f"📡 Funding: {funding_pct}% (⚪ Нейтрален)")

        return "\n".join(liq_info)
    except:
        return None

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
        return index
    except: return None

def check_pair(symbol):
    df = get_candles(symbol)
    if df is None or len(df) < 150: return None
    index_series = calc_index_series(df)
    if index_series is None or len(index_series) < PEAK_LOOKBACK + 2: return None

    idx_current = round(index_series.iloc[-1], 2)
    price = df["close"].iloc[-1]

    # Ищем пик 93-98% за последние PEAK_LOOKBACK свечей (исключая текущую)
    recent_values = index_series.iloc[-(PEAK_LOOKBACK+1):-1]
    peak_mask = (recent_values >= PEAK_MIN) & (recent_values <= PEAK_MAX)

    if not peak_mask.any():
        return None  # не было пика — сигнала нет

    # Находим значение и позицию пика
    peak_value = recent_values[peak_mask].max()
    peak_pos = list(recent_values.values).index(recent_values[peak_mask].max())
    candles_ago = PEAK_LOOKBACK - peak_pos

    # Проверяем что СЕЙЧАС индекс в зоне 70-79% (откат произошёл)
    in_zone = ZONE_LOW <= idx_current <= ZONE_HIGH

    if not in_zone:
        return None

    # Проверяем что текущее значение НИЖЕ пика (реальный откат)
    if idx_current >= peak_value:
        return None

    return {
        "symbol": symbol,
        "idx_current": idx_current,
        "peak_value": round(peak_value, 2),
        "candles_ago": candles_ago,
        "price": price
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
                        f"🎯 Сигналов отправлено: {bot_status['signals_sent']}\n"
                        f"🕐 Последняя проверка: {bot_status['last_check'] or 'ещё не было'}\n"
                        f"📈 Пик: {PEAK_MIN}-{PEAK_MAX}% → Откат → Зона: {ZONE_LOW}-{ZONE_HIGH}%"
                    )
        except: time.sleep(5)

def main():
    threading.Thread(target=listen_commands, daemon=True).start()
    send_telegram(
        f"🤖 Бот запущен! (Стратегия: Пик → Откат → Лонг)\n"
        f"📈 Ищу пик {PEAK_MIN}-{PEAK_MAX}% за последние {PEAK_LOOKBACK} свечей\n"
        f"🟢 Сигнал когда откат в зону {ZONE_LOW}-{ZONE_HIGH}%\n"
        f"💥 + анализ зон ликвидаций\n\n"
        f"Напиши /status для проверки"
    )

    alerted = {}  # symbol → последнее значение при сигнале

    while True:
        now = datetime.now().strftime("%H:%M")
        start = time.time()
        print(f"[{now}] Проверяю пары...")

        pairs = get_all_pairs()
        if not pairs:
            time.sleep(120); continue
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

                symbol     = result["symbol"]
                idx_curr   = result["idx_current"]
                peak_val   = result["peak_value"]
                candles_ago = result["candles_ago"]
                price      = result["price"]

                # Защита от дублей: не слать если уже был сигнал и индекс не выходил из зоны
                prev = alerted.get(symbol, 100)
                if prev <= ZONE_HIGH:
                    continue  # уже были в зоне, ждём выхода

                # Получаем информацию о ликвидациях
                liq_info = get_liquidation_info(symbol, price)
                liq_str = liq_info if liq_info else "Данные недоступны"

                time_ago = candles_ago * 30  # в минутах
                time_str = f"{time_ago} мин назад" if time_ago < 60 else f"{round(time_ago/60,1)} ч назад"

                msg = (
                    f"🟢 <b>СИГНАЛ НА ЛОНГ!</b>\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"📈 Пик был: <b>{peak_val}%</b> ({time_str})\n"
                    f"📉 Текущий индекс: <b>{idx_curr}%</b> (в зоне {ZONE_LOW}-{ZONE_HIGH}%)\n"
                    f"💰 Цена входа: <b>{price}</b>\n"
                    f"⏱ Таймфрейм: 30m\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💥 <b>Зона ликвидаций:</b>\n{liq_str}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🎯 Логика: откат от перекупленности в зону поддержки"
                )
                send_telegram(msg)
                bot_status["signals_sent"] += 1
                alerted[symbol] = idx_curr
                print(f"🟢 СИГНАЛ: {symbol} пик={peak_val}% текущий={idx_curr}%")

        # Сброс алертов для пар которые вышли из зоны
        for s in list(alerted.keys()):
            if alerted[s] <= ZONE_HIGH:
                pass  # оставляем пока в зоне

        elapsed = round(time.time() - start, 1)
        bot_status["last_check"] = datetime.now().strftime("%H:%M:%S")
        bot_status["cycles_done"] += 1
        print(f"✅ Завершено за {elapsed} сек. Жду {SLEEP_MIN} минут...\n")
        time.sleep(SLEEP_MIN * 60)

if __name__ == "__main__":
    main()
