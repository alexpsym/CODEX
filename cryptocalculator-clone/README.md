# Crypto Calculator

This tool calculates trade information for Bybit.

## Usage

1. Set your Bybit API credentials as environment variables:

```bash
export BYBIT_API_KEY=your_api_key
export BYBIT_API_SECRET=your_secret
```

2. **Web interface** – install Flask and start the browser interface:

```bash
pip install flask
python cryptocalculator_web.py
```

Your Microsoft Edge browser will open to `http://localhost:5000/` using the same dark theme and layout as the OANDA calculator. Fill in the form and press **Calculate** to see the results. After the summary appears, use the **Download Summary** button to save it as `trade_summary.txt`.

3. *(Optional)* **CLI** – edit `config.json` for your trade parameters and run `python cryptocalculator.py` to generate summary files on the command line.

