# OANDA Position Size CLI

This repository contains a small command line tool for calculating the number of units to trade with OANDA.

## Requirements
- Python 3
- The `requests` package (`pip install requests`)
- The `python-dotenv` package if you want credentials loaded automatically
  from a local `oanda.env` file (`pip install python-dotenv`)
- Environment variables `OANDA_API_KEY` and `OANDA_ACCOUNT_ID` set to your API token and account ID.
- Optionally, `OANDA_BASE_URL` can override the API base URL (defaults to the live trading endpoint).

## Usage
Run the tool with the instrument, trade side, stop loss (in pips), risk percentage and optional risk–reward ratio:

```bash
OANDA_API_KEY=XXX OANDA_ACCOUNT_ID=YYY \
python oanda_calculator_cli.py EUR_USD buy 10 1 2
```

The script will print a JSON order ready to send to the OANDA Orders API.

## Web Interface
You can also run a simple web version using Flask:

```bash
pip install flask requests waitress
python oanda_calculator_web.py
```

If an `oanda.env` file exists in the same directory, the app automatically
loads it and reads `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, and optional
`OANDA_BASE_URL` values (for example, switching to the practice API at
`https://api-fxpractice.oanda.com/v3`). To store the env file elsewhere, set
`OANDA_ENV_FILE` to its full path (e.g. `OANDA_ENV_FILE=E:\ENV\oanda.env` on
Windows). You can still export the variables in your shell instead if you
prefer.

### Where to place your account ID

Set your account number in `OANDA_ACCOUNT_ID` inside `oanda.env` (or in the
file pointed to by `OANDA_ENV_FILE`). OANDA shows this number—typically in the
format `001-001-1234567-001`—in the trading dashboard under **Account Summary**.
If the calculator cannot find a real account ID (for example, if the placeholder
value is left untouched), it raises a clear error instead of making an API call
that fails with `invalid value specified for 'accountID'`.

The launcher requires Microsoft Edge to be installed and available to Python's
`webbrowser` module; it raises an error if Edge cannot be started. When you run
`oanda_calculator_web.py`, the app is served by the Waitress WSGI server on
`http://127.0.0.1:5000/` and Edge is opened to that address automatically. Type the
instrument name directly in the form—case and punctuation do not matter. The
calculator automatically reformats entries such as `eurusd` into the exact
symbol (`EUR_USD`) that the OANDA API expects before performing any
calculations. Choose the trade side, stop loss, risk percentage and risk–reward
ratio, then submit the form to see the resulting order as JSON.
You can download the details of your most recently calculated trade using the
**Download Specs** button on the web page.

### Production-style serving

For deployments beyond local testing, run the Flask app behind a WSGI server
such as Gunicorn:

```bash
pip install gunicorn flask requests
gunicorn --bind 0.0.0.0:8000 wsgi:app
```

The `wsgi:app` target simply re-exports the Flask application object from
`oanda_calculator_web.py`, making it easy to plug into any WSGI-compatible
server stack.
