"""
scrape.py — single-session scraper with mid-run captcha polling.

Flow:
  1. Open browser, load login page, capture captcha.png, commit it.
  2. Poll GitHub repo variable CAPTCHA_ANSWER every 10s (up to 5 min).
  3. Once you set CAPTCHA_ANSWER in repo Settings → Variables → Actions,
     script reads it, clears it, fills captcha, completes login, scrapes.

Env vars (set as GitHub Secrets):
  JUIT_USERNAME
  JUIT_PASSWORD
  GH_TOKEN        — a fine-grained PAT with "Variables" read+write on this repo
  GH_REPO         — e.g. "theaaryansinghh/lessmis-rables"
"""

import json, time, base64, os, subprocess, requests, urllib3
from datetime import datetime, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USERNAME  = os.environ["JUIT_USERNAME"]
PASSWORD  = os.environ["JUIT_PASSWORD"]
GH_TOKEN  = os.environ["GH_TOKEN"]
GH_REPO   = os.environ["GH_REPO"]          # "owner/repo"
SEMESTER  = "2026EVESEM"
LOGIN_URL = "https://webportal.juit.ac.in:6011/studentportal/#/"
BASE_URL  = "https://webportal.juit.ac.in:6011"
API_BASE  = f"{BASE_URL}/StudentPortalAPI"
OUTPUT_DIR = "juit_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
VAR_NAME = "CAPTCHA_ANSWER"

# ── GitHub variable helpers ──────────────────────────────────────────────────

def get_captcha_answer():
    """Return current value of CAPTCHA_ANSWER variable, or None."""
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables/{VAR_NAME}"
    r = requests.get(url, headers=GH_HEADERS, timeout=10)
    if r.status_code == 200:
        val = r.json().get("value", "").strip()
        return val if val and val != "WAITING" else None
    return None

def clear_captcha_answer():
    """Reset CAPTCHA_ANSWER back to WAITING so it's ready for next run."""
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables/{VAR_NAME}"
    # Try PATCH (update existing)
    r = requests.patch(url, headers=GH_HEADERS, timeout=10,
                       json={"name": VAR_NAME, "value": "WAITING"})
    if r.status_code not in (200, 201, 204):
        # Variable might not exist yet — create it
        create_url = f"https://api.github.com/repos/{GH_REPO}/actions/variables"
        requests.post(create_url, headers=GH_HEADERS, timeout=10,
                      json={"name": VAR_NAME, "value": "WAITING"})
    print(f"CAPTCHA_ANSWER reset to WAITING.")

def commit_captcha(path="captcha.png"):
    """Commit captcha.png to repo so you can read it in the GitHub app."""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", path], check=True)
    result = subprocess.run(["git", "commit", "-m", "chore: update captcha [skip ci]"],
                             capture_output=True, text=True)
    if result.returncode == 0:
        subprocess.run(["git", "push"], check=True)
        print("captcha.png committed and pushed.")
    else:
        print("Nothing new to commit for captcha.")

# ── Chrome ───────────────────────────────────────────────────────────────────

options = webdriver.ChromeOptions()
options.add_argument("--headless")
options.add_argument("--window-size=1400,1000")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)
options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
driver.execute_cdp_cmd("Network.enable", {})
driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
wait = WebDriverWait(driver, 45)

# ── Step 1: Load page and capture captcha ────────────────────────────────────

print("Opening login page...")
driver.get(LOGIN_URL)
time.sleep(6)

# Grab captcha image from network logs
captcha_saved = False
for _ in range(40):
    time.sleep(0.5)
    for log in driver.get_log("performance"):
        try:
            msg = json.loads(log["message"])["message"]
            if (msg["method"] == "Network.requestWillBeSent"
                    and "getcaptcha" in msg["params"]["request"].get("url", "")):
                rid = msg["params"]["requestId"]
                time.sleep(1)
                try:
                    body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
                    b64 = (json.loads(body.get("body", "{}"))
                           .get("response", {}).get("captcha", {}).get("image", ""))
                    if b64:
                        with open("captcha.png", "wb") as f:
                            f.write(base64.b64decode(b64))
                        captcha_saved = True
                        print("captcha.png saved.")
                        break
                except Exception as e:
                    print(f"  captcha body error: {e}")
        except Exception:
            continue
    if captcha_saved:
        break

if not captcha_saved:
    driver.save_screenshot("debug_no_captcha.png")
    driver.quit()
    raise RuntimeError("Could not capture captcha image.")

# Fill username now (before pausing — keeps session warm)
try:
    u = wait.until(EC.presence_of_element_located(
        (By.XPATH, '//input[contains(@placeholder,"USER ID") or contains(@placeholder,"User ID")]')))
    u.clear()
    u.send_keys(USERNAME)
    print("Username prefilled.")
except Exception as e:
    print(f"Username field error: {e}")

# Commit captcha so you can read it in the GitHub app
commit_captcha("captcha.png")

# ── Step 2: Poll for CAPTCHA_ANSWER ─────────────────────────────────────────

print("\nWaiting for you to set CAPTCHA_ANSWER in repo Settings → Variables → Actions...")
print("You have 5 minutes.\n")

captcha_answer = None
for i in range(30):   # 30 x 10s = 5 minutes
    time.sleep(10)
    answer = get_captcha_answer()
    if answer:
        captcha_answer = answer
        print(f"Got captcha answer: '{captcha_answer}'")
        break
    print(f"  [{(i+1)*10}s] Still waiting for CAPTCHA_ANSWER...")

if not captcha_answer:
    driver.quit()
    raise RuntimeError("Timed out waiting for CAPTCHA_ANSWER. Run the workflow again.")

clear_captcha_answer()

# ── Step 3: Complete login in the same browser session ───────────────────────

# Fill captcha
try:
    c = wait.until(EC.presence_of_element_located((By.XPATH,
        '//input[contains(@placeholder,"shown in the image") or '
        'contains(@placeholder,"Enter the text") or '
        'contains(@placeholder,"captcha") or '
        'contains(@placeholder,"Captcha")]')))
    c.clear()
    c.send_keys(captcha_answer)
    print("Captcha filled.")
except Exception as e:
    print(f"Captcha field error: {e}")

time.sleep(1)

# First LOGIN
try:
    btn = wait.until(EC.element_to_be_clickable((By.XPATH,
        "//*[normalize-space(text())='LOGIN' or normalize-space(text())='Login']")))
    driver.execute_script("arguments[0].click();", btn)
    print("First LOGIN clicked.")
except Exception as e:
    print(f"First LOGIN error: {e}")

# Wait for password field
print("Waiting for password field...")
time.sleep(5)

try:
    p = WebDriverWait(driver, 45).until(EC.presence_of_element_located((By.XPATH,
        '//input[@type="password" or contains(@placeholder,"Password") or contains(@placeholder,"password")]')))
    p.clear()
    p.send_keys(PASSWORD)
    print("Password entered.")
except TimeoutException:
    driver.save_screenshot("debug_no_password.png")
    driver.quit()
    raise RuntimeError("Password field not found — captcha was likely wrong. Try again.")

time.sleep(1)

# Second LOGIN
try:
    btn = wait.until(EC.element_to_be_clickable((By.XPATH,
        "//*[normalize-space(text())='LOGIN' or normalize-space(text())='Login']")))
    driver.execute_script("arguments[0].click();", btn)
    print("Second LOGIN clicked.")
except Exception as e:
    print(f"Second LOGIN error: {e}")

print("Waiting for dashboard...")
time.sleep(12)
print(f"URL: {driver.current_url}")

if "dashbord" not in driver.current_url and "dashboard" not in driver.current_url.lower():
    driver.save_screenshot("debug_login_failed.png")
    driver.quit()
    raise RuntimeError(f"Login failed. Still at: {driver.current_url}")

print("Logged in successfully!")

# ── Step 4: Navigate to attendance to capture auth headers ───────────────────

try:
    el = wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//*[contains(text(),"Class and Attendance")]')))
    driver.execute_script("arguments[0].click();", el)
    time.sleep(2)
except Exception:
    print("Class and Attendance link not found")

try:
    el = wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//a[@href="#/student/myclassattendance"]')))
    driver.execute_script("arguments[0].click();", el)
    time.sleep(2)
except Exception:
    print("My Attendance link not found")

try:
    dd = wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//mat-select | //*[contains(@class,"mat-select-trigger")]')))
    driver.execute_script("arguments[0].click();", dd)
    time.sleep(1)
    opt = wait.until(EC.element_to_be_clickable(
        (By.XPATH, f'//*[contains(text(),"{SEMESTER}")]')))
    driver.execute_script("arguments[0].click();", opt)
    print(f"Semester {SEMESTER} selected.")
except Exception as e:
    print(f"Semester dropdown: {e}")

try:
    sub = wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//button[contains(.,"Submit")]')))
    driver.execute_script("arguments[0].click();", sub)
    print("Submit clicked.")
except Exception as e:
    print(f"Submit: {e}")

time.sleep(6)

# ── Step 5: Capture auth from network logs ───────────────────────────────────

def get_req(logs, fragment):
    for req in reversed(logs):
        if fragment in req.get("url", ""):
            return req.get("headers", {}), req.get("postData", "")
    return None, None

all_logs = [json.loads(l["message"])["message"]["params"]["request"]
            for l in driver.get_log("performance")
            if "Network.requestWillBeSent" in l["message"]]

att_headers, att_payload   = get_req(all_logs, "getstudentattendancedetail")
_,           short_payload = get_req(all_logs, "getstudentbankinfo")
_,           fee_payload   = get_req(all_logs, "getmyactivefeeevents")

if not att_headers:
    driver.save_screenshot("debug_no_auth.png")
    driver.quit()
    raise RuntimeError("Could not capture auth headers.")

print("Auth headers captured.")
sess = requests.Session()
for c in driver.get_cookies():
    sess.cookies.set(c["name"], c["value"])
driver.quit()
print("Browser closed.")

# ── Step 6: Scrape all endpoints ─────────────────────────────────────────────

hdrs = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://webportal.juit.ac.in:6011/studentportal/",
    "User-Agent": "Mozilla/5.0",
}
if att_headers.get("Authorization"): hdrs["Authorization"] = att_headers["Authorization"]
if att_headers.get("LocalName"):     hdrs["LocalName"]     = att_headers["LocalName"]

def post(path, payload, label):
    print(f"Fetching [{label}]...")
    try:
        r = sess.post(f"{API_BASE}/{path}", headers=hdrs, data=payload, verify=False, timeout=30)
        print(f"  {r.status_code}")
        if r.status_code == 200:
            try:
                data = r.json()
                with open(f"{OUTPUT_DIR}/{label}.json", "w") as f:
                    json.dump(data, f, indent=2)
                print(f"  Saved.")
                return data
            except Exception:
                return None
    except Exception as e:
        print(f"  Error: {e}")
    return None

results = {
    "attendance_detail":            post("StudentClassAttendance/getstudentattendancedetail", att_payload, "attendance_detail"),
    "attendance_registration_info": post("StudentClassAttendance/getstudentInforegistrationforattendence", json.dumps({"instituteid": "INID2201J000001"}), "attendance_registration_info"),
    "bank_info":                    post("studentbankdetails/getstudentbankinfo", short_payload, "bank_info"),
    "fee_active_events":            post("onlinefeepayment/getmyactivefeeevents", fee_payload, "fee_active_events"),
    "exam_result":                  post("ExamResult/getstudentresult", short_payload, "exam_result"),
    "grade_card":                   post("ExamResult/getstudentgradecard", short_payload, "grade_card"),
    "personal_info":                post("studentpersonalinfo/getstudentpersonalinfo", short_payload, "personal_info"),
    "subject_faculty":              post("StudentClassAttendance/getmysubjectfaculty", short_payload, "subject_faculty"),
}

meta = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "semester": SEMESTER,
    "endpoints_fetched": [k for k,v in results.items() if v is not None],
    "endpoints_failed":  [k for k,v in results.items() if v is None],
}
with open(f"{OUTPUT_DIR}/meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"\nDone! Fetched: {meta['endpoints_fetched']}")
print(f"Failed: {meta['endpoints_failed']}")
