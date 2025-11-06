# CoinSpot History

This project includes a small script for downloading your transaction history from CoinSpot.

First, make sure the required ``coinspot`` package is installed. You can do this with ``pip``:

```bash
pip install coinspot
```

Before running `coinspot_history.py`, you need to provide your API credentials as environment variables.

1. Find your CoinSpot API key and secret in your CoinSpot account settings.
2. Set them as environment variables:

   **On macOS/Linux:**
   ```bash
   export COINSPOT_API_KEY=your_key_here
   export COINSPOT_API_SECRET=your_secret_here
   ```

   **On Windows Command Prompt:**
   ```cmd
   set COINSPOT_API_KEY=your_key_here
   set COINSPOT_API_SECRET=your_secret_here
   ```

   **On Windows PowerShell:**
   ```powershell
   $env:COINSPOT_API_KEY="your_key_here"
   $env:COINSPOT_API_SECRET="your_secret_here"
   ```

3. Run the script:

```bash
python coinspot_history.py
```

The program will print your history in JSON format. If either variable is missing, the script will stop and let you know.

If you're on Windows and don't want to type the commands yourself, just double-click `RUN.bat`. It sets `COINSPOT_API_KEY` and `COINSPOT_API_SECRET` for you before launching the script.
