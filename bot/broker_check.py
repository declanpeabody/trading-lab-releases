"""Optional Trading 212 DEMO authentication check. GET requests only."""
import base64
import json
import os
from urllib.request import Request, urlopen


def main():
    key, secret = os.getenv('T212_API_KEY'), os.getenv('T212_API_SECRET')
    if not key or not secret:
        raise SystemExit('Set the demo API key and secret privately in environment variables.')
    auth = base64.b64encode(f'{key}:{secret}'.encode()).decode()
    request = Request('https://demo.trading212.com/api/v0/equity/account/summary',
                      headers={'Authorization': 'Basic ' + auth})
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
        currency = data['currency']
        if not isinstance(currency, str) or len(currency) != 3:
            raise ValueError('Unexpected account currency')
    except Exception:
        raise SystemExit('Demo connection check failed. Check permissions, key and network; credentials were not logged.') from None
    print('Demo read-only authentication works. Primary currency:', currency)
    print('No real-money connection or order execution was attempted.')


if __name__ == '__main__':
    main()
