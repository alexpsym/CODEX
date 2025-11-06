import os
import json
import pandas as pd
import time
from datetime import datetime
import requests
import asyncio
import logging
import websockets

# === CONFIG ===
SYMBOL = "BTCUSDT"
WS_URL = "wss://stream.bybit.com/v5/public/linear"
EMA_PERIOD = int(os.getenv("EMA_PERIOD", "20"))
BUFFER_PERCENT = 0.00075  # 0.075% buffer from EMA
COOLDOWN_SECONDS = 10  # Seconds between alerts
# Which candle interval to use for EMA calculations
TIMEFRAME = os.getenv("TIMEFRAME", "1")
ALLOWED_TIMEFRAMES = {"1", "5", "15", "30", "60", "240", "D", "W", "M"}
if TIMEFRAME not in ALLOWED_TIMEFRAMES:
    raise ValueError(
        f"Invalid TIMEFRAME '{TIMEFRAME}'. Choose from: {', '.join(sorted(ALLOWED_TIMEFRAMES))}"
    )

# === TELEGRAM SETTINGS ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_alert(message: str) -> None:
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials not set; skipping alert")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as exc:
        logging.error("Telegram send failed: %s", exc)


ROLLING_CLOSES = []
last_price = None
last_alert_time = 0.0


def compute_ema(close_prices, span: int = EMA_PERIOD):
    if len(close_prices) < span:
        return None
    series = pd.Series(close_prices)
    return series.ewm(span=span, adjust=False).mean().iloc[-1]


def should_alert(price, ema, previous_price, buffer_pct, last_alert_ts):
    if previous_price is None:
        return False
    now = time.time()
    if now - last_alert_ts < COOLDOWN_SECONDS:
        return False
    buffer = ema * buffer_pct
    if previous_price > ema and ema < price < (ema + buffer):
        return True
    if previous_price < ema and ema > price > (ema - buffer):
        return True
    return False


async def handle_message(message: str) -> None:
    global ROLLING_CLOSES, last_price, last_alert_time
    try:
        data = json.loads(message)
        if data.get("topic") != f"kline.{TIMEFRAME}.{SYMBOL}":
            return
        kline_data = data.get("data")
        if not kline_data:
            return
        kline = kline_data[0] if isinstance(kline_data, list) else kline_data
        price = float(kline.get("c") or kline.get("close"))
        ROLLING_CLOSES.append(price)
        if len(ROLLING_CLOSES) > EMA_PERIOD:
            ROLLING_CLOSES.pop(0)
        ema = compute_ema(ROLLING_CLOSES, span=EMA_PERIOD)
        if ema is None:
            return
        now_str = datetime.now().strftime("%H:%M:%S")
        logging.info("[%s] Price: %.2f | EMA: %.2f", now_str, price, ema)
        if should_alert(price, ema, last_price, BUFFER_PERCENT, last_alert_time):
            direction = "above" if last_price > ema else "below"
            buffer_val = ema * BUFFER_PERCENT
            alert_msg = (
                "\U0001F514 EMA Bounce Alert\n"
                f"Symbol: {SYMBOL}\n"
                f"Live Price: {price:.2f}\n"
                f"EMA ({EMA_PERIOD}): {ema:.2f}\n"
                f"Direction: approaching from {direction}\n"
                f"Buffer: {buffer_val:.2f} ({BUFFER_PERCENT*100:.3f}%)"
            )
            logging.info(alert_msg)
            send_telegram_alert(alert_msg)
            last_alert_time = time.time()
        last_price = price
    except Exception as exc:
        logging.error("Error handling message: %s", exc)


async def run_websocket() -> None:
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(
                    json.dumps({"op": "subscribe", "args": [f"kline.{TIMEFRAME}.{SYMBOL}"]})
                )
                logging.info("WebSocket connected")
                async for message in ws:
                    await handle_message(message)
        except Exception as exc:
            logging.error("WebSocket error: %s", exc)
            logging.info("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run_websocket())
