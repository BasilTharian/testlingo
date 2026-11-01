"""Mobile runner: plain-English steps -> Appium actions (Android via UiAutomator2, iOS via XCUITest).

Needs an Appium server running (default http://127.0.0.1:4723) with an emulator,
simulator or real device connected. See README.md -> "Mobile setup".
"""
import base64
import re
import time

from .common import quoted, run_steps, split_steps, summary

UNKNOWN = ('I don\'t understand this step. Try: Tap "Login", Type "abc" into "Username", '
           'Verify "Welcome" is displayed, Swipe up, Press back.')


def _xp(s):
    """Quote a string safely for XPath."""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in s.split('"')) + ")"


def find(ctx, target, timeout=10):
    from appium.webdriver.common.appiumby import AppiumBy

    d, ios = ctx["driver"], ctx["ios"]
    t = _xp(target)
    if target.startswith("//"):
        strategies = [(AppiumBy.XPATH, target)]
    elif ios:
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID, target),
            (AppiumBy.IOS_PREDICATE, f'label == "{target}" OR name == "{target}" OR value == "{target}"'),
            (AppiumBy.IOS_PREDICATE, f'label CONTAINS[c] "{target}" OR placeholderValue CONTAINS[c] "{target}"'),
        ]
    else:
        strategies = [
            (AppiumBy.ACCESSIBILITY_ID, target),
            (AppiumBy.ID, target if ":" in target else f"{ctx.get('package', '')}:id/{target}"),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text({t})'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains({t})'),
            (AppiumBy.XPATH, f'//*[@hint={t} or @content-desc={t} or @resource-id={t}]'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains({t})'),
        ]
    deadline = time.time() + timeout
    while True:
        for by, value in strategies:
            try:
                els = d.find_elements(by, value)
                for el in els:
                    if el.is_displayed():
                        return el, f'driver.find_element({_by_name(by)}, {value!r})'
            except Exception:  # noqa: BLE001 - try next strategy
                continue
        if time.time() > deadline:
            raise AssertionError(f'Could not find "{target}" on the screen')
        time.sleep(0.5)


def _by_name(by):
    return {"accessibility id": "AppiumBy.ACCESSIBILITY_ID", "id": "AppiumBy.ID", "xpath": "AppiumBy.XPATH",
            "-android uiautomator": "AppiumBy.ANDROID_UIAUTOMATOR",
            "-ios predicate string": "AppiumBy.IOS_PREDICATE"}.get(by, repr(by))


def _shot(ctx):
    return ctx["driver"].get_screenshot_as_base64()


def _field(ctx, target):
    """Find an input: try the target, then the nth EditText/TextField when given like "field 2"."""
    m = re.match(r"^(field|input|textbox)\s*(\d+)$", target, re.I)
    if m:
        from appium.webdriver.common.appiumby import AppiumBy
        cls = "XCUIElementTypeTextField" if ctx["ios"] else "android.widget.EditText"
        els = ctx["driver"].find_elements(AppiumBy.CLASS_NAME, cls)
        i = int(m.group(2)) - 1
        assert i < len(els), f"Only {len(els)} text fields on screen"
        return els[i], f'driver.find_elements(AppiumBy.CLASS_NAME, "{cls}")[{i}]'
    return find(ctx, target)


def h_tap(ctx, m, step):
    target = quoted(step)[0]
    el, code = find(ctx, target)
    el.click()
    return f'Tapped "{target}"', f"{code}.click()"


def h_long(ctx, m, step):
    target = quoted(step)[0]
    el, code = find(ctx, target)
    ctx["driver"].execute_script("mobile: longClickGesture" if not ctx["ios"] else "mobile: touchAndHold",
                                 {"elementId": el.id, "duration": 1500})
    return f'Long-pressed "{target}"', f'driver.execute_script("mobile: longClickGesture", {{"elementId": el.id}})'


def h_type(ctx, m, step):
    q = quoted(step)
    value, field = q[0], q[1]
    el, code = _field(ctx, field)
    el.click()
    el.clear()
    el.send_keys(value)
    shown = "•" * len(value) if re.search(r"pass|pin", field, re.I) else value
    return f'Typed "{shown}" into "{field}"', f'{code}.send_keys("{value}")'


def h_clear(ctx, m, step):
    field = quoted(step)[0]
    el, code = _field(ctx, field)
    el.clear()
    return f'Cleared "{field}"', f"{code}.clear()"


def h_hide_kb(ctx, m, step):
    try:
        ctx["driver"].hide_keyboard()
    except Exception:  # noqa: BLE001 - keyboard may already be hidden
        pass
    return "Keyboard hidden", "driver.hide_keyboard()"


def h_swipe(ctx, m, step):
    direction = m.group("dir").lower()
    size = ctx["driver"].get_window_size()
    w, h = size["width"], size["height"]
    pts = {"up": (w / 2, h * .75, w / 2, h * .25), "down": (w / 2, h * .25, w / 2, h * .75),
           "left": (w * .8, h / 2, w * .2, h / 2), "right": (w * .2, h / 2, w * .8, h / 2)}[direction]
    ctx["driver"].swipe(*map(int, pts), 600)
    return f"Swiped {direction}", f"driver.swipe({', '.join(str(int(p)) for p in pts)}, 600)"


def h_scroll_to(ctx, m, step):
    text = quoted(step)[0]
    if ctx["ios"]:
        ctx["driver"].execute_script("mobile: scroll", {"predicateString": f'label CONTAINS "{text}"'})
    else:
        from appium.webdriver.common.appiumby import AppiumBy
        ctx["driver"].find_element(AppiumBy.ANDROID_UIAUTOMATOR,
            f'new UiScrollable(new UiSelector().scrollable(true)).scrollIntoView(new UiSelector().textContains({_xp(text)}))')
    return f'Scrolled to "{text}"', f"# scroll until {text!r} is visible"


def h_back(ctx, m, step):
    ctx["driver"].back()
    return "Pressed back", "driver.back()"


def h_wait_seconds(ctx, m, step):
    s = float(m.group("n"))
    time.sleep(s)
    return f"Waited {s:g} seconds", f"time.sleep({s:g})"


def h_wait_for(ctx, m, step):
    text = quoted(step)[0]
    find(ctx, text, timeout=20)
    return f'"{text}" appeared', f"# wait until {text!r} is visible"


def h_verify_not(ctx, m, step):
    text = quoted(step)[0]
    try:
        find(ctx, text, timeout=2)
    except AssertionError:
        return f'"{text}" is not displayed', f"# assert {text!r} not visible"
    raise AssertionError(f'"{text}" should not be displayed, but it is')


def h_verify(ctx, m, step):
    q = quoted(step)
    if len(q) >= 2 and re.search(r"(has|have) (text|value)", step, re.I):
        el, code = find(ctx, q[0])
        actual = el.text
        assert q[1].lower() in (actual or "").lower(), f'"{q[0]}" shows "{actual}", expected "{q[1]}"'
        return f'"{q[0]}" shows "{actual}"', f'assert "{q[1]}" in {code}.text'
    text = q[0]
    try:
        _, code = find(ctx, text, timeout=8)
    except AssertionError:
        if text.lower() in ctx["driver"].page_source.lower():
            return f'Found "{text}" in the screen source', f'assert "{text}" in driver.page_source'
        raise AssertionError(f'Expected to see "{text}" on the screen, but it was not found')
    return f'"{text}" is displayed', f"assert {code}.is_displayed()"


def h_rotate(ctx, m, step):
    o = "LANDSCAPE" if "landscape" in step.lower() else "PORTRAIT"
    ctx["driver"].orientation = o
    return f"Rotated to {o.lower()}", f'driver.orientation = "{o}"'


def h_background(ctx, m, step):
    n = re.search(r"(\d+)", step)
    secs = int(n.group(1)) if n else 3
    ctx["driver"].background_app(secs)
    return f"Sent app to background for {secs}s", f"driver.background_app({secs})"


def h_screenshot(ctx, m, step):
    return "Screenshot taken", "driver.get_screenshot_as_file('step.png')", {"screenshot": _shot(ctx)}


def h_launch(ctx, m, step):
    return "App is open", "# app launched by the session"


I = re.I
HANDLERS = [
    (re.compile(r"^(launch|open|start)\s+(the\s+)?app\b", I), h_launch),
    (re.compile(r"^hide\s+(the\s+)?keyboard", I), h_hide_kb),
    (re.compile(r"^clear\b", I), h_clear),
    (re.compile(r"^(type|enter|fill|input|write)\b", I), h_type),
    (re.compile(r"^(long[- ]?press|press and hold|touch and hold)\b", I), h_long),
    (re.compile(r"^(press|tap|go)\s+(the\s+)?back\b", I), h_back),
    (re.compile(r"^(tap|click|press|select|choose)\b", I), h_tap),
    (re.compile(r"^(swipe|scroll)\s+(?P<dir>up|down|left|right)\b", I), h_swipe),
    (re.compile(r"^scroll\s+(to|until)\b", I), h_scroll_to),
    (re.compile(r"^wait\s+(for\s+)?(?P<n>\d+(\.\d+)?)\s*(s|sec|secs|seconds?)\b", I), h_wait_seconds),
    (re.compile(r"^wait\s+(for|until)\b", I), h_wait_for),
    (re.compile(r"^rotate\b", I), h_rotate),
    (re.compile(r"background", I), h_background),
    (re.compile(r"(screenshot|capture)", I), h_screenshot),
    (re.compile(r"(not (be )?(visible|displayed|shown)|not see|should not|does not|doesn't)", I), h_verify_not),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm|i should see|should see|see)\b", I), h_verify),
]


def _browserstack_upload(app, appium_url):
    """Upload a local app file to BrowserStack and return its bs:// id."""
    import os
    from urllib.parse import urlparse

    import requests
    p = urlparse(appium_url)
    user = p.username or os.environ.get("BROWSERSTACK_USERNAME")
    key = p.password or os.environ.get("BROWSERSTACK_ACCESS_KEY")
    if not (user and key):
        raise RuntimeError("Add your BrowserStack username and access key to the Appium server address, "
                           "like https://USERNAME:ACCESS_KEY@hub-cloud.browserstack.com/wd/hub")
    if app.startswith(("http://", "https://")):
        r = requests.post("https://api-cloud.browserstack.com/app-automate/upload", auth=(user, key),
                          data={"url": app}, timeout=300)
    else:
        with open(app, "rb") as fh:
            r = requests.post("https://api-cloud.browserstack.com/app-automate/upload", auth=(user, key),
                              files={"file": fh}, timeout=300)
    if r.status_code != 200 or "app_url" not in r.text:
        raise RuntimeError(f"BrowserStack upload failed ({r.status_code}): {r.text[:200]}")
    return r.json()["app_url"]


def run(app, testcase, platform_name="Android", appium_url="http://127.0.0.1:4723",
        device_name="", app_package="", app_activity="", bundle_id="", os_version=""):
    started = time.time()
    title, steps = split_steps(testcase)
    ios = platform_name.lower() == "ios"
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        from appium.options.ios import XCUITestOptions
    except ImportError:
        return _setup_error(title, steps, started,
                            "The Appium Python client is not installed. Run: pip install Appium-Python-Client")

    cloud = "browserstack" in appium_url.lower()
    if cloud and app and not app.startswith("bs://"):
        try:
            app = _browserstack_upload(app, appium_url)
        except Exception as e:  # noqa: BLE001
            return _setup_error(title, steps, started, str(e))

    opts = XCUITestOptions() if ios else UiAutomator2Options()
    opts.device_name = device_name or ("iPhone 15" if ios else "Android Emulator")
    opts.new_command_timeout = 120
    if app:
        opts.app = app
    if not ios and app_package:
        opts.app_package = app_package
        if app_activity:
            opts.app_activity = app_activity
    if ios and bundle_id:
        opts.bundle_id = bundle_id
    if not ios:
        opts.auto_grant_permissions = True
    if os_version:
        opts.platform_version = os_version
    if cloud:
        if not device_name:
            return _setup_error(title, steps, started,
                                'BrowserStack needs a device name, e.g. "Google Pixel 7" or "iPhone 15", and an OS version like "13.0" or "17".')
        bs = {"deviceName": device_name, "projectName": "Testlingo", "sessionName": title or "Testlingo run"}
        if os_version:
            bs["osVersion"] = os_version
        opts.set_capability("bstack:options", bs)

    caps = {k: v for k, v in opts.to_capabilities().items()}
    shown_url = re.sub(r"//[^/@]+@", "//USERNAME:ACCESS_KEY@", appium_url)  # never print keys in the code
    code = [
        "from appium import webdriver",
        "from appium.options." + ("ios import XCUITestOptions" if ios else "android import UiAutomator2Options"),
        "from appium.webdriver.common.appiumby import AppiumBy",
        "import time", "",
        f"options = {'XCUITestOptions' if ios else 'UiAutomator2Options'}().load_capabilities({caps!r})",
        f'driver = webdriver.Remote("{shown_url}", options=options)',
    ]
    try:
        driver = webdriver.Remote(appium_url, options=opts)
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        return _setup_error(title, steps, started,
                            f"Could not start the app on {shown_url}: {msg}. Is the Appium server running and a device/emulator connected?",
                            code)

    ctx = {"driver": driver, "ios": ios, "vars": {},
           "package": app_package or (driver.capabilities.get("appPackage") if not ios else "")}
    try:
        results = run_steps(steps, HANDLERS, ctx, UNKNOWN)
        for r in results:
            if r["status"] == "failed":
                try:
                    r["screenshot"] = _shot(ctx)
                except Exception:  # noqa: BLE001
                    pass
        final = _shot(ctx)
    finally:
        driver.quit()

    for r in results:
        if r.get("code"):
            code += [f"# {r['step']}", r["code"]]
    code.append("driver.quit()")
    return {"title": title, "platform": "mobile", "target": app or app_package or bundle_id, "steps": results,
            "summary": summary(results, started), "code": "\n".join(code), "final_screenshot": final}


def _setup_error(title, steps, started, message, code=None):
    results = [{"index": 0, "step": "Start the app session", "status": "failed", "message": message, "code": "", "ms": 0}]
    results += [{"index": i, "step": s, "status": "skipped", "message": "Skipped because the app did not start",
                 "code": "", "ms": 0} for i, s in enumerate(steps, 1)]
    return {"title": title, "platform": "mobile", "steps": results, "summary": summary(results, started),
            "code": "\n".join(code or [])}
