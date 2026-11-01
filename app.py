"""Testlingo - plain-English test automation for Web, Mobile and API.

Run:  python app.py   then open http://127.0.0.1:5000
"""
import hmac
import os
import re
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory

from runners import api_runner, importer, java_gen, mobile_runner, netguard, web_runner
from runners.common import case_text, split_cases

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", 300)) * 1024 * 1024

# ---------------- online-deployment settings (all optional, set as environment variables) ----------------
# The name shown in the page header, browser tab and reports. Change it here or with the APP_NAME setting.
APP_NAME = os.environ.get("APP_NAME", "Testlingo").strip() or "Testlingo"
APP_TAGLINE = os.environ.get("APP_TAGLINE", "Write tests in plain English. Run them for real.")
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "")      # protects the site with a login prompt
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL_RUNS", 2))     # browsers use ~250 MB each
RUN_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
ONLINE = bool(os.environ.get("PORT")) and os.environ.get("HOST", "0.0.0.0") != "127.0.0.1"

# Pages anyone can open without the password: the demo targets (so the server's own
# browser can test them) and the health check the host uses.
OPEN_PATHS = ("/demo/", "/healthz")


@app.before_request
def require_password():
    if not ACCESS_PASSWORD or request.path.startswith(OPEN_PATHS):
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", ACCESS_PASSWORD):
        return None
    return Response(f"{APP_NAME} needs a password.", 401, {"WWW-Authenticate": 'Basic realm="Test automation"'})


MISSING_PACKAGES = []
for _pkg, _what in (("openpyxl", "Excel upload and download"), ("playwright", "web tests")):
    try:
        __import__(_pkg)
    except ImportError:
        MISSING_PACKAGES.append((_pkg, _what))


def _missing_message(pkg):
    return (f"The '{pkg}' package isn't installed, so this can't run yet. Stop Testlingo and run start.bat "
            f"(Windows) or ./start.sh (Mac/Linux) again, or run:  pip install -r requirements.txt")


@app.errorhandler(ImportError)
def on_missing_package(e):
    found = re.search(r"named '?([\w.]+)", str(e))
    pkg = (getattr(e, "name", None) or (found.group(1) if found else "a required")).split(".")[0]
    msg = _missing_message(pkg)
    if request.path.startswith("/api/"):
        return jsonify(error=msg), 500
    return Response(msg, 500, mimetype="text/plain")


@app.errorhandler(500)
def on_server_error(e):
    original = getattr(e, "original_exception", None) or e
    app.logger.exception("Unexpected error", exc_info=original)
    msg = f"Something went wrong on the server: {type(original).__name__}: {original}"
    if request.path.startswith("/api/"):
        return jsonify(error=msg), 500
    return Response(msg, 500, mimetype="text/plain")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/config")
def config():
    return jsonify(online=ONLINE, private_networks_blocked=netguard.ENABLED, max_parallel=MAX_PARALLEL,
                   app_name=APP_NAME, tagline=APP_TAGLINE)


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE, "templates"), "index.html")


# ---------------- Excel / CSV import ----------------

@app.post("/api/import")
def import_cases():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Choose an Excel (.xlsx) or CSV file."), 400
    name = f.filename
    if not name.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv", ".tsv")):
        return jsonify(error="Upload an Excel (.xlsx) or CSV file."), 400
    try:
        tables = importer.read_tables(name, f.read())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception:  # noqa: BLE001 - a damaged or password-protected file
        return jsonify(error=f"{name} could not be opened. Check it isn't password-protected, then save it again as .xlsx."), 400
    title = os.path.splitext(os.path.basename(name))[0].replace("_", " ").strip()
    result = importer.tables_to_text(tables, title)
    if not result["sheets_used"]:
        return jsonify(error="No Steps column found. Add a header row with columns like "
                             "Test Case ID, Title, Steps and Expected Result (or download the template)."), 400
    if not result["cases"]:
        return jsonify(error="The file has a Steps column but no test cases with steps to run.", **result), 400
    return jsonify(result)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(data, filename):
    safe = re.sub(r"[^\w\- .]", "", filename).strip() or "testlingo.xlsx"
    return Response(data, mimetype=XLSX, headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@app.post("/api/export.xlsx")
def export_cases():
    """The test cases in the box, as an Excel file in the same layout Upload Excel reads."""
    d = request.get_json(force=True)
    suite, cases = split_cases(d.get("testcase", ""))
    if not cases:  # nothing written yet: hand over the template instead of an error
        platform = d.get("platform", "web")
        return _xlsx(importer.template_xlsx(platform), f"testlingo-{platform}-test-case-template.xlsx")
    name = (suite or f"testlingo-{d.get('platform', 'web')}-test-cases")
    return _xlsx(importer.cases_xlsx(suite, cases), f"{name}.xlsx")


@app.get("/api/jobs/<job_id>/results.xlsx")
def export_results(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="This run is no longer available. Run the tests again."), 404
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(job["created"]))
    name = f"{job.get('suite') or 'testlingo-' + job['platform']}-results-{stamp}"
    return _xlsx(importer.results_xlsx(job, job.get("case_steps", [])), f"{name}.xlsx")


@app.get("/api/template.xlsx")
def template():
    platform = request.args.get("platform", "web")
    return Response(importer.template_xlsx(platform),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="testlingo-{platform}-test-case-template.xlsx"'})


@app.post("/api/appium/check")
def appium_check():
    """Is the Appium server (or cloud service) reachable? Used by the Check connection button."""
    import requests as rq
    url = (request.get_json(force=True).get("url") or "").strip().rstrip("/")
    if not url:
        return jsonify(ok=False, message="Type the Appium server address first, e.g. http://127.0.0.1:4723")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    shown = re.sub(r"//[^/@]+@", "//", url)
    if ONLINE and netguard.ENABLED and not netguard.is_allowed(url):
        return jsonify(ok=False, message="The online Testlingo can't reach an Appium server on your own computer. "
                                         "Run Testlingo on that computer (start.bat / start.sh), or use BrowserStack.")
    if "browserstack" in url.lower():
        from urllib.parse import urlparse
        p = urlparse(url)
        if not p.username or p.username == "USERNAME":
            return jsonify(ok=False, message="Put your BrowserStack username and access key in place of USERNAME and ACCESS_KEY.")
        try:
            r = rq.get("https://api-cloud.browserstack.com/app-automate/plan.json", auth=(p.username, p.password), timeout=10)
        except rq.RequestException as e:
            return jsonify(ok=False, message=f"Could not reach BrowserStack: {e}")
        if r.status_code == 401:
            return jsonify(ok=False, message="BrowserStack refused the username / access key. Copy them again from your BrowserStack account settings.")
        return jsonify(ok=r.ok, message="BrowserStack account connected." if r.ok else f"BrowserStack answered {r.status_code}.")
    try:
        r = rq.get(url + "/status", timeout=5)
        info = r.json().get("value", {}) if r.ok else {}
    except rq.RequestException:
        return jsonify(ok=False, message=f"Nothing is answering at {shown}. Start Appium: open a terminal and run  appium  "
                                         "(then leave that window open).")
    except ValueError:
        info = {}
    if not r.ok:
        return jsonify(ok=False, message=f"{shown} answered with error {r.status_code}. Is that really the Appium server?")
    version = (info.get("build") or {}).get("version")
    return jsonify(ok=True, message=f"Appium {version + ' ' if version else ''}is running at {shown}. "
                                    "Make sure your emulator or phone is on (adb devices should list it).")


# ---------------- test runs ----------------
# A run is a job: one or more test cases that run in the background while the page
# asks for progress. Each test case gets its own browser / app session / variables.

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_KEEP_SECONDS = 3600


def _start_job(platform, testcase, run_one, java_kw, parallel=1, cleanup=None):
    suite, cases = split_cases(testcase)
    if not cases:
        return jsonify(error="No test steps found. Write at least one step, e.g. Click \"Login\"."), 400
    if len(cases) > 200:
        return jsonify(error="That's more than 200 test cases. Split them into smaller suites."), 400
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "platform": platform, "suite": suite, "status": "running", "created": time.time(),
           "cases": [{"index": i, "title": t or f"Test case {i + 1}", "status": "queued", "result": None}
                     for i, (t, _) in enumerate(cases)],
           "cancel": False, "java": None, "case_steps": [st for _, st in cases]}
    with JOBS_LOCK:
        for old in [k for k, j in JOBS.items() if time.time() - j["created"] > JOB_KEEP_SECONDS]:
            JOBS.pop(old, None)
        JOBS[job_id] = job

    def work(i):
        case = job["cases"][i]
        if job["cancel"]:
            case["status"] = "cancelled"
            return
        with RUN_SLOTS:  # never more browsers / sessions than the server can hold
            if job["cancel"]:
                case["status"] = "cancelled"
                return
            case["status"] = "running"
            title, steps = cases[i]
            try:
                result = run_one(case_text(title, steps))
            except Exception as e:  # noqa: BLE001 - one broken case must not stop the others
                res = [{"index": 0, "step": "Run the test case", "status": "failed",
                        "message": f"{type(e).__name__}: {e}", "code": "", "ms": 0}]
                result = {"title": title, "platform": platform, "steps": res,
                          "summary": {"status": "failed", "total": 1, "passed": 0, "failed": 1, "skipped": 0,
                                      "duration_ms": 0}, "code": ""}
            result["title"] = result.get("title") or case["title"]
            result.pop("java", None)
            case["result"] = result
            case["status"] = result["summary"]["status"]

    def runner():
        try:
            n = max(1, min(int(parallel or 1), MAX_PARALLEL, len(cases)))
            if n == 1:
                for i in range(len(cases)):
                    work(i)
            else:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=n) as pool:
                    list(pool.map(work, range(len(cases))))
            try:
                kw = java_kw() if callable(java_kw) else java_kw
                job["java"] = java_gen.build(platform, cases, suite, **kw)
            except Exception as e:  # noqa: BLE001 - results are still useful without Java
                job["java"] = {"error": f"Could not generate Java: {e}"}
        finally:
            job["status"] = "cancelled" if job["cancel"] else "done"
            if cleanup:
                cleanup()

    threading.Thread(target=runner, daemon=True).start()
    return jsonify(job_id=job_id, suite=suite, cases=[c["title"] for c in job["cases"]],
                   case_texts=[case_text(t, st) for t, st in cases],
                   parallel=max(1, min(int(parallel or 1), MAX_PARALLEL)))


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    """Progress of a run. ?have=3 skips the full results of the first 3 finished cases the page already has."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="This run is no longer available. Run the tests again."), 404
    have = int(request.args.get("have", 0))
    cases = []
    for c in job["cases"]:
        item = {"index": c["index"], "title": c["title"], "status": c["status"]}
        if c["result"] is not None and c["index"] >= have:
            item["result"] = c["result"]
        cases.append(item)
    done = sum(1 for c in job["cases"] if c["status"] in ("passed", "failed", "cancelled"))
    return jsonify(id=job_id, platform=job["platform"], suite=job["suite"], status=job["status"],
                   total=len(cases), finished=done, cases=cases,
                   java=job["java"] if job["status"] != "running" else None)


@app.post("/api/jobs/<job_id>/stop")
def job_stop(job_id):
    job = JOBS.get(job_id)
    if job:
        job["cancel"] = True  # the running test case finishes; the rest are not started
    return jsonify(ok=True)


@app.post("/api/run/web")
def run_web():
    d = request.get_json(force=True)
    if not d.get("url") or not d.get("testcase"):
        return jsonify(error="Please paste a URL and at least one test case."), 400
    headless = ONLINE or not d.get("show_browser")
    target = d["url"] if re.match(r"^https?://", d["url"]) else "https://" + d["url"].lstrip("/")
    return _start_job("web", d["testcase"], lambda tc: web_runner.run(d["url"], tc, headless=headless),
                      {"target": target}, d.get("parallel", 1))


@app.post("/api/run/api")
def run_api():
    d = request.get_json(force=True)
    if not d.get("url") or not d.get("testcase"):
        return jsonify(error="Please paste an API endpoint (or cURL) and at least one test case."), 400
    defaults = {}

    def run_one(tc):
        r = api_runner.run(d["url"], tc, d.get("method", "GET"), d.get("headers", ""), d.get("body", ""))
        defaults.setdefault("req", r.get("request_defaults", {}))
        return r

    def java_kw():
        req = defaults.get("req", {})
        return {"target": req.get("target", d["url"]), "method": req.get("method", d.get("method", "GET")),
                "headers": req.get("headers"), "body": req.get("body")}

    return _start_job("api", d["testcase"], run_one, java_kw, d.get("parallel", 1))


@app.post("/api/run/mobile")
def run_mobile():
    f = request.form.to_dict()
    app_path = f.get("app_path", "").strip()
    upload = request.files.get("app_file")
    saved = None
    if upload and upload.filename:
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in (".apk", ".aab", ".ipa", ".app", ".zip"):
            return jsonify(error="App file must be .apk, .aab, .ipa, .app or .zip"), 400
        saved = os.path.join(UPLOADS, f"{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}")
        upload.save(saved)
        app_path = saved
    if not (app_path or f.get("app_package") or f.get("bundle_id")):
        return jsonify(error="Upload an app file, paste its path/URL, or give an installed app's package / bundle id."), 400
    if not f.get("testcase"):
        return jsonify(error="Please paste at least one test case."), 400
    appium_url = f.get("appium_url") or "http://127.0.0.1:4723"
    if ONLINE and netguard.ENABLED and not netguard.is_allowed(appium_url):
        if saved:
            os.remove(saved)
        return jsonify(error="The online version can't reach an Appium server on your own computer. "
                             "Use a cloud device service such as BrowserStack, or run Testlingo locally."), 400

    state = {"app": app_path}
    lock = threading.Lock()

    def run_one(tc):
        # Upload to BrowserStack once for the whole suite, not once per test case.
        with lock:
            if "browserstack" in appium_url.lower() and state["app"] and not state["app"].startswith("bs://"):
                state["app"] = mobile_runner._browserstack_upload(state["app"], appium_url)
        return mobile_runner.run(state["app"], tc, f.get("platform", "Android"), appium_url, f.get("device_name", ""),
                                 f.get("app_package", ""), f.get("app_activity", ""), f.get("bundle_id", ""),
                                 f.get("os_version", ""))

    def java_kw():
        app = state["app"]
        return {"settings": {**f, "appium_url": appium_url, "app": app,
                             "app_was_uploaded": bool(saved) and not str(app).startswith("bs://"),
                             "app_name": upload.filename if saved else ""}}

    def cleanup():
        if saved and os.path.exists(saved):
            os.remove(saved)  # don't keep people's app files on the server

    return _start_job("mobile", f["testcase"], run_one, java_kw, f.get("parallel", 1), cleanup)


# ---------------- built-in demo targets (so the examples work right away) ----------------

USERS = [
    {"id": 1, "name": "Asha Patel", "email": "asha@example.com", "role": "admin"},
    {"id": 2, "name": "Ben Carter", "email": "ben@example.com", "role": "tester"},
    {"id": 3, "name": "Chen Wei", "email": "chen@example.com", "role": "developer"},
]


@app.get("/demo/<path:page>")
def demo_page(page):
    return send_from_directory(os.path.join(BASE, "demo"), page)


@app.get("/demo/")
def demo_home():
    return send_from_directory(os.path.join(BASE, "demo"), "login.html")


@app.post("/demo/api/login")
def demo_login():
    d = request.get_json(silent=True) or {}
    if d.get("username") == "tester" and d.get("password") == "Pass@123":
        return jsonify(token="demo-token-123", user={"username": "tester", "role": "tester"})
    return jsonify(error="Invalid username or password"), 401


@app.get("/demo/api/users")
def demo_users():
    role = request.args.get("role")
    data = [u for u in USERS if not role or u["role"] == role]
    return jsonify(page=1, total=len(data), data=data)


@app.get("/demo/api/users/<int:uid>")
def demo_user(uid):
    u = next((u for u in USERS if u["id"] == uid), None)
    return (jsonify(data=u), 200) if u else (jsonify(error="User not found"), 404)


@app.post("/demo/api/users")
def demo_create_user():
    if request.headers.get("Authorization") != "Bearer demo-token-123":
        return jsonify(error="Unauthorized"), 401
    d = request.get_json(silent=True) or {}
    if not d.get("name"):
        return jsonify(error="name is required"), 400
    u = {"id": max(x["id"] for x in USERS) + 1, "name": d["name"], "email": d.get("email", ""), "role": d.get("role", "tester")}
    USERS.append(u)
    return jsonify(data=u), 201


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"\n  {APP_NAME} is running →  http://127.0.0.1:{port}\n")
    for pkg, what in MISSING_PACKAGES:
        print(f"  WARNING: '{pkg}' is not installed, so {what} won't work. Run:  pip install -r requirements.txt\n")
    app.run(host=host, port=port, debug=False, threaded=True)
