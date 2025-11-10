# Crypto Calculator

This tool calculates trade information for Bybit and CoinSpot using a pluggable exchange adapter layer.

## Usage

1. **Configure your exchange**

   Set the `exchange` field in `config.json` to either `"bybit"` (the default) or `"coinspot"` and provide the required credentials:

   - **Bybit** – export the API keys used for balance lookups:

     ```bash
     export BYBIT_API_KEY=your_api_key
     export BYBIT_API_SECRET=your_secret
     ```

   - **CoinSpot** – install the [`coinspot`](https://pypi.org/project/coinspot/) package and export your read-only API credentials:

     ```bash
     pip install coinspot
     export COINSPOT_API_KEY=your_api_key
     export COINSPOT_API_SECRET=your_secret
     ```

     CoinSpot does not publish tick/lot sizes via the API, so add `base_asset`, `quote_asset`, `tick_size`, `qty_step`, and `min_qty` entries to `config.json` when using `"coinspot"`.

2. **Web interface** – install Flask and start the browser interface:

   ```bash
   pip install flask
   python cryptocalculator_web.py
   ```

   Your Microsoft Edge browser will open to `http://localhost:5000/` using the same dark theme and layout as the OANDA calculator. Fill in the form and press **Calculate** to see the results. After the summary appears, use the **Download Summary** button to save it as `trade_summary.txt`.

3. *(Optional)* **CLI** – edit `config.json` for your trade parameters (including the `exchange` and any CoinSpot-specific overrides) and run `python cryptocalculator.py` to generate summary files on the command line.
