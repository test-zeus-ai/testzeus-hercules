#!/usr/bin/env python3

import os
import json
import urllib.parse
import subprocess

# Read from environment variables or use default
LT_USERNAME = os.environ.get('LAMBDATEST_USERNAME', 'LAMBDATEST_USERNAME')
LT_ACCESS_KEY = os.environ.get('LAMBDATEST_ACCESS_KEY', 'LAMBDATEST_ACCESS_KEY')

capabilities = {
    'browserName': 'Chrome',  # Browsers allowed: Chrome, MicrosoftEdge, pw-chromium, pw-firefox, pw-webkit
    'browserVersion': 'latest',
    'LT:Options': {
        'platform': 'Windows 11',
        'build': 'Playwright Python Build',
        'name': 'Playwright Test',
        'user': LT_USERNAME,
        'accessKey': LT_ACCESS_KEY,
        'network': True,
        'video': True,
        'console': True,
        'tunnel': False,  # set to True if testing locally hosted pages
        # 'tunnelName': '',
        # 'geoLocation': '',  # e.g. 'US', 'FR', 'IN'
        'playwrightClientVersion': '1.latest'  # Will be updated dynamically
    }
}

def create_lt_cdp_url() -> str:
    # Dynamically get the local Playwright version
    playwright_version = subprocess.getoutput('playwright --version').strip().split()[1]
    # Update the capabilities to reflect the local Playwright version
    capabilities['LT:Options']['playwrightClientVersion'] = playwright_version

    # Construct the final CDP URL for LambdaTest
    raw_caps = json.dumps(capabilities)
    encoded_caps = urllib.parse.quote(raw_caps)
    lt_cdp_url = f"wss://cdp.lambdatest.com/playwright?capabilities={encoded_caps}"
    return lt_cdp_url

if __name__ == "__main__":
    print(create_lt_cdp_url())