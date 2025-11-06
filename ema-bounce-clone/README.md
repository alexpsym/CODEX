# EMA Bounce Alert

This script watches Bybit candlestick data and lets you know on Telegram when the closing price gets close to the Exponential Moving Average (EMA).

## Setup

1. Install Python 3.8 or later.
2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables so the program can send Telegram messages:
   - `TELEGRAM_TOKEN` – your Telegram bot token
   - `TELEGRAM_CHAT_ID` – the chat or channel ID where alerts should appear
   - `EMA_PERIOD` *(optional)* – how many prices to include when calculating the EMA. By default this is `20`. Set a different number if you want a shorter or longer EMA.
   - `TIMEFRAME` *(optional)* – candlestick interval to watch. Allowed values are `1`, `5`, `15`, `30`, `60`, `240`, `D`, `W`, and `M`. Defaults to `1` (1-minute candles).

## Running

Start the script with:
```bash
python ema-bounce.py
```
The program will reconnect automatically if the connection drops. Prices and EMA values will print to the console and an alert is sent when the price nears the EMA.

### Windows batch file

On Windows systems you can run `RUN.bat`. It sets the Telegram variables and launches the program for you.
Edit that file if you want to change the `EMA_PERIOD` or `TIMEFRAME` values on Windows.
