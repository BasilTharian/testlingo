"""Turns a plain-English test case into a ready-to-run Java test (Maven project).

Web    -> Selenium WebDriver + TestNG
Mobile -> Appium java-client + TestNG
API    -> RestAssured + TestNG

Steps are classified with the same HANDLERS the Python runners use, so the Java test
does exactly what the run did, step for step, including steps that failed or were skipped.
"""
import base64
import io
import json
import os
import re
import zipfile
from urllib.parse import urljoin

from . import api_runner, mobile_runner, web_runner
from .common import QUOTED, quoted, split_steps

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "java_templates")

VERSIONS = {
    "selenium": "4.44.0",
    "appium": "10.1.1",
    "restassured": "5.5.7",
    "testng": "7.12.0",
}


# ---------------------------------------------------------------- Java text helpers

def jlit(s):
    """A Java string literal."""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "").replace("\t", "\\t")
    return f'"{s}"'


def jexpr(template, declared):
    """A Java expression for text that may contain {{saved}} variables."""
    parts, pos = [], 0
    for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", template):
        name = _ident(m.group(1))
        if name not in declared:
            continue
        if m.start() > pos:
            parts.append(jlit(template[pos:m.start()]))
        parts.append(name)
        pos = m.end()
    if pos < len(template) or not parts:
        parts.append(jlit(template[pos:]))
    return " + ".join(parts)


def jlower(template, declared):
    """Lower-cased Java text: done now for plain text, at run time when it holds a variable."""
    if any(_ident(v) in declared for v in re.findall(r"\{\{\s*(\w+)\s*\}\}", template)):
        return f"({jexpr(template, declared)}).toLowerCase()"
    return jlit(template.lower())


def _ident(name):
    name = re.sub(r"\W", "_", name)
    return name if re.match(r"[A-Za-z_]", name) else "v" + name


def class_name(title, platform):
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    base = "".join(w[:1].upper() + w[1:] for w in words)[:60]
    if not base or not base[0].isalpha():
        base = {"web": "Web", "mobile": "Mobile", "api": "Api"}[platform] + base
    return base if base.endswith("Test") else base + "Test"


def method_name(title):
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    if not words:
        return "runTest"
    name = words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
    return name[:60] if name[0].isalpha() else "test" + name[:56]


def _classify(step, handlers):
    shape = QUOTED.sub('""', step)
    for pattern, fn in handlers:
        m = pattern.search(shape)
        if m:
            return fn, m
    return None, None


def _test_methods(cases, make_body, kind):
    """One TestNG @Test method per test case, run in the order they were written."""
    out, used = [], set()
    for n, (title, steps) in enumerate(cases, 1):
        name = method_name(title) if title else f"testCase{n}"
        base, k = name, 2
        while name in used:
            name, k = f"{base}{k}", k + 1
        used.add(name)
        out.append(f"    @Test(priority = {n}, description = {jlit(title or f'{kind} test {n}')})\n"
                   f"    public void {name}() {{\n{_indent(make_body(steps), 8)}\n    }}")
    return "\n\n".join(out)


def _todo(step, why):
    return [f"// TODO: write this step in Java ({why})", f"fail({jlit('Step not converted to Java: ' + step)});"]


# ================================================================ WEB (Selenium)

KEYS = {"enter": "ENTER", "return": "RETURN", "tab": "TAB", "escape": "ESCAPE", "esc": "ESCAPE",
        "space": "SPACE", "backspace": "BACK_SPACE", "delete": "DELETE", "up": "ARROW_UP", "down": "ARROW_DOWN",
        "left": "ARROW_LEFT", "right": "ARROW_RIGHT", "home": "HOME", "end": "END", "pageup": "PAGE_UP",
        "pagedown": "PAGE_DOWN"}


def _web_step(step, m, fn, base, idx):
    w = web_runner
    q = quoted(step)
    L = jlit
    if fn is w.h_open:
        target = q[0] if q else m.group("rest").strip()
        url = target if re.match(r"^https?://", target) else urljoin(base, target)
        return [f"driver.get({L(url)});"]
    if fn is w.h_clear:
        return [f"field({L(q[0])}).clear();"]
    if fn is w.h_type:
        return [f"fill(field({L(q[1])}), {L(q[0])});"]
    if fn is w.h_select:
        return [f"new Select(field({L(q[1])})).selectByVisibleText({L(q[0])});"]
    if fn is w.h_check_step:
        on = not step.lower().startswith("uncheck")
        return [f"setChecked(element({L(q[0])}), {'true' if on else 'false'});"]
    if fn is w.h_click:
        if not q:
            return ["new Actions(driver).sendKeys(Keys.ENTER).perform(); // submit the form"]
        if re.match(r"\s*double", step, re.I):
            return [f"new Actions(driver).doubleClick(clickable({L(q[0])})).perform();"]
        return [f"clickable({L(q[0])}).click();"]
    if fn is w.h_hover:
        return [f"new Actions(driver).moveToElement(element({L(q[0])})).perform();"]
    if fn is w.h_press:
        key = q[0] if q else m.group("key")
        k = KEYS.get(key.lower().replace(" ", ""))
        return [f"new Actions(driver).sendKeys({'Keys.' + k if k else L(key)}).perform();"]
    if fn is w.h_wait_seconds:
        return [f"pause({int(float(m.group('n')) * 1000)});"]
    if fn is w.h_wait_for:
        return [f"waitForText({L(q[0])});"]
    if fn is w.h_scroll:
        d = m.group("dir").lower()
        js = {"down": "window.scrollBy(0, 600)", "up": "window.scrollBy(0, -600)", "top": "window.scrollTo(0, 0)",
              "bottom": "window.scrollTo(0, document.body.scrollHeight)"}[d]
        return [f"((JavascriptExecutor) driver).executeScript({L(js)});"]
    if fn is w.h_back:
        return ["driver.navigate().back();"]
    if fn is w.h_refresh:
        return ["driver.navigate().refresh();"]
    if fn is w.h_screenshot:
        return [f'screenshot("step-{idx}");']
    if fn is w.h_verify_title:
        text = q[0]
        exact = bool(re.search(r"\b(is|equals?)\b", step, re.I)) and "contain" not in step.lower()
        if exact:
            return [f"assertEquals(driver.getTitle().trim().toLowerCase(), {L(text.lower())}, \"Page title\");"]
        return [f"assertTrue(driver.getTitle().toLowerCase().contains({L(text.lower())}),",
                f"        {L('Title should contain ' + chr(34) + text + chr(34) + ' but was ')} + driver.getTitle());"]
    if fn is w.h_verify_url:
        return [f"assertTrue(driver.getCurrentUrl().toLowerCase().contains({L(q[0].lower())}),",
                f"        {L('URL should contain ' + chr(34) + q[0] + chr(34) + ' but was ')} + driver.getCurrentUrl());"]
    if fn is w.h_verify_field_value:
        return [f"assertEquals(field({L(q[0])}).getDomProperty(\"value\"), {L(q[1])});"]
    if fn is w.h_count_step:
        n = int(re.search(r"(\d+)", QUOTED.sub("", step)).group(1))
        by = f"By.xpath({L(q[0])})" if q[0].startswith("//") else f"By.cssSelector({L(q[0])})"
        return [f"assertEquals(driver.findElements({by}).size(), {n}, {L('Number of ' + q[0])});"]
    if fn is w.h_verify_not_contains:
        return [f"assertPageNotContains({L(q[0])});"]
    if fn is w.h_verify_visible:
        return [f"assertPageContains({L(q[0])});"]
    return _todo(step, "no Java rule for it")


def web_java(cases, target_url, cls, title):
    def make(steps):
        body = [f"driver.get({jlit(target_url)});", ""]
        if steps and web_runner.HANDLERS[0][0].search(QUOTED.sub('""', steps[0])):
            body = []
        for i, step in enumerate(steps, 1):
            body.append(f"// {i}. {step}")
            fn, m = _classify(step, web_runner.HANDLERS)
            try:
                body += _web_step(step, m, fn, target_url, i) if fn else _todo(step, "Testlingo did not understand it")
            except (IndexError, AttributeError, ValueError):
                body += _todo(step, "put the names and values in \"quotes\"")
            body.append("")
        return body[:-1] if body and body[-1] == "" else body
    return f"""package testlingo;

import org.openqa.selenium.By;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.Keys;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.Select;
import org.testng.annotations.Test;

import static org.testng.Assert.*;

/**
 * {title or 'Web test'}
 * Generated by Testlingo from a plain-English test case (Selenium + TestNG).
 * Run it with:  mvn test   (add -Dheaded=true to watch the browser)
 */
public class {cls} extends BaseWebTest {{

{_test_methods(cases, make, 'Web')}
}}
"""


# ================================================================ MOBILE (Appium)

def _mobile_step(step, m, fn, idx):
    mo = mobile_runner
    q = quoted(step)
    L = jlit
    if fn is mo.h_launch:
        return ["// the app was started in startApp()"]
    if fn is mo.h_hide_kb:
        return ["hideKeyboard();"]
    if fn is mo.h_clear:
        return [f"field({L(q[0])}).clear();"]
    if fn is mo.h_type:
        return [f"fill(field({L(q[1])}), {L(q[0])});"]
    if fn is mo.h_long:
        return [f"longPress(find({L(q[0])}));"]
    if fn is mo.h_back:
        return ["driver.navigate().back();"]
    if fn is mo.h_tap:
        return [f"find({L(q[0])}).click();"]
    if fn is mo.h_swipe:
        return [f"swipe({L(m.group('dir').lower())});"]
    if fn is mo.h_scroll_to:
        return [f"scrollTo({L(q[0])});"]
    if fn is mo.h_wait_seconds:
        return [f"pause({int(float(m.group('n')) * 1000)});"]
    if fn is mo.h_wait_for:
        return [f"find({L(q[0])}, 20); // wait until it appears"]
    if fn is mo.h_rotate:
        o = "LANDSCAPE" if "landscape" in step.lower() else "PORTRAIT"
        return [f"rotate(ScreenOrientation.{o});"]
    if fn is mo.h_background:
        n = re.search(r"(\d+)", step)
        return [f"runInBackground({int(n.group(1)) if n else 3});"]
    if fn is mo.h_screenshot:
        return [f'screenshot("step-{idx}");']
    if fn is mo.h_verify_not:
        return [f"assertNotDisplayed({L(q[0])});"]
    if fn is mo.h_verify:
        if len(q) >= 2 and re.search(r"(has|have) (text|value)", step, re.I):
            return [f"assertTrue(find({L(q[0])}).getText().toLowerCase().contains({L(q[1].lower())}),",
                    f"        {L(q[0] + ' should show ' + q[1])});"]
        return [f"assertDisplayed({L(q[0])});"]
    return _todo(step, "no Java rule for it")


def mobile_java(cases, settings, cls, title):
    ios = settings.get("platform", "Android").lower() == "ios"
    opts = "XCUITestOptions" if ios else "UiAutomator2Options"
    server = settings.get("appium_url") or "http://127.0.0.1:4723"
    cloud = "browserstack" in server.lower()
    o = [f"{opts} options = new {opts}();",
         f"options.setDeviceName({jlit(settings.get('device_name') or ('iPhone 15' if ios else 'Android Emulator'))});"]
    if settings.get("os_version"):
        o.append(f"options.setPlatformVersion({jlit(settings['os_version'])});")
    app = settings.get("app") or ""
    if app:
        if settings.get("app_was_uploaded") and not app.startswith("bs://"):
            o.append(f"options.setApp(System.getProperty(\"app\", {jlit(settings.get('app_name') or 'path/to/your-app')}));"
                     " // full path to the app file")
        else:
            o.append(f"options.setApp(System.getProperty(\"app\", {jlit(app)}));")
    if not ios and settings.get("app_package"):
        o.append(f"options.setAppPackage({jlit(settings['app_package'])});")
        if settings.get("app_activity"):
            o.append(f"options.setAppActivity({jlit(settings['app_activity'])});")
    if ios and settings.get("bundle_id"):
        o.append(f"options.setBundleId({jlit(settings['bundle_id'])});")
    if not ios:
        o.append("options.setAutoGrantPermissions(true);")
    o.append("options.setNewCommandTimeout(Duration.ofSeconds(120));")
    if cloud:
        o += ["",
              "// BrowserStack: set BROWSERSTACK_USERNAME and BROWSERSTACK_ACCESS_KEY as environment variables",
              "Map<String, Object> browserstack = new HashMap<>();",
              "browserstack.put(\"userName\", System.getenv(\"BROWSERSTACK_USERNAME\"));",
              "browserstack.put(\"accessKey\", System.getenv(\"BROWSERSTACK_ACCESS_KEY\"));",
              "browserstack.put(\"projectName\", \"Testlingo\");",
              f"browserstack.put(\"sessionName\", {jlit(title or 'Testlingo run')});",
              "options.setCapability(\"bstack:options\", browserstack);"]
        server_default = "https://hub-cloud.browserstack.com/wd/hub"
    else:
        server_default = re.sub(r"//[^/@]+@", "//", server)
    o.append("return options;")

    def make(steps):
        body = []
        for i, step in enumerate(steps, 1):
            body.append(f"// {i}. {step}")
            fn, m = _classify(step, mobile_runner.HANDLERS)
            try:
                body += _mobile_step(step, m, fn, i) if fn else _todo(step, "Testlingo did not understand it")
            except (IndexError, AttributeError, ValueError):
                body += _todo(step, "put the names and values in \"quotes\"")
            body.append("")
        return body[:-1] if body and body[-1] == "" else body
    imp = "io.appium.java_client.ios.options.XCUITestOptions" if ios else "io.appium.java_client.android.options.UiAutomator2Options"
    return f"""package testlingo;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

import {imp};
import io.appium.java_client.remote.options.BaseOptions;
import org.openqa.selenium.ScreenOrientation;
import org.testng.annotations.Test;

import static org.testng.Assert.*;

/**
 * {title or 'Mobile test'}
 * Generated by Testlingo from a plain-English test case (Appium java-client + TestNG).
 * Start Appium (or use a cloud device service), then run:  mvn test
 * Use another app file with:  mvn test -Dapp=/full/path/to/app.apk
 */
public class {cls} extends BaseMobileTest {{

    @Override
    protected BaseOptions<?> options() {{
{_indent(o, 8)}
    }}

    @Override
    protected String serverUrl() {{
        return System.getProperty("appium.url", {jlit(server_default)});
    }}

{_test_methods(cases, make, 'Mobile')}
}}
"""


# ================================================================ API (RestAssured)

def jpath(p):
    """Testlingo field path (data.0.email, $.data[0].email) -> RestAssured GPath (data[0].email)."""
    p = re.sub(r"^\$\.?", "", p.strip())
    out = ""
    for t in [t for t in re.split(r"\.|\[|\]", p) if t != ""]:
        out += f"[{t}]" if t.isdigit() else (("." if out else "") + t)
    return out or "$"


def _api_send(method, path_tpl, body_tpl, params, declared, base):
    lines = ["resp = given().spec(spec).headers(headers)"]
    for k, v in params.items():
        lines.append(f"        .queryParam({jlit(k)}, {jexpr(v, declared)})")
    if body_tpl is not None:
        try:
            json.loads(re.sub(r"\{\{\s*\w+\s*\}\}", "0", body_tpl))
            lines.append("        .contentType(ContentType.JSON)")
        except ValueError:
            pass
        lines.append(f"        .body({jexpr(body_tpl, declared)})")
    path = path_tpl if path_tpl is not None else ""
    verb = method.lower()
    call = f".{verb}({jexpr(path, declared)})" if verb in ("get", "post", "put", "patch", "delete", "head", "options") \
        else f".request({jlit(method)}, {jexpr(path, declared)})"
    lines.append(f"    .when(){call}")
    lines.append("    .then().extract().response();")
    return lines


def _api_step(step, raw, m, fn, st, base):
    """st: generation state (declared vars, pending params/body)."""
    a = api_runner
    q = quoted(raw)  # templates keep {{vars}}
    L = jlit
    D = st["declared"]
    shape = QUOTED.sub('""', step).lower()
    if fn is a._send_router:
        sh = re.sub(r'"[^"]*"|\'[^\']*\'|\{.*\}|\[.*\]', '', step)
        method = re.search(rf"\b({a.METHODS})\b", sh, re.I).group(1).upper()
        path = next((x for x in q if x.startswith(("/", "http", "{{"))), None)
        body = a.extract_json(raw) if re.search(r"\b(body|payload|data|json)\b", step, re.I) else None
        pending = st.pop("body", None)
        lines = _api_send(method, path, body if body is not None else pending, st.pop("params", {}), D, base)
        st["sent"] = True
        return lines
    if fn is a.h_token:
        return [f"headers.put(\"Authorization\", {jexpr('Bearer ' + q[0], D)});"]
    if fn is a.h_header:
        if len(q) >= 2:
            k, v = q[0], q[1]
        else:
            k, _, v = q[0].partition(":")
        return [f"headers.put({L(k.strip())}, {jexpr(v.strip(), D)});"]
    if fn is a.h_param:
        st.setdefault("params", {})[q[0]] = q[1]
        return [f"// query parameter {q[0]}={q[1]} is added to the next request"]
    if fn is a.h_body:
        st["body"] = a.extract_json(raw) or q[0]
        return ["// this body is sent with the next request"]
    if fn is a.h_save:
        name = _ident(q[1] if len(q) > 1 else q[0].split(".")[-1])
        decl = "" if name in D else "String "
        D.add(name)
        return [f"{decl}{name} = resp.jsonPath().getString({L(jpath(q[0]))});"]
    if fn is a.h_status:
        codes = [int(c) for c in re.findall(r"\b([1-5]\d\d)\b", step)]
        if not codes:
            return ["assertTrue(resp.statusCode() >= 200 && resp.statusCode() < 300,",
                    "        \"Expected a 2xx success status, got \" + resp.statusCode());"]
        if len(codes) == 1:
            return [f"assertEquals(resp.statusCode(), {codes[0]}, \"Status code\");"]
        return [f"assertTrue(List.of({', '.join(map(str, codes))}).contains(resp.statusCode()),",
                "        \"Unexpected status \" + resp.statusCode());"]
    if fn is a.h_time:
        limit = int(re.search(r"(\d+)", step.replace(",", "")).group(1))
        if re.search(r"\bsec", step, re.I) and not re.search(r"\bms\b|millis", step, re.I):
            limit *= 1000
        return [f"assertTrue(resp.time() < {limit}, \"Response took \" + resp.time() + \" ms\");"]
    if fn is a.h_resp_header:
        name = q[0]
        lines = [f"assertNotNull(resp.header({L(name)}), {L('Header ' + name + ' is missing')});"]
        if len(q) > 1:
            lines.append(f"assertTrue(resp.header({L(name)}).toLowerCase().contains({L(q[1].lower())}));")
        return lines
    if fn is a.h_field:
        p = L(jpath(q[0]))
        get = f"resp.jsonPath().get({p})"
        gets = f"resp.jsonPath().getString({p})"
        if re.search(r"not exist|is missing|absent", shape):
            return [f"assertNull({get}, {L(q[0] + ' should not exist')});"]
        count = re.search(r"\b(has|have|contains?|with)\s+(\d+)\s+(items?|elements?|entries|records?)", shape)
        if count:
            return [f"assertEquals(sizeOf({get}), {int(count.group(2))}, {L('Items in ' + q[0])});"]
        if re.search(r"not (be )?empty|is not null|not null", shape):
            return [f"assertFalse(isEmpty({get}), {L(q[0] + ' is empty')});"]
        if re.search(r"\b(is|be) (a )?(number|integer)\b", shape):
            return [f"assertTrue({get} instanceof Number, {L(q[0] + ' should be a number')});"]
        if re.search(r"\b(is|be) (a |an )?(string|text)\b", shape):
            return [f"assertTrue({get} instanceof String, {L(q[0] + ' should be text')});"]
        if re.search(r"\b(is|be) (a |an )?(list|array)\b", shape):
            return [f"assertTrue({get} instanceof java.util.List, {L(q[0] + ' should be a list')});"]
        if len(q) >= 2:
            exp = jexpr(q[1], D)
            if re.search(r"contain", shape):
                return [f"assertTrue(String.valueOf((Object) {get}).toLowerCase().contains({jlower(q[1], D)}),",
                        f"        {L(q[0] + ' should contain ' + q[1])});"]
            if re.search(r"not (equal|be)|is not|isn't|!=", shape):
                return [f"assertNotEquals({gets}, {exp});"]
            return [f"assertEquals({gets}, {exp}, {L(q[0])});"]
        return [f"assertNotNull({get}, {L(q[0] + ' should exist')});"]
    if fn is a.h_array_len:
        n = int(re.search(r"(\d+)", QUOTED.sub("", step)).group(1))
        return [f"assertEquals(resp.jsonPath().getList(\"$\").size(), {n}, \"Items in the response\");"]
    if fn is a.h_json:
        return ["assertNotNull(resp.jsonPath().get(\"$\"), \"Response should be JSON\");"]
    if fn is a.h_contains:
        neg = re.search(r"not contain|doesn't contain|does not contain", step, re.I)
        check = f"resp.asString().toLowerCase().contains({jlower(q[0], D)})"
        return [f"assert{'False' if neg else 'True'}({check},",
                f"        {L('Response ' + ('should not contain ' if neg else 'should contain ') + q[0])});"]
    return _todo(step, "no Java rule for it")


def api_java(cases, target, method, headers, body, cls, title):
    def make(raw_steps):
        st = {"declared": set()}  # every test case starts fresh, like each Testlingo run
        send_re = api_runner.HANDLERS[0][0]
        steps_have_send = any(send_re.search(re.sub(r'"[^"]*"', '""', s)) for s in raw_steps)
        body_lines = ["Response resp;", ""]
        if not steps_have_send:
            body_lines.append(f"// Send the request that was pasted: {method} {target}")
            body_lines += _api_send(method.upper(), None, body, {}, st["declared"], target)
            body_lines.append("")
        elif body:
            st["body"] = body
        for i, raw in enumerate(raw_steps, 1):
            body_lines.append(f"// {i}. {raw}")
            fn, m = _classify(raw, api_runner.HANDLERS)
            try:
                body_lines += _api_step(raw, raw, m, fn, st, target) if fn else _todo(raw, "Testlingo did not understand it")
            except (IndexError, AttributeError, ValueError):
                body_lines += _todo(raw, "put the names and values in \"quotes\"")
            body_lines.append("")
        return body_lines[:-1] if body_lines and body_lines[-1] == "" else body_lines
    header_lines = [f"headers.put({jlit(k)}, {jlit(v)});" for k, v in (headers or {}).items()]
    return f"""package testlingo;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import io.restassured.RestAssured;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.filter.log.RequestLoggingFilter;
import io.restassured.filter.log.ResponseLoggingFilter;
import io.restassured.http.ContentType;
import io.restassured.response.Response;
import io.restassured.specification.RequestSpecification;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static io.restassured.RestAssured.given;
import static org.testng.Assert.*;

/**
 * {title or 'API test'}
 * Generated by Testlingo from a plain-English test case (RestAssured + TestNG).
 * Run it with:  mvn test
 * Point it at another server:  mvn test -DbaseUri=https://staging.example.com/api
 * Print every request and response:  mvn test -Dlog=true
 */
public class {cls} {{

    private static final String BASE_URI = System.getProperty("baseUri", {jlit(target)});

    private RequestSpecification spec;
    private final Map<String, Object> headers = new LinkedHashMap<>();

    @BeforeMethod
    public void setUp() {{
        if (Boolean.getBoolean("log")) {{
            RestAssured.filters(new RequestLoggingFilter(), new ResponseLoggingFilter());
        }}
        spec = new RequestSpecBuilder().setBaseUri(BASE_URI).build();
        headers.clear();
{_indent(header_lines, 8) if header_lines else ''}
    }}

{_test_methods(cases, make, 'API')}

    private static boolean isEmpty(Object value) {{
        return value == null || String.valueOf(value).isEmpty()
                || (value instanceof java.util.Collection<?> c && c.isEmpty())
                || (value instanceof Map<?, ?> m && m.isEmpty());
    }}

    private static int sizeOf(Object value) {{
        if (value instanceof java.util.Collection<?> c) return c.size();
        if (value instanceof Map<?, ?> m) return m.size();
        return -1;
    }}
}}
"""


# ================================================================ project files

def _indent(lines, n):
    pad = " " * n
    out = "\n".join((pad + ln) if ln.strip() else "" for ln in lines)
    return out.rstrip("\n")


def pom(platform):
    v = VERSIONS
    deps = {
        "web": f"""        <dependency>
            <groupId>org.seleniumhq.selenium</groupId>
            <artifactId>selenium-java</artifactId>
            <version>{v['selenium']}</version>
        </dependency>""",
        "mobile": f"""        <!-- brings the matching Selenium version with it -->
        <dependency>
            <groupId>io.appium</groupId>
            <artifactId>java-client</artifactId>
            <version>{v['appium']}</version>
        </dependency>""",
        "api": f"""        <dependency>
            <groupId>io.rest-assured</groupId>
            <artifactId>rest-assured</artifactId>
            <version>{v['restassured']}</version>
            <scope>test</scope>
        </dependency>""",
    }[platform]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>testlingo</groupId>
    <artifactId>testlingo-{platform}-tests</artifactId>
    <version>1.0.0</version>
    <name>Testlingo {platform} tests</name>

    <properties>
        <maven.compiler.release>17</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
{deps}
        <dependency>
            <groupId>org.testng</groupId>
            <artifactId>testng</artifactId>
            <version>{v['testng']}</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.13.0</version>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
                <configuration>
                    <suiteXmlFiles>
                        <suiteXmlFile>testng.xml</suiteXmlFile>
                    </suiteXmlFiles>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
"""


def testng_xml(cls, platform):
    return f"""<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Testlingo {platform} tests">
    <test name="{cls}">
        <classes>
            <class name="testlingo.{cls}"/>
        </classes>
    </test>
</suite>
"""


README = {
    "web": """# {cls} (Selenium + TestNG)

Generated by Testlingo.

## Run
Needs Java 17+, Maven and Google Chrome. Selenium downloads the right ChromeDriver by itself.

```
mvn test                    # headless
mvn test -Dheaded=true      # watch the browser
```
Screenshots go to `target/screenshots/` (one is saved automatically when a test fails).
Reports: `target/surefire-reports/`.

## Files
- `src/test/java/testlingo/{cls}.java` - your test, one block per plain-English step
- `src/test/java/testlingo/BaseWebTest.java` - browser set-up and the smart finders
  `field("Username")`, `clickable("Login")`, `element("...")`

Add more tests by copying the class, or add methods with `@Test` to it.
""",
    "mobile": """# {cls} (Appium java-client + TestNG)

Generated by Testlingo.

## Run on your own device / emulator
1. `npm install -g appium` then `appium driver install uiautomator2` (Android) or `xcuitest` (iOS)
2. Start an emulator / simulator or connect a phone, then start the server: `appium`
3. `mvn test -Dapp=/full/path/to/your-app.apk`

## Run on BrowserStack
```
export BROWSERSTACK_USERNAME=your_username
export BROWSERSTACK_ACCESS_KEY=your_key
mvn test -Dappium.url=https://hub-cloud.browserstack.com/wd/hub -Dapp=bs://your-app-id
```
(Add the BrowserStack block to `options()` if this test was generated for a local device.)

## Files
- `src/test/java/testlingo/{cls}.java` - device settings in `options()` and your steps
- `src/test/java/testlingo/BaseMobileTest.java` - driver set-up, `find(...)`, `swipe(...)` and other helpers
""",
    "api": """# {cls} (RestAssured + TestNG)

Generated by Testlingo.

## Run
Needs Java 17+ and Maven.

```
mvn test
mvn test -DbaseUri=https://staging.example.com/api   # another server
mvn test -Dlog=true                                  # print every request and response
```
Reports: `target/surefire-reports/`.

## Files
- `src/test/java/testlingo/{cls}.java` - the whole test: one block per plain-English step.
  Values saved with "Save response field ... as ..." become Java variables.
""",
}


def build(platform, cases, suite_title=None, **kw):
    """Return {class_name, code, files, zip_b64} for a run of one or more test cases.

    cases: list of (title, steps). Each test case becomes one @Test method of the same class.
    """
    title = suite_title or (cases[0][0] if len(cases) == 1 else None)
    cls = class_name(title or f"{platform} suite" if len(cases) > 1 else title, platform)
    if platform == "web":
        code = web_java(cases, kw["target"], cls, title)
        extra = {"src/test/java/testlingo/BaseWebTest.java": _read("BaseWebTest.java")}
    elif platform == "mobile":
        code = mobile_java(cases, kw["settings"], cls, title)
        extra = {"src/test/java/testlingo/BaseMobileTest.java": _read("BaseMobileTest.java")}
    else:
        code = api_java(cases, kw["target"], kw.get("method", "GET"), kw.get("headers"), kw.get("body"), cls, title)
        extra = {}
    files = {
        "pom.xml": pom(platform),
        "testng.xml": testng_xml(cls, platform),
        "README.md": README[platform].replace("{cls}", cls),
        f"src/test/java/testlingo/{cls}.java": code,
        **extra,
    }
    root = f"testlingo-{platform}-java"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files.items():
            z.writestr(f"{root}/{path}", text)
    return {"class_name": cls, "code": code, "files": sorted(files), "zip_name": f"{root}.zip",
            "zip_b64": base64.b64encode(buf.getvalue()).decode()}


def _read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()
