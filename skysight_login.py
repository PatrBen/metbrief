"""Open Skysight in a visible browser so you can log in yourself; the session is kept in a local profile.

    python skysight_login.py

This script neither reads nor stores credentials. It only waits until the map view has loaded
(or the window is closed) and then quits the browser. The session stays in skysight_profile/
(not versioned) and is used by skysight_shots.py.
"""
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

PROFILE = Path(__file__).resolve().parent / 'skysight_profile'
LOGIN_URL = 'https://skysight.io/login/'
WAIT_MINUTES = 10


def logged_in(page):
    """Logged in = no longer on the login page and a map canvas exists."""
    return 'login' not in urlsplit(page.url).path and page.locator('canvas').count() > 0


def main():
    ok = False
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(PROFILE), headless=False,
                                                        viewport={'width': 1280, 'height': 900})
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL)
        print(f'Please log in to Skysight in the browser window (waiting up to {WAIT_MINUTES} min) ...')
        deadline = time.time() + WAIT_MINUTES * 60
        try:
            while time.time() < deadline:
                if logged_in(page):
                    ok = True
                    break
                page.wait_for_timeout(2000)
            if ok:
                page.wait_for_timeout(5000)  # let cookies/storage be written
                print(f'Login detected. Profile saved in {PROFILE.name}/')
        except Exception as exc:  # window closed by hand
            print(f'Browser was closed ({type(exc).__name__}).')
        try:
            context.close()
        except Exception:
            pass
    if not ok:
        print('No login detected. Please run again.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
