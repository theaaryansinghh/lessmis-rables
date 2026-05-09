"""
step1_get_captcha.py
Logs in up to the captcha stage, saves captcha.png and enough session
state so step2 can resume without re-launching the browser.

Env vars required:
  JUIT_USERNAME
  JUIT_PASSWORD
"""

import json
import time
import base64
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

USERNAME   = os.environ["JUIT_USERNAME"]
PASSWORD   = os.environ["JUIT_PASSWORD"]
LOGIN_URL  = "https://webportal.juit.ac.in:6011/studentportal/#/"
BASE_URL   = "https://webportal.juit.ac.in:6011"
API_BASE   = f"{BASE_URL}/StudentPortalAPI"
CAPTCHA_FILE      = "captcha.png"
SESSION_STATE_FILE = "session_state.json"

# ── Chrome setup ────────────────────────────────────────────────────────────

options = webdriver.ChromeOptions()
options.add_argument("--headless")
options.add_argument("--window-size=1400,1000")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)
options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options,
)
driver.execute_cdp_cmd("Network.enable", {})
driver.execute_script(
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
)

wait = WebDriverWait(driver, 30)

print("Opening login page...")
driver.get(LOGIN_URL)
time.sleep(5)

# ── Grab captcha ────────────────────────────────────────────────────────────

captcha_saved = False
for attempt in range(40):
    time.sleep(0.5)
    logs = driver.get_log("performance")
    for log in logs:
        try:
            msg = json.loads(log["message"])["message"]
            if (
                msg["method"] == "Network.requestWillBeSent"
                and "getcaptcha" in msg["params"]["request"].get("url", "")
            ):
                request_id = msg["params"]["requestId"]
                time.sleep(1)
                try:
                    body = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id}
                    )
                    captcha_json = json.loads(body.get("body", "{}"))
                    captcha_b64 = (
                        captcha_json.get("response", {})
                        .get("captcha", {})
                        .get("image", "")
                    )
                    if captcha_b64:
                        with open(CAPTCHA_FILE, "wb") as f:
                            f.write(base64.b64decode(captcha_b64))
                        captcha_saved = True
                        print(f"Captcha saved to {CAPTCHA_FILE}")
                        break
                except Exception as e:
                    print(f"  captcha body error: {e}")
        except Exception:
            continue
    if captcha_saved:
        break

if not captcha_saved:
    driver.save_screenshot("step1_failure.png")
    driver.quit()
    raise RuntimeError("Could not capture captcha image")

# ── Fill username only (stop before password — step2 will finish login) ──────
# We save cookies + the username field so step2 can pick up the session

try:
    username_input = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, '//input[contains(@placeholder,"USER ID")]')
        )
    )
    username_input.clear()
    username_input.send_keys(USERNAME)
    print("Username entered.")
except Exception as e:
    print(f"Username field error: {e}")

# Save cookies so step2 can reuse the same browser session if needed
cookies = driver.get_cookies()

state = {
    "cookies": cookies,
    "username": USERNAME,
    "password": PASSWORD,   # stored in GitHub secret, fine to pass through
    "login_url": LOGIN_URL,
    "api_base": API_BASE,
    "base_url": BASE_URL,
}

with open(SESSION_STATE_FILE, "w") as f:
    json.dump(state, f, indent=2)

print(f"Session state saved to {SESSION_STATE_FILE}")
driver.quit()
print("Step 1 complete. Captcha image committed — check captcha.png in the repo.")
