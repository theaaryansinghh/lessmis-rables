"""
step2_scrape.py - Fixed version with longer waits and better error handling
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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
driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

wait = WebDriverWait(driver, 45)

def find_any(xpaths, timeout=45):
    per = max(5, timeout // len(xpaths))
    for xpath in xpaths:
        try:
            return WebDriverWait(driver, per).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
        except TimeoutException:
            continue
    return None

def click_any(xpaths, timeout=45):
    per = max(5, timeout // len(xpaths))
    for xpath in xpaths:
        try:
            el = WebDriverWait(driver, per).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].click();", el)
            return True
        except TimeoutException:
            continue
    return False

print("Opening login page...")
driver.get(LOGIN_URL)
time.sleep(7)
print(f"URL: {driver.current_url}")

# Username
username_input = find_any([
    '//input[contains(@placeholder,"USER ID")]',
    '//input[contains(@placeholder,"User ID")]',
    '//input[@type="text"][1]',
])
if not username_input:
    driver.save_screenshot("debug_username.png")
    driver.quit()
    raise RuntimeError("Could not find username field")
username_input.clear()
username_input.send_keys(USERNAME)
print(f"Username entered.")

# Captcha
captcha_input = find_any([
    '//input[contains(@placeholder,"shown in the image")]',
    '//input[contains(@placeholder,"Enter the text")]',
    '//input[contains(@placeholder,"captcha")]',
    '//input[contains(@placeholder,"Captcha")]',
    '//input[@type="text"][2]',
])
if not captcha_input:
    driver.save_screenshot("debug_captcha_field.png")
    driver.quit()
    raise RuntimeError("Could not find captcha field")
captcha_input.clear()
captcha_input.send_keys(CAPTCHA_TEXT)
print(f"Captcha entered: {CAPTCHA_TEXT}")
time.sleep(1)

# First LOGIN
print("Clicking first LOGIN...")
click_any([
    "//*[normalize-space(text())='LOGIN']",
    "//*[normalize-space(text())='Login']",
    "//button[contains(.,'LOGIN')]",
    "//button[@type='submit']",
])
print("Waiting for password field (up to 45s)...")
time.sleep(5)
driver.save_screenshot("debug_after_first_login.png")

# Password — longer wait
password_input = find_any([
    '//input[@type="password"]',
    '//input[contains(@placeholder,"Password")]',
    '//input[contains(@placeholder,"password")]',
], timeout=45)

if not password_input:
    try:
        errs = driver.find_elements(By.XPATH, '//*[contains(@class,"error") or contains(@class,"alert") or contains(@class,"snack")]')
        for e in errs:
            if e.text.strip():
                print(f"Page says: {e.text}")
    except Exception:
        pass
    driver.quit()
    raise RuntimeError(
        "Password field not found — captcha was probably wrong. "
        "Run Step 1 again for a fresh captcha."
    )

password_input.clear()
password_input.send_keys(PASSWORD)
print("Password entered.")
time.sleep(1)

# Second LOGIN
print("Clicking second LOGIN...")
click_any([
    "//*[normalize-space(text())='LOGIN']",
    "//*[normalize-space(text())='Login']",
    "//button[contains(.,'LOGIN')]",
    "//button[@type='submit']",
])
print("Waiting for dashboard (up to 15s)...")
time.sleep(12)
driver.save_screenshot("debug_after_second_login.png")
print(f"URL after login: {driver.current_url}")

if "dashbord" not in driver.current_url and "dashboard" not in driver.current_url.lower():
    driver.quit()
    raise RuntimeError(f"Login failed. Still at: {driver.current_url}")

print("Dashboard loaded!")

# Navigate to attendance for auth headers
try:
    el = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[contains(text(),"Class and Attendance")]')))
    driver.execute_script("arguments[0].click();", el)
    time.sleep(2)
except Exception:
    print("Class and Attendance link not found")

try:
    el = wait.until(EC.element_to_be_clickable((By.XPATH, '//a[@href="#/student/myclassattendance"]')))
    driver.execute_script("arguments[0].click();", el)
    time.sleep(2)
except Exception:
    print("My Attendance link not found")

try:
    sem_dd = wait.until(EC.element_to_be_clickable((By.XPATH, '//mat-select | //*[contains(@class,"mat-select-trigger")]')))
    driver.execute_script("arguments[0].click();", sem_dd)
    time.sleep(1)
    sem_opt = wait.until(EC.element_to_be_clickable((By.XPATH, f'//*[contains(text(),"{SEMESTER}")]')))
    driver.execute_script("arguments[0].click();", sem_opt)
    print(f"Semester {SEMESTER} selected.")
except Exception as e:
    print(f"Semester select error: {e}")

try:
    sub = wait.until(EC.element_to_be_clickable((By.XPATH, '//button[contains(.,"Submit")]')))
    driver.execute_script("arguments[0].click();", sub)
    print("Submit clicked.")
except Exception as e:
    print(f"Submit error: {e}")

time.sleep(6)

def get_network_logs():
    logs = driver.get_log("performance")
    out = []
    for log in logs:
        try:
            msg = json.loads(log["message"])["message"]
            if msg["method"] == "Network.requestWillBeSent":
                out.append(msg["params"]["request"])
        except Exception:
            continue
    return out

def find_request(logs, fragment):
    for req in reversed(logs):
        if fragment in req.get("url", ""):
            return req.get("headers", {}), req.get("postData", "")
    return None, None

logs = get_network_logs()
att_headers, att_payload   = find_request(logs, "getstudentattendancedetail")
_,           short_payload = find_request(logs, "getstudentbankinfo")
_,           fee_payload   = find_request(logs, "getmyactivefeeevents")

if not att_headers:
    driver.save_screenshot("debug_no_auth.png")
    driver.quit()
    raise RuntimeError("Could not capture auth headers.")

print("Auth headers captured.")
session = requests.Session()
for cookie in driver.get_cookies():
    session.cookies.set(cookie["name"], cookie["value"])
driver.quit()
print("Browser closed.")

base_headers = {
    "Accept":       "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer":      "https://webportal.juit.ac.in:6011/studentportal/",
    "User-Agent":   "Mozilla/5.0",
}
if att_headers.get("Authorization"):
    base_headers["Authorization"] = att_headers["Authorization"]
if att_headers.get("LocalName"):
    base_headers["LocalName"] = att_headers["LocalName"]

def post(path, payload, label):
    url = f"{API_BASE}/{path}"
    print(f"Fetching [{label}]...")
    try:
        resp = session.post(url, headers=base_headers, data=payload, verify=False, timeout=30)
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
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None

results = {}
results["attendance_detail"] = post("StudentClassAttendance/getstudentattendancedetail", att_payload, "attendance_detail")
results["attendance_registration_info"] = post("StudentClassAttendance/getstudentInforegistrationforattendence", json.dumps({"instituteid": "INID2201J000001"}), "attendance_registration_info")
results["bank_info"] = post("studentbankdetails/getstudentbankinfo", short_payload, "bank_info")
results["fee_active_events"] = post("onlinefeepayment/getmyactivefeeevents", fee_payload, "fee_active_events")
results["exam_result"] = post("ExamResult/getstudentresult", short_payload, "exam_result")
results["grade_card"] = post("ExamResult/getstudentgradecard", short_payload, "grade_card")
results["personal_info"] = post("studentpersonalinfo/getstudentpersonalinfo", short_payload, "personal_info")
results["subject_faculty"] = post("StudentClassAttendance/getmysubjectfaculty", short_payload, "subject_faculty")

meta = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "semester": SEMESTER,
    "endpoints_fetched": [k for k, v in results.items() if v is not None],
    "endpoints_failed":  [k for k, v in results.items() if v is None],
}
with open(os.path.join(OUTPUT_DIR, "meta.json"), "w") as f:
    json.dump(meta, f, indent=2)

print(f"\nDone. Fetched: {meta['endpoints_fetched']}")
print(f"Failed: {meta['endpoints_failed']}")
