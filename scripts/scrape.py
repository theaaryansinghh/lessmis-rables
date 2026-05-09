"""
scrape.py — single-session scraper with mid-run captcha polling.

Confirmed login flow:
  Page 1: USER ID + captcha → LOGIN  (URL: #/)
  Page 2: Password* → LOGIN          (URL: #/pwdlogin)
  Then: dashboard                    (URL: #/dashbord)

Flow:
  1. Open browser, fill username, commit captcha.png.
  2. Poll CAPTCHA_ANSWER variable every 10s.
  3. You set CAPTCHA_ANSWER to the captcha text.
  4. Script fills captcha, clicks LOGIN, waits for #/pwdlogin.
  5. Script fills password, clicks LOGIN, waits for #/dashbord.
  6. Scrapes all endpoints, commits JSONs.

Secrets needed:
  JUIT_USERNAME, JUIT_PASSWORD, GH_TOKEN, GH_REPO
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
GH_REPO   = os.environ["GH_REPO"]
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
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables/{VAR_NAME}"
    try:
        r = requests.get(url, headers=GH_HEADERS, timeout=10)
        print(f"  [poll] status={r.status_code} value='{r.json().get('value','')}'")
        if r.status_code == 200:
            val = r.json().get("value", "").strip()
            return val if val and val not in ("WAITING", "") else None
    except Exception as e:
        print(f"  [poll] error: {e}")
    return None

def set_var(value):
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables/{VAR_NAME}"
    r = requests.patch(url, headers=GH_HEADERS, timeout=10,
                       json={"name": VAR_NAME, "value": value})
    if r.status_code not in (200, 201, 204):
        requests.post(
            f"https://api.github.com/repos/{GH_REPO}/actions/variables",
            headers=GH_HEADERS, timeout=10,
            json={"name": VAR_NAME, "value": value}
        )
    print(f"  CAPTCHA_ANSWER set to '{value}'")

def git_commit_push(files, message):
    subprocess.run(["git", "config", "user.name",  "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    for f in files:
        subprocess.run(["git", "add", f], check=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        subprocess.run(["git", "push"], check=True)
        print(f"Committed: {message}")
    else:
        print(f"Nothing to commit ({message})")

# ── JS helper to set Angular input value ────────────────────────────────────

def angular_set(driver, element, value):
    """Set input value and fire Angular-compatible events."""
    driver.execute_script("""
        var el = arguments[0];
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, arguments[1]);
        el.dispatchEvent(new Event('input',  { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur',   { bubbles: true }));
    """, element, value)

# ── Chrome setup ─────────────────────────────────────────────────────────────

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
    service=Service(ChromeDriverManager().install()), options=options)
driver.execute_cdp_cmd("Network.enable", {})
driver.execute_script(
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
wait = WebDriverWait(driver, 45)

# ── Step 1: Load page, fill username, capture captcha ────────────────────────

print("Opening login page...")
driver.get(LOGIN_URL)
time.sleep(6)
print(f"URL: {driver.current_url}")

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
                    body = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": rid})
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

# Fill username
u = wait.until(EC.presence_of_element_located(
    (By.XPATH, '//input[contains(@placeholder,"USER ID") or contains(@placeholder,"User ID")]')))
angular_set(driver, u, USERNAME)
print(f"Username filled: {USERNAME}")

# Commit captcha for you to read
git_commit_push(["captcha.png"], "chore: update captcha [skip ci]")
set_var("WAITING")

# ── Step 2: Poll for captcha answer ─────────────────────────────────────────

print("\nWaiting for CAPTCHA_ANSWER (5 min timeout)...")
captcha_answer = None
for i in range(30):
    time.sleep(10)
    answer = get_captcha_answer()
    if answer:
        captcha_answer = answer
        print(f"Got captcha: '{captcha_answer}'")
        break
    print(f"  [{(i+1)*10}s] waiting...")

if not captcha_answer:
    driver.quit()
    raise RuntimeError("Timed out. Run workflow again.")

set_var("WAITING")

# ── Step 3: Fill captcha and submit page 1 ───────────────────────────────────

print("Filling captcha field...")

# Re-confirm username is still there
u = wait.until(EC.presence_of_element_located(
    (By.XPATH, '//input[contains(@placeholder,"USER ID") or contains(@placeholder,"User ID")]')))
current = u.get_attribute("value")
print(f"Username field value: '{current}'")
if current != USERNAME:
    print("Username was cleared, re-filling...")
    angular_set(driver, u, USERNAME)
    time.sleep(0.5)

# Fill captcha
c = wait.until(EC.presence_of_element_located(
    (By.XPATH, '//input[contains(@placeholder,"shown in the image") or '
               'contains(@placeholder,"Enter the text")]')))
angular_set(driver, c, captcha_answer)
time.sleep(1)

# Verify form values
print("Form values before LOGIN:")
for inp in driver.find_elements(By.XPATH, '//input[@placeholder]'):
    print(f"  '{inp.get_attribute('placeholder')}' = '{inp.get_attribute('value')}'")

# Click LOGIN (page 1)
btn = wait.until(EC.element_to_be_clickable(
    (By.XPATH, "//button[contains(.,'LOGIN') or contains(.,'Login')]")))
btn.click()
print("Page 1 LOGIN clicked.")

# ── Step 4: Wait for #/pwdlogin and fill password ────────────────────────────

print("Waiting for password page (#/pwdlogin)...")
try:
    WebDriverWait(driver, 30).until(
        lambda d: "pwdlogin" in d.current_url or "dashbord" in d.current_url)
    print(f"URL after page 1 LOGIN: {driver.current_url}")
except TimeoutException:
    driver.save_screenshot("debug_stuck_after_login1.png")
    git_commit_push(["debug_stuck_after_login1.png"], "debug: stuck after login1 [skip ci]")
    driver.quit()
    raise RuntimeError(
        "Did not reach #/pwdlogin after 30s. "
        "Captcha was probably wrong. Check debug_stuck_after_login1.png.")

if "pwdlogin" in driver.current_url:
    time.sleep(2)
    # Password field placeholder is "Password*"
    p = wait.until(EC.presence_of_element_located(
        (By.XPATH, '//input[@type="password"] | '
                   '//input[contains(@placeholder,"Password") or '
                   'contains(@placeholder,"password")]')))
    angular_set(driver, p, PASSWORD)
    print("Password filled.")
    time.sleep(1)

    btn2 = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[contains(.,'LOGIN') or contains(.,'Login')]")))
    btn2.click()
    print("Page 2 LOGIN clicked.")

print("Waiting for dashboard...")
time.sleep(10)
print(f"Final URL: {driver.current_url}")

if "dashbord" not in driver.current_url and "dashboard" not in driver.current_url.lower():
    driver.save_screenshot("debug_login_failed.png")
    git_commit_push(["debug_login_failed.png"], "debug: login failed [skip ci]")
    driver.quit()
    raise RuntimeError(f"Login failed. URL: {driver.current_url}")

print("Logged in!")

# ── Step 5: Navigate to attendance to capture auth headers ────────────────────

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

# Also navigate to fee page to capture fee_payload
try:
    driver.get(f"{LOGIN_URL.replace('#/', '#/student/feeDetails')}")
    time.sleep(4)
    print("Navigated to fee page.")
except Exception as e:
    print(f"Fee nav error: {e}")

time.sleep(3)

# ── Step 6: Capture auth from network logs ────────────────────────────────────

def get_req(fragment):
    for log in reversed(driver.get_log("performance")):
        try:
            msg = json.loads(log["message"])["message"]
            if (msg["method"] == "Network.requestWillBeSent"
                    and fragment in msg["params"]["request"].get("url", "")):
                req = msg["params"]["request"]
                return req.get("headers", {}), req.get("postData", "")
        except Exception:
            continue
    return None, None

att_headers, att_payload   = get_req("getstudentattendancedetail")
_,           fee_payload   = get_req("getmyactivefeeevents")

if not att_headers:
    driver.save_screenshot("debug_no_auth.png")
    git_commit_push(["debug_no_auth.png"], "debug: no auth headers [skip ci]")
    driver.quit()
    raise RuntimeError("Could not capture auth headers.")

print("Auth headers captured.")

# short_payload: most endpoints use the same encrypted student ID as the
# attendance payload. Extract it directly from att_payload.
# att_payload is the full attendance request body — the portal uses the
# same encrypted studentid field for all other endpoints too.
short_payload = att_payload
print(f"Using att_payload as short_payload (length: {len(att_payload) if att_payload else 0})")

sess = requests.Session()
for c in driver.get_cookies():
    sess.cookies.set(c["name"], c["value"])
driver.quit()
print("Browser closed.")

# ── Step 7: Scrape all endpoints ──────────────────────────────────────────────

hdrs = {
    "Accept":       "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer":      "https://webportal.juit.ac.in:6011/studentportal/",
    "User-Agent":   "Mozilla/5.0",
}
if att_headers.get("Authorization"): hdrs["Authorization"] = att_headers["Authorization"]
if att_headers.get("LocalName"):     hdrs["LocalName"]     = att_headers["LocalName"]

def post(path, payload, label):
    print(f"Fetching [{label}]...")
    try:
        r = sess.post(f"{API_BASE}/{path}", headers=hdrs,
                      data=payload, verify=False, timeout=30)
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
