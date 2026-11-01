"""API runner: plain-English steps -> HTTP requests (requests library)."""
import json
import re
import shlex
import time
from urllib.parse import urljoin

import requests

from . import netguard
from .common import quoted, run_steps, split_steps, summary

UNKNOWN = ('I don\'t understand this step. Try: Send GET request to "/users", '
           'Verify status code is 200, Verify response field "data.0.id" equals "1".')
METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"


# ---------------- helpers ----------------

def parse_curl(text):
    """Turn a pasted cURL command into method, url, headers, body."""
    parts = shlex.split(text.replace("\\\n", " "))
    method, url, headers, body = None, None, {}, None
    i = 1
    while i < len(parts):
        a = parts[i]
        if a in ("-X", "--request"):
            method = parts[i + 1].upper(); i += 1
        elif a in ("-H", "--header"):
            k, _, v = parts[i + 1].partition(":"); headers[k.strip()] = v.strip(); i += 1
        elif a in ("-d", "--data", "--data-raw", "--data-binary", "--json"):
            body = parts[i + 1]; i += 1
            if a == "--json":
                headers.setdefault("Content-Type", "application/json")
        elif a in ("-u", "--user"):
            import base64
            headers["Authorization"] = "Basic " + base64.b64encode(parts[i + 1].encode()).decode(); i += 1
        elif not a.startswith("-") and url is None:
            url = a
        i += 1
    return method or ("POST" if body else "GET"), url, headers, body


def extract_json(step):
    """Find the first {...} or [...] JSON block in a step."""
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = step.find(open_c)
        if start == -1:
            continue
        depth = 0
        for j in range(start, len(step)):
            depth += step[j] == open_c
            depth -= step[j] == close_c
            if depth == 0:
                return step[start:j + 1]
    return None


def get_path(data, path):
    """Read a value with a path like data.0.email, data[0].email or $.data[0].email."""
    path = re.sub(r"^\$\.?", "", path.strip())
    tokens = [t for t in re.split(r"\.|\[|\]", path) if t != ""]
    cur = data
    for t in tokens:
        if isinstance(cur, list):
            try:
                cur = cur[int(t)]
            except (ValueError, IndexError):
                raise AssertionError(f'Field "{path}" not found (no item {t})')
        elif isinstance(cur, dict):
            if t not in cur:
                raise AssertionError(f'Field "{path}" not found in the response')
            cur = cur[t]
        else:
            raise AssertionError(f'Field "{path}" not found in the response')
    return cur


def _coerce(expected, actual):
    """Compare loosely: "1" equals 1, "true" equals True."""
    if isinstance(actual, bool):
        return expected.lower() == str(actual).lower()
    if isinstance(actual, (int, float)):
        try:
            return float(expected) == float(actual)
        except ValueError:
            return False
    if actual is None:
        return expected.lower() in ("null", "none")
    return str(actual) == expected


def _json(ctx):
    resp = ctx.get("resp")
    if resp is None:
        raise AssertionError("No request has been sent yet")
    try:
        return resp.json()
    except ValueError:
        raise AssertionError("Response body is not JSON")


def _short(v):
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:77] + "..."


# ---------------- step handlers ----------------

def h_send(ctx, m, step):
    method = m.group("method").upper()
    q = quoted(step)
    path = next((x for x in q if x.startswith(("/", "http"))), None)
    url = ctx["base"] if not path else (path if path.startswith("http") else urljoin(ctx["base"].rstrip("/") + "/", path.lstrip("/")))
    body_txt = extract_json(step) if re.search(r"\b(body|payload|data|json)\b", step, re.I) else None
    body = ctx.pop("next_body", None)
    if body_txt:
        body = body_txt
    return _do_request(ctx, method, url, body, step)


def _do_request(ctx, method, url, body, step=""):
    headers = dict(ctx["headers"])
    params = dict(ctx.pop("next_params", {}))
    kwargs = {"headers": headers, "params": params, "timeout": 30}
    if body is not None:
        try:
            kwargs["json"] = json.loads(body) if isinstance(body, str) else body
        except ValueError:
            kwargs["data"] = body
    t0 = time.time()
    resp = _request_guarded(method, url, kwargs)
    elapsed = int((time.time() - t0) * 1000)
    ctx["resp"], ctx["elapsed"] = resp, elapsed
    try:
        pretty = json.dumps(resp.json(), indent=2)
    except ValueError:
        pretty = resp.text[:20000]
    record = {
        "method": method, "url": resp.request.url, "status": resp.status_code, "reason": resp.reason,
        "time_ms": elapsed, "size": len(resp.content),
        "request_headers": headers, "request_body": body if isinstance(body, str) else (json.dumps(body) if body else None),
        "response_headers": dict(resp.headers), "response_body": pretty,
    }
    ctx["requests"].append(record)
    code = f'resp = requests.{method.lower()}("{url}"'
    if headers:
        code += f", headers={json.dumps(headers)}"
    if params:
        code += f", params={json.dumps(params)}"
    if "json" in kwargs:
        code += f", json={json.dumps(kwargs['json'])}"
    code += ")"
    return f"{method} {resp.request.url} → {resp.status_code} {resp.reason} in {elapsed} ms", code, {"request": record}


def _request_guarded(method, url, kwargs):
    """Send the request, checking every redirect hop when private networks are blocked."""
    if not netguard.ENABLED:
        return requests.request(method, url, **kwargs)
    for _ in range(6):
        try:
            netguard.check_url(url)
        except netguard.BlockedTarget as e:
            raise AssertionError(str(e))
        resp = requests.request(method, url, allow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            url = urljoin(url, resp.headers["Location"])
            if resp.status_code in (301, 302, 303) and method != "HEAD":
                method = "GET"
                kwargs = {k: v for k, v in kwargs.items() if k not in ("json", "data")}
            continue
        return resp
    raise AssertionError("Too many redirects")


def h_header(ctx, m, step):
    q = quoted(step)
    if len(q) >= 2:
        k, v = q[0], q[1]
    elif q and ":" in q[0]:
        k, _, v = q[0].partition(":")
    else:
        raise ValueError('Use: Set header "Authorization" to "Bearer abc"')
    ctx["headers"][k.strip()] = v.strip()
    return f"Header {k.strip()} set", f'headers["{k.strip()}"] = "{v.strip()}"'


def h_token(ctx, m, step):
    token = quoted(step)[0]
    ctx["headers"]["Authorization"] = f"Bearer {token}"
    return "Bearer token set", f'headers["Authorization"] = "Bearer {token}"'


def h_param(ctx, m, step):
    q = quoted(step)
    ctx.setdefault("next_params", {})[q[0]] = q[1]
    return f"Query parameter {q[0]}={q[1]}", f'params["{q[0]}"] = "{q[1]}"'


def h_body(ctx, m, step):
    body = extract_json(step) or (quoted(step) or [None])[0]
    if body is None:
        raise ValueError("Put the JSON body on the same line, e.g. Set body {\"name\": \"Ann\"}")
    ctx["next_body"] = body
    return "Request body set", f"body = {body}"


def h_status(ctx, m, step):
    resp = ctx.get("resp")
    if resp is None:
        raise AssertionError("No request has been sent yet")
    codes = [int(c) for c in re.findall(r"\b([1-5]\d\d)\b", step)]
    if not codes:
        if re.search(r"success|ok\b", step, re.I):
            assert 200 <= resp.status_code < 300, f"Expected a 2xx success status, got {resp.status_code}"
            return f"Status {resp.status_code} is a success", "assert 200 <= resp.status_code < 300"
        raise ValueError("Say which status code, e.g. Verify status code is 200")
    assert resp.status_code in codes, f"Expected status {' or '.join(map(str, codes))}, got {resp.status_code} {resp.reason}"
    return f"Status is {resp.status_code}", f"assert resp.status_code == {codes[0]}"


def h_time(ctx, m, step):
    limit = int(re.search(r"(\d+)", step.replace(",", "")).group(1))
    if re.search(r"\bsec", step, re.I) and not re.search(r"\bms\b|millis", step, re.I):
        limit *= 1000
    assert ctx["elapsed"] < limit, f"Response took {ctx['elapsed']} ms, expected under {limit} ms"
    return f"Responded in {ctx['elapsed']} ms (limit {limit} ms)", f"assert resp.elapsed.total_seconds() * 1000 < {limit}"


def h_resp_header(ctx, m, step):
    q = quoted(step)
    name, expected = q[0], q[1] if len(q) > 1 else None
    actual = ctx["resp"].headers.get(name)
    assert actual is not None, f'Header "{name}" is missing from the response'
    if expected:
        assert expected.lower() in actual.lower(), f'Header "{name}" is "{actual}", expected it to contain "{expected}"'
    return f'Header {name}: {actual}', f'assert "{expected or ""}" in resp.headers["{name}"]'


def h_field(ctx, m, step):
    data = _json(ctx)
    q = quoted(step)
    path = q[0]
    shape = re.sub(r'"[^"]*"|\'[^\']*\'', '""', step).lower()
    if re.search(r"not exist|is missing|absent", shape):
        try:
            get_path(data, path)
        except AssertionError:
            return f'Field "{path}" is absent', f'assert "{path}" not in data'
        raise AssertionError(f'Field "{path}" should not exist, but it does')
    value = get_path(data, path)
    count = re.search(r"\b(has|have|contains?|with)\s+(\d+)\s+(items?|elements?|entries|records?)", shape)
    if count:
        n = int(count.group(2))
        assert isinstance(value, (list, dict)) and len(value) == n, f'Expected "{path}" to have {n} items, it has {len(value) if hasattr(value, "__len__") else "no"} items'
        return f'"{path}" has {n} items', f'assert len(data{_py(path)}) == {n}'
    if re.search(r"not (be )?empty|is not null|not null", shape):
        assert value not in (None, "", [], {}), f'Field "{path}" is empty'
        return f'"{path}" = {_short(value)}', f"assert data{_py(path)}"
    if re.search(r"\b(is|be) (a )?(number|integer)\b", shape):
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f'"{path}" is {type(value).__name__}, expected a number'
        return f'"{path}" is a number ({value})', f"assert isinstance(data{_py(path)}, (int, float))"
    if re.search(r"\b(is|be) (a |an )?(string|text)\b", shape):
        assert isinstance(value, str), f'"{path}" is {type(value).__name__}, expected text'
        return f'"{path}" is text', f"assert isinstance(data{_py(path)}, str)"
    if re.search(r"\b(is|be) (a |an )?(list|array)\b", shape):
        assert isinstance(value, list), f'"{path}" is {type(value).__name__}, expected a list'
        return f'"{path}" is a list of {len(value)}', f"assert isinstance(data{_py(path)}, list)"
    if len(q) >= 2:
        expected = q[1]
        if re.search(r"contain", shape):
            assert expected.lower() in json.dumps(value).lower(), f'"{path}" is {_short(value)}, expected it to contain "{expected}"'
            return f'"{path}" contains "{expected}"', f'assert "{expected}" in str(data{_py(path)})'
        if re.search(r"not (equal|be)|is not|isn't|!=", shape):
            assert not _coerce(expected, value), f'"{path}" should not be "{expected}"'
            return f'"{path}" is not "{expected}"', f'assert data{_py(path)} != "{expected}"'
        assert _coerce(expected, value), f'Expected "{path}" to be "{expected}", actual {_short(value)}'
        return f'"{path}" = {_short(value)}', f"assert data{_py(path)} == {json.dumps(value)}"
    # plain "exists"
    return f'Field "{path}" exists = {_short(value)}', f'assert data{_py(path)} is not None'


def _py(path):
    path = re.sub(r"^\$\.?", "", path)
    out = ""
    for t in [t for t in re.split(r"\.|\[|\]", path) if t]:
        out += f"[{t}]" if t.isdigit() else f'["{t}"]'
    return out


def h_array_len(ctx, m, step):
    data = _json(ctx)
    n = int(re.search(r"(\d+)", step).group(1))
    assert isinstance(data, list) and len(data) == n, f"Expected the response to be a list of {n}, got {len(data) if isinstance(data, list) else type(data).__name__}"
    return f"Response has {n} items", f"assert len(data) == {n}"


def h_contains(ctx, m, step):
    text = quoted(step)[0]
    body = ctx["resp"].text
    negative = re.search(r"not contain|doesn't contain|does not contain", step, re.I)
    found = text.lower() in body.lower()
    if negative:
        assert not found, f'Response should not contain "{text}", but it does'
        return f'Response does not contain "{text}"', f'assert "{text}" not in resp.text'
    assert found, f'Response does not contain "{text}"'
    return f'Response contains "{text}"', f'assert "{text}" in resp.text'


def h_json(ctx, m, step):
    _json(ctx)
    return "Response is valid JSON", "data = resp.json()"


def h_save(ctx, m, step):
    q = quoted(step)
    path, name = q[0], q[1] if len(q) > 1 else q[0].split(".")[-1]
    value = get_path(_json(ctx), path)
    ctx["vars"][name] = value
    return f'Saved {name} = {_short(value)} (use {{{{{name}}}}} in later steps)', f'{name} = data{_py(path)}'


I = re.I
HANDLERS = [
    (re.compile(rf"\b(?P<method>{METHODS})\b.*\b(request|call|to|on)\b|^(send|make|call|do|hit|perform)\s+(an?\s+)?(?P<method2>{METHODS})\b", I), None),
    (re.compile(r"^(set|add|use)\s+(the\s+)?(bearer\s+)?token\b", I), h_token),
    (re.compile(r"^(set|add|with)\s+(the\s+)?(request\s+)?header", I), h_header),
    (re.compile(r"^(set|add|with)\s+(the\s+)?(query\s+)?param", I), h_param),
    (re.compile(r"^(set|use|with)\s+(the\s+)?(request\s+)?(body|payload|json)", I), h_body),
    (re.compile(r"^(save|store|remember|extract)\b", I), h_save),
    (re.compile(r"status", I), h_status),
    (re.compile(r"(response time|responds? (with)?in|faster than|less than \d+\s*(ms|milli|sec))", I), h_time),
    (re.compile(r"(response\s+)?header\b", I), h_resp_header),
    (re.compile(r"\b(field|key|property|value of|path)\b", I), h_field),
    (re.compile(r"(response|body).*\b(is|be) (a )?(list|array) (of|with) \d+", I), h_array_len),
    (re.compile(r"(valid json|is json|be json)", I), h_json),
    (re.compile(r"(contain|include|has text|have text)", I), h_contains),
]


def _send_router(ctx, m, step):
    shape = re.sub(r'"[^"]*"|\'[^\']*\'|\{.*\}|\[.*\]', '', step)
    mm = re.search(rf"\b({METHODS})\b", shape, re.I)
    class M:  # minimal match-like object
        def group(self, _):
            return mm.group(1)
    return h_send(ctx, M(), step)


HANDLERS[0] = (HANDLERS[0][0], _send_router)


def run(target, testcase, method="GET", headers_text="", body_text=""):
    started = time.time()
    title, steps = split_steps(testcase)
    target = (target or "").strip()
    headers, body = {}, (body_text or "").strip() or None
    if target.lower().startswith("curl"):
        method, target, h, b = parse_curl(target)
        headers.update(h)
        body = b or body
    if headers_text and headers_text.strip():
        try:
            headers.update(json.loads(headers_text))
        except ValueError:
            for line in headers_text.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip()] = v.strip()
    if target and not re.match(r"^https?://", target):
        target = "https://" + target

    defaults = {"target": target, "method": method.upper(), "headers": dict(headers), "body": body}
    ctx = {"base": target, "headers": headers, "requests": [], "vars": {}, "resp": None, "elapsed": 0}
    code = ["import requests", "", f"headers = {json.dumps(headers)}", "params = {}"]

    results = []
    # If no step sends a request, send the pasted request first so checks have something to look at.
    send_re = HANDLERS[0][0]
    if not any(send_re.search(re.sub(r'"[^"]*"', '""', s)) for s in steps):
        pre = {"index": 0, "step": f"{method} {target}", "status": "passed", "message": "", "code": "", "ms": 0}
        t0 = time.time()
        try:
            msg, c, extra = _do_request(ctx, method.upper(), target, body)
            pre.update(message=msg, code=c, **extra)
        except Exception as e:  # noqa: BLE001
            pre.update(status="failed", message=str(e) if isinstance(e, AssertionError) else f"{type(e).__name__}: {e}")
        pre["ms"] = int((time.time() - t0) * 1000)
        results.append(pre)
        if pre["status"] == "failed":
            steps = []
    elif body:
        ctx["next_body"] = body

    results += run_steps(steps, HANDLERS, ctx, UNKNOWN)
    for r in results:
        if r.get("code"):
            code += [f"# {r['step']}", r["code"]]
            if r["code"].startswith("resp = "):
                code.append("data = resp.json() if 'json' in resp.headers.get('Content-Type', '') else None")
    return {"title": title, "platform": "api", "target": target, "steps": results,
            "requests": ctx["requests"], "variables": ctx["vars"],
            "request_defaults": defaults,
            "summary": summary(results, started), "code": "\n".join(code)}
