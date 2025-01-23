#!/usr/bin/env python3

import os
import json
import urllib.parse
import subprocess

# Use environment variables if set, else fallback to default strings
BROWSERSTACK_USERNAME = os.environ.get('BROWSERSTACK_USERNAME', 'BROWSERSTACK_USERNAME')
BROWSERSTACK_ACCESS_KEY = os.environ.get('BROWSERSTACK_ACCESS_KEY', 'BROWSERSTACK_ACCESS_KEY')

desired_cap = {
    'os': 'osx',
    'os_version': 'catalina',
    'browser': 'chrome',  # allowed browsers: chrome, edge, playwright-chromium, playwright-firefox, playwright-webkit
    'browser_version': 'latest',  # valid only for `chrome` and `edge`
    'browserstack.username': BROWSERSTACK_USERNAME,
    'browserstack.accessKey': BROWSERSTACK_ACCESS_KEY,
    # 'browserstack.geoLocation': 'FR',
    'project': 'My First Project',
    'build': 'playwright-python-1',
    'name': 'My First Test',
    'buildTag': 'reg',
    'resolution': '1280x1024',
    'browserstack.local': 'false',
    'browserstack.localIdentifier': 'local_connection_name',
    'browserstack.playwrightVersion': '1.latest',
    'client.playwrightVersion': '1.latest',  # We will overwrite this with the installed version
    'browserstack.debug': 'true',
    'browserstack.console': 'info',
    'browserstack.networkLogs': 'true',
    'browserstack.interactiveDebugging': 'true'
}

def create_cdp_url() -> str:
    # Dynamically get the local Playwright version
    playwright_version = subprocess.getoutput('playwright --version').strip().split()[1]
    desired_cap['client.playwrightVersion'] = playwright_version
    # import ipdb; ipdb.set_trace()
    # Construct the final CDP URL
    raw_caps = json.dumps(desired_cap)
    encoded_caps = urllib.parse.quote(raw_caps)
    cdp_url = f"wss://cdp.browserstack.com/playwright?caps={encoded_caps}"
    return cdp_url

if __name__ == "__main__":
    print(create_cdp_url())
