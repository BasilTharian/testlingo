"""Web runner: plain-English steps -> Playwright actions (Chromium)."""
import base64
import re
import time
from urllib.parse import urljoin

from . import netguard
from .common import quoted, run_steps, split_steps, summary

UNKNOWN = ("I don't understand this step. Try a form like: Click \"Login\", "
           "Type \"abc\" into \"Username\", Verify page contains \"Welcome\".")


def _is_selector(t):
    return t.startswith(("#", ".", "[", "//", "css=", "xpath=", "text="))


def find(page, target, kind="any", timeout=5000):
    """Find an element by visible text, label, placeholder, name, id or CSS/XPath."""
    if _is_selector(target):
        loc = page.locator(target[4:] if target.startswith("css=") else target).first
        loc.wait_for(state="visible", timeout=timeout)
        return loc, f'page.locator("{target}")'

    exact = re.compile(rf"^\s*{re.escape(target)}\s*$", re.I)
    candidates = []
    if kind in ("field", "any"):
        candidates += [
            (lambda: page.get_by_label(target, exact=False), f'page.get_by_label("{target}")'),
            (lambda: page.get_by_placeholder(target, exact=False), f'page.get_by_placeholder("{target}")'),
            (lambda: page.locator(f'[name="{target}"], #{_css_id(target)}'), f'page.locator(\'[name="{target}"]\')'),
            (lambda: page.get_by_role("textbox", name=target), f'page.get_by_role("textbox", name="{target}")'),
        ]
    if kind in ("click", "any"):
        candidates += [
            (lambda: page.get_by_role("button", name=exact), f'page.get_by_role("button", name="{target}")'),
            (lambda: page.get_by_role("link", name=exact), f'page.get_by_role("link", name="{target}")'),
            (lambda: page.get_by_role("button", name=target), f'page.get_by_role("button", name="{target}")'),
            (lambda: page.get_by_role("link", name=target), f'page.get_by_role("link", name="{target}")'),
            (lambda: page.get_by_role("tab", name=target), f'page.get_by_role("tab", name="{target}")'),
            (lambda: page.get_by_role("menuitem", name=target), f'page.get_by_role("menuitem", name="{target}")'),
            (lambda: page.locator(f'input[type=submit][value="{target}"], input[type=button][value="{target}"]'),
             f'page.locator(\'input[value="{target}"]\')'),
            (lambda: page.get_by_text(target, exact=True), f'page.get_by_text("{target}", exact=True)'),
            (lambda: page.get_by_text(target), f'page.get_by_text("{target}")'),
            (lambda: page.get_by_title(target), f'page.get_by_title("{target}")'),
            (lambda: page.get_by_alt_text(target), f'page.get_by_alt_text("{target}")'),
            (lambda: page.locator(f'[name="{target}"], #{_css_id(target)}'), f'page.locator(\'#{target}\')'),
        ]

    deadline = time.time() + timeout / 1000
    while True:
        for make, code in candidates:
            try:
                loc = make()
                n = loc.count()
                for i in range(min(n, 5)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el, code + (f".nth({i})" if i else "")
            except Exception:  # noqa: BLE001 - try the next strategy
                continue
        if time.time() > deadline:
            raise AssertionError(f'Could not find "{target}" on the page')
        time.sleep(0.25)


def _css_id(t):
    return re.sub(r"[^\w-]", "", t) or "__none__"


def _shot(page):
    return base64.b64encode(page.screenshot(full_page=False)).decode()


# ---------- step handlers: each returns (message, generated_code[, extra]) ----------

def h_open(ctx, m, step):
    q = quoted(step)
    target = q[0] if q else m.group("rest").strip()
    url = target if re.match(r"^https?://", target) else urljoin(ctx["base"], target)
    ctx["page"].goto(url, wait_until="domcontentloaded")
    return f"Opened {ctx['page'].url}", f'page.goto("{url}")'


def h_type(ctx, m, step):
    q = quoted(step)
    if len(q) < 2:
        raise ValueError('Use: Type "value" into "field name"')
    value, field = q[0], q[1]
    el, code = find(ctx["page"], field, "field")
    el.fill(value)
    shown = "•" * len(value) if re.search(r"pass", field, re.I) else value
    return f'Typed "{shown}" into "{field}"', f'{code}.fill("{value}")'


def h_clear(ctx, m, step):
    field = quoted(step)[0]
    el, code = find(ctx["page"], field, "field")
    el.fill("")
    return f'Cleared "{field}"', f'{code}.fill("")'


def h_select(ctx, m, step):
    q = quoted(step)
    option, field = q[0], q[1]
    el, code = find(ctx["page"], field, "field")
    try:
        el.select_option(label=option)
    except Exception:  # noqa: BLE001
        el.select_option(value=option)
    return f'Selected "{option}" in "{field}"', f'{code}.select_option(label="{option}")'


def h_check(ctx, m, step):
    target = quoted(step)[0]
    uncheck = m.group(1).lower() == "uncheck"
    el, code = find(ctx["page"], target, "any")
    (el.uncheck if uncheck else el.check)()
    verb = "uncheck" if uncheck else "check"
    return f'{verb.title()}ed "{target}"', f"{code}.{verb}()"


def h_click(ctx, m, step):
    if not quoted(step):
        if re.match(r"\s*submit", step, re.I):
            ctx["page"].keyboard.press("Enter")
            return "Submitted the form (pressed Enter)", 'page.keyboard.press("Enter")'
        raise ValueError('Put what to click in quotes, e.g. Click "Login"')
    target = quoted(step)[0]
    el, code = find(ctx["page"], target, "click")
    double = bool(re.match(r"\s*double", step, re.I))
    (el.dblclick if double else el.click)()
    try:
        ctx["page"].wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    return f'{"Double-clicked" if double else "Clicked"} "{target}"', f'{code}.{"dblclick" if double else "click"}()'


def h_hover(ctx, m, step):
    target = quoted(step)[0]
    el, code = find(ctx["page"], target, "any")
    el.hover()
    return f'Hovered over "{target}"', f"{code}.hover()"


def h_press(ctx, m, step):
    key = quoted(step)[0] if quoted(step) else m.group("key")
    key = {"enter": "Enter", "tab": "Tab", "escape": "Escape", "esc": "Escape"}.get(key.lower(), key)
    ctx["page"].keyboard.press(key)
    return f"Pressed {key}", f'page.keyboard.press("{key}")'


def h_wait_seconds(ctx, m, step):
    s = float(m.group("n"))
    ctx["page"].wait_for_timeout(s * 1000)
    return f"Waited {s:g} seconds", f"page.wait_for_timeout({int(s * 1000)})"


def h_wait_for(ctx, m, step):
    text = quoted(step)[0]
    ctx["page"].get_by_text(text).first.wait_for(state="visible", timeout=15000)
    return f'"{text}" appeared', f'page.get_by_text("{text}").first.wait_for()'


def h_scroll(ctx, m, step):
    direction = m.group("dir").lower()
    dy = {"down": 600, "up": -600}.get(direction)
    if dy is None:
        ctx["page"].evaluate("window.scrollTo(0, %s)" % ("0" if direction == "top" else "document.body.scrollHeight"))
    else:
        ctx["page"].mouse.wheel(0, dy)
    return f"Scrolled {direction}", f"page.mouse.wheel(0, {dy or 0})"


def h_verify_not_contains(ctx, m, step):
    text = quoted(step)[0]
    body = ctx["page"].inner_text("body")
    assert text.lower() not in body.lower(), f'Page should not contain "{text}", but it does'
    return f'Page does not contain "{text}"', f'expect(page.locator("body")).not_to_contain_text("{text}")'


def h_verify_title(ctx, m, step):
    text = quoted(step)[0]
    title = ctx["page"].title()
    exact = bool(re.search(r"\b(is|equals?)\b", step, re.I)) and "contain" not in step.lower()
    ok = title.strip().lower() == text.lower() if exact else text.lower() in title.lower()
    assert ok, f'Expected title {"to be" if exact else "to contain"} "{text}", actual title is "{title}"'
    return f'Title is "{title}"', f'expect(page).to_have_title(re.compile(r"{re.escape(text)}"))'


def h_verify_url(ctx, m, step):
    text = quoted(step)[0]
    url = ctx["page"].url
    assert text.lower() in url.lower(), f'Expected URL to contain "{text}", actual URL is {url}'
    return f"URL is {url}", f'expect(page).to_have_url(re.compile(r"{re.escape(text)}"))'


def h_verify_field_value(ctx, m, step):
    q = quoted(step)
    field, value = q[0], q[1]
    el, code = find(ctx["page"], field, "field")
    actual = el.input_value()
    assert actual == value, f'Expected "{field}" to have value "{value}", actual "{actual}"'
    return f'"{field}" has value "{value}"', f'expect({code}).to_have_value("{value}")'


def h_verify_visible(ctx, m, step):
    text = quoted(step)[0]
    page = ctx["page"]
    deadline = time.time() + 5
    while True:
        body = page.inner_text("body")
        if text.lower() in body.lower():
            break
        try:
            find(page, text, "any", timeout=300)
            break
        except AssertionError:
            pass
        if time.time() > deadline:
            raise AssertionError(f'Expected to see "{text}" on the page, but it was not found')
        time.sleep(0.3)
    return f'Found "{text}" on the page', f'expect(page.get_by_text("{text}").first).to_be_visible()'


def h_count(ctx, m, step):
    n = int(m.group("n"))
    sel = quoted(step)[0]
    actual = ctx["page"].locator(sel).count()
    assert actual == n, f'Expected {n} elements matching "{sel}", found {actual}'
    return f'Found {n} × "{sel}"', f'expect(page.locator("{sel}")).to_have_count({n})'


def h_screenshot(ctx, m, step):
    return "Screenshot taken", "page.screenshot(path='step.png')", {"screenshot": _shot(ctx["page"])}


def h_back(ctx, m, step):
    ctx["page"].go_back()
    return "Went back", "page.go_back()"


def h_refresh(ctx, m, step):
    ctx["page"].reload()
    return "Page refreshed", "page.reload()"


def h_check_step(ctx, m, step):
    return h_check(ctx, re.match(r"(\w+)", step.replace("tick", "check", 1)), step)


def h_count_step(ctx, m, step):
    return h_count(ctx, re.search(r"(?P<n>\d+)", step), step)


I = re.I
HANDLERS = [
    (re.compile(r"^(open|go to|navigate to|visit|launch)\b(?P<rest>.*)$", I), h_open),
    (re.compile(r"^clear\b", I), h_clear),
    (re.compile(r"^(type|enter|fill|input|write)\b", I), h_type),
    (re.compile(r"^(select|choose)\b", I), h_select),
    (re.compile(r"^(check|uncheck|tick)\b", I), h_check_step),
    (re.compile(r"^(double[- ]?click|click|tap|press the|submit)\b", I), h_click),
    (re.compile(r"^(hover|mouse over)\b", I), h_hover),
    (re.compile(r"^press\s+(?P<key>\w+)", I), h_press),
    (re.compile(r"^wait\s+(for\s+)?(?P<n>\d+(\.\d+)?)\s*(s|sec|secs|seconds?)\b", I), h_wait_seconds),
    (re.compile(r"^wait\s+(for|until)\b", I), h_wait_for),
    (re.compile(r"^scroll\s+(to\s+(the\s+)?)?(?P<dir>down|up|top|bottom)", I), h_scroll),
    (re.compile(r"^(go back|navigate back|back)\b", I), h_back),
    (re.compile(r"^(refresh|reload)\b", I), h_refresh),
    (re.compile(r"(screenshot|capture)", I), h_screenshot),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm)?.*\btitle\b", I), h_verify_title),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm)?.*\burl\b", I), h_verify_url),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm)?.*(field|input)?.*\b(has|have) value\b", I), h_verify_field_value),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm).*\b(\d+)\b.*\b(elements?|items?)\b", I),
     h_count_step),
    (re.compile(r"(not contain|not see|not be (visible|displayed)|not displayed|does not|doesn't|should not)", I), h_verify_not_contains),
    (re.compile(r"^(verify|assert|check that|ensure|expect|confirm|i should see|should see|see)\b", I), h_verify_visible),
]


def run(target_url, testcase, headless=True):
    from playwright.sync_api import sync_playwright

    started = time.time()
    title, steps = split_steps(testcase)
    if not re.match(r"^https?://", target_url or ""):
        target_url = "https://" + (target_url or "").lstrip("/")

    code_lines = [
        "from playwright.sync_api import sync_playwright, expect",
        "import re",
        "",
        "with sync_playwright() as p:",
        "    browser = p.chromium.launch(headless=True)",
        "    page = browser.new_page()",
        f'    page.goto("{target_url}")',
    ]
    try:
        netguard.check_url(target_url)
    except netguard.BlockedTarget as e:
        res = [{"index": 0, "step": f"Open {target_url}", "status": "failed", "message": str(e), "code": "", "ms": 0}]
        return {"title": title, "platform": "web", "target": target_url, "steps": res,
                "summary": summary(res, started), "code": ""}

    with sync_playwright() as p:
        # --no-sandbox / --disable-dev-shm-usage keep Chromium stable inside containers
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_default_timeout(15000)
        if netguard.ENABLED:
            allowed = {}

            def guard(route):
                if not route.request.url.startswith("http"):
                    return route.continue_()  # data:, blob: and similar never leave the browser
                host = route.request.url.split("/")[2] if "://" in route.request.url else ""
                if host not in allowed:
                    allowed[host] = netguard.is_allowed(route.request.url)
                route.continue_() if allowed[host] else route.abort("blockedbyclient")
            page.route("**/*", guard)
        ctx = {"page": page, "base": target_url, "vars": {}}
        load_error = None
        # Open the pasted URL first unless step 1 opens something itself
        if not (steps and HANDLERS[0][0].search(steps[0])):
            try:
                page.goto(target_url, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                load_error = str(e).splitlines()[0]
        if load_error:
            results = [{"index": 0, "step": f"Open {target_url}", "status": "failed",
                        "message": load_error, "code": "", "ms": 0}]
        else:
            results = run_steps(steps, HANDLERS, ctx, UNKNOWN)
            # attach a screenshot to the failed step so you can see what went wrong
            for r in results:
                if r["status"] == "failed" and "screenshot" not in r:
                    try:
                        r["screenshot"] = _shot(page)
                    except Exception:  # noqa: BLE001
                        pass
            final = _shot(page)
        browser.close()

    for r in results:
        if r.get("code"):
            code_lines.append(f"    # {r['step']}")
            code_lines.append(f"    {r['code']}")
    code_lines.append("    browser.close()")
    out = {"title": title, "platform": "web", "target": target_url, "steps": results,
           "summary": summary(results, started), "code": "\n".join(code_lines)}
    if not load_error:
        out["final_screenshot"] = final
    return out
