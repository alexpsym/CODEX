import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID')
API_KEY = os.getenv('OANDA_API_KEY')
BASE_URL = os.getenv('OANDA_URL', 'https://api-fxtrade.oanda.com/v3')

if not ACCOUNT_ID or not API_KEY:
    raise SystemExit('Set OANDA_ACCOUNT_ID and OANDA_API_KEY environment variables.')

HEADERS = {
    'Authorization': f'Bearer {API_KEY}',
    'Content-Type': 'application/json'
}

LOG_FILE = 'trade_closure.log'


def log(msg: str) -> None:
    timestamp = datetime.now().isoformat()
    with open(LOG_FILE, 'a') as f:
        f.write(f'{timestamp} - {msg}\n')


def is_us_dst() -> bool:
    ny = datetime.now(ZoneInfo('America/New_York'))
    return bool(ny.dst())


def close_all_positions() -> None:
    url = f'{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions'
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        log(f'Failed to fetch positions: {resp.text}')
        return
    data = resp.json()
    positions = data.get('positions', [])
    if not positions:
        return
    for pos in positions:
        instrument = pos['instrument']
        close_url = f'{BASE_URL}/accounts/{ACCOUNT_ID}/positions/{instrument}/close'
        payload = {}
        if float(pos['long']['units']) != 0:
            payload['longUnits'] = 'ALL'
        if float(pos['short']['units']) != 0:
            payload['shortUnits'] = 'ALL'
        c_resp = requests.put(close_url, headers=HEADERS, json=payload)
        if c_resp.status_code in (200, 201):
            log(f'Closed {instrument}: {c_resp.text}')
        else:
            log(f'Failed to close {instrument}: {c_resp.text}')


def main() -> None:
    last_week = None
    while True:
        brisbane = datetime.now(ZoneInfo('Australia/Brisbane'))
        cutoff = 5 if is_us_dst() else 6
        if brisbane.weekday() == 5 and brisbane.hour >= cutoff:
            current_week = brisbane.isocalendar()[1]
            if last_week != current_week:
                close_all_positions()
                last_week = current_week
        time.sleep(60)


if __name__ == '__main__':
    main()
