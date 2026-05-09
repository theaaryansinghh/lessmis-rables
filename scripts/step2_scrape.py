"""
step2_scrape.py
Uses the captcha text (passed as CAPTCHA_TEXT env var) to complete login,
then scrapes attendance, bank info, registration info and saves to juit_data/.

Env vars required:
  JUIT_USERNAME
  JUIT_PASSWORD
  CAPTCHA_TEXT
"""

import json
import time
import os
import requests
import urllib3
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USERNAME     = os.environ["JUIT_USERNAME"]
PASSWORD     = os.environ["JUIT_PASSWORD"]
CAPTCHA_TEXT = os.environ["CAPTCHA_TEXT"].strip()
SEMESTER     = "2026EVESEM"

LOGIN_URL = "https://webportal.juit.ac.in:6011/studentportal/#/"
BASE_URL  = "https://webportal.juit.ac.in:6011"
API_BASE  = f"{BASE_URL}/StudentPortalAPI"

OUTPUT_DIR = "juit_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"Starting Step 2 with captcha: '{CAPTCHA_TEXT}'")

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

# ── Full login flow ──────────────────────────────────────────────────────────

print("Opening login page...")
driver.get(LOGIN_URL)
time.sleep(5)

# Username
username_input = wait.until(
    EC.presence_of_element_located(
        (By.XPATH, '//input[contains(@placeholder,"USER ID")]')
    )
)
username_input.clear()
username_input.send_keys(USERNAME)

# Captcha
captcha_input = driver.find_element(
    By.XPATH,
    '//input[contains(@placeholder,"shown in the image") or contains(@placeholder,"Enter the text")]',
)
captcha_input.clear()
captcha_input.send_keys(CAPTCHA_TEXT)

# First LOGIN click
login_btn = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//*[normalize-space(text())='LOGIN' or normalize-space(text())='Login']")
    )
)
driver.execute_script("arguments[0].click();", login_btn)
print("First LOGIN clicked.")

# Password
password_input = wait.until(
    EC.presence_of_element_located(
        (By.XPATH, '//input[@type="password" or contains(@placeholder,"Password")]')
    )
)
password_input.clear()
password_input.send_keys(PASSWORD)

# Second LOGIN click
login_btn = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//*[normalize-space(text())='LOGIN' or normalize-space(text())='Login']")
    )
)
driver.execute_script("arguments[0].click();", login_btn)
print("Second LOGIN clicked.")

time.sleep(8)

if "dashbord" not in driver.current_url:
    driver.save_screenshot("step2_login_failure.png")
    driver.quit()
    raise RuntimeError(
        f"Login failed. Current URL: {driver.current_url}. "
        "Check captcha text or credentials."
    )

print("Dashboard loaded.")

# ── Navigate to attendance to capture auth headers ───────────────────────────

try:
    class_att_link = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//*[contains(text(),"Class and Attendance")]')
        )
    )
    driver.execute_script("arguments[0].click();", class_att_link)
    time.sleep(2)
except Exception:
    print("Could not click Class and Attendance")

try:
    my_att_link = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//a[@href="#/student/myclassattendance"]')
        )
    )
    driver.execute_script("arguments[0].click();", my_att_link)
    time.sleep(2)
except Exception:
    print("Could not click My Attendance link")

try:
    sem_dropdown = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//mat-select | //*[contains(@class,"mat-select-trigger")]')
        )
    )
    driver.execute_script("arguments[0].click();", sem_dropdown)
    time.sleep(1)
    sem_option = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, f'//*[contains(text(),"{SEMESTER}")]')
        )
    )
    driver.execute_script("arguments[0].click();", sem_option)
    print(f"Semester {SEMESTER} selected.")
except Exception as e:
    print(f"Semester dropdown error: {e}")

try:
    submit_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, '//button[contains(.,"Submit")]'))
    )
    driver.execute_script("arguments[0].click();", submit_btn)
    print("Submit clicked.")
except Exception as e:
    print(f"Submit error: {e}")

time.sleep(6)

# ── Capture network logs ─────────────────────────────────────────────────────

def get_network_logs():
    logs = driver.get_log("performance")
    results = []
    for log in logs:
        try:
            msg = json.loads(log["message"])["message"]
            if msg["method"] == "Network.requestWillBeSent":
                results.append(msg["params"]["request"])
        except Exception:
            continue
    return results


def find_request(logs, fragment):
    for req in reversed(logs):
        if fragment in req.get("url", ""):
            return req.get("headers", {}), req.get("postData", "")
    return None, None


logs = get_network_logs()

att_headers, att_payload     = find_request(logs, "getstudentattendancedetail")
_,           short_payload   = find_request(logs, "getstudentbankinfo")
_,           fee_payload     = find_request(logs, "getmyactivefeeevents")

if not att_headers:
    driver.save_screenshot("step2_capture_failure.png")
    driver.quit()
    raise RuntimeError("Could not capture auth headers from network logs.")

print("Auth headers captured.")

# Build requests session
session = requests.Session()
for cookie in driver.get_cookies():
    session.cookies.set(cookie["name"], cookie["value"])

driver.quit()
print("Browser closed.")

# ── HTTP helpers ─────────────────────────────────────────────────────────────

base_headers = {
    "Accept":        "application/json, text/plain, */*",
    "Content-Type":  "application/json",
    "Referer":       "https://webportal.juit.ac.in:6011/studentportal/",
    "User-Agent":    "Mozilla/5.0",
}
if att_headers.get("Authorization"):
    base_headers["Authorization"] = att_headers["Authorization"]
if att_headers.get("LocalName"):
    base_headers["LocalName"] = att_headers["LocalName"]


def post(path, payload, label):
    url = f"{API_BASE}/{path}"
    print(f"Fetching [{label}]...")
    try:
        resp = session.post(
            url, headers=base_headers, data=payload, verify=False, timeout=30
        )
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                out = os.path.join(OUTPUT_DIR, f"{label}.json")
                with open(out, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"  Saved {out}")
                return data
            except Exception:
                print(f"  Non-JSON response")
                return None
        else:
            print(f"  HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


# ── Scrape endpoints ─────────────────────────────────────────────────────────

results = {}

results["attendance_detail"] = post(
    "StudentClassAttendance/getstudentattendancedetail",
    att_payload,
    "attendance_detail",
)

results["attendance_registration_info"] = post(
    "StudentClassAttendance/getstudentInforegistrationforattendence",
    json.dumps({"instituteid": "INID2201J000001"}),
    "attendance_registration_info",
)

results["bank_info"] = post(
    "studentbankdetails/getstudentbankinfo",
    short_payload,
    "bank_info",
)

results["fee_active_events"] = post(
    "onlinefeepayment/getmyactivefeeevents",
    fee_payload,
    "fee_active_events",
)

results["exam_result"] = post(
    "ExamResult/getstudentresult",
    short_payload,
    "exam_result",
)

results["grade_card"] = post(
    "ExamResult/getstudentgradecard",
    short_payload,
    "grade_card",
)

results["personal_info"] = post(
    "studentpersonalinfo/getstudentpersonalinfo",
    short_payload,
    "personal_info",
)

results["subject_faculty"] = post(
    "StudentClassAttendance/getmysubjectfaculty",
    short_payload,
    "subject_faculty",
)

# ── Write metadata (last updated timestamp) ──────────────────────────────────

meta = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "semester": SEMESTER,
    "endpoints_fetched": [k for k, v in results.items() if v is not None],
    "endpoints_failed":  [k for k, v in results.items() if v is None],
}
with open(os.path.join(OUTPUT_DIR, "meta.json"), "w") as f:
    json.dump(meta, f, indent=2)

print("\nAll done.")
print(f"Fetched: {meta['endpoints_fetched']}")
print(f"Failed:  {meta['endpoints_failed']}")
