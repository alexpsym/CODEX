# OANDA Weekend Close Script

This simple Python script closes all open positions on your OANDA account early on Saturday morning in Brisbane time. It prevents holding trades over the weekend.

## Usage

1. **Install Python dependencies**
   ```bash
   pip install requests
   ```

2. **Set environment variables** before running the script (replace the values with your actual account ID and API token). You can do this in the Command Prompt:
   ```cmd
   set OANDA_ACCOUNT_ID=YOUR_ACCOUNT_ID
   set OANDA_API_KEY=YOUR_API_TOKEN
   ```
   Optionally set `OANDA_URL` if you use the practice server:
   ```cmd
   set OANDA_URL=https://api-fxpractice.oanda.com/v3
   ```

3. **Run the script**
   ```bash
   python liquidate.py
   ```

Keep the script running. Every minute it checks the time. At 5 a.m. on Saturday morning (or 6 a.m. when the U.S. is not on daylight saving time) any open positions will be closed. Each closure is written to `trade_closure.log`.

## How it Works

- The script looks at the current time in Brisbane.
- It also checks if New York is on daylight saving time.
- On Saturday morning at the cut-off time it contacts the OANDA API to close every open position.
- Results of each attempt are appended to `trade_closure.log`.
