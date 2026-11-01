"""Shared helpers for turning plain-English test steps into actions."""
import re
import time

QUOTED = re.compile(r'"([^"]*)"|\'([^\']*)\'')


def quoted(text):
    """Return every quoted value in a step, in order."""
    return [a or b for a, b in QUOTED.findall(text)]


# A line that starts a new test case: "Test: ...", "Test case 2: ...", "Scenario: ...", "TC01: ...", "TC_02 - ..."
CASE_LINE = re.compile(
    r"^(?P<kind>test(\s*case)?|scenario(\s+outline)?|title|tc)\s*[#_\-]?\s*(?P<num>\d+)?\s*[:\-.)]\s*(?P<text>.+)$", re.I)
SUITE_LINE = re.compile(r"^(feature|suite|test suite)\s*[:\-]\s*(?P<text>.+)$", re.I)
BACKGROUND_LINE = re.compile(r"^(background|before each|setup|set up)\s*:?\s*$", re.I)


def _case_title(m):
    text = m.group("text").strip()
    kind, num = m.group("kind").lower(), m.group("num")
    if kind == "tc":
        return f"TC{num or ''} {text}".strip() if num else f"TC {text}"
    return f"Test case {num}: {text}" if num else text


def clean_step(raw):
    """Normalise one line into a step, or return None for blank lines and comments."""
    line = raw.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    line = re.sub(r"^(step\s*\d+\s*[:.)\-]\s*|\d+\s*[.)\-:]\s*|[-*•]\s+)", "", line, flags=re.I)
    # Gherkin words are allowed and simply dropped
    line = re.sub(r"^(given|when|then|and|but)\s+", "", line, flags=re.I)
    line = re.sub(r"^(i|we|the user|user)\s+(will\s+|can\s+)?", "", line, flags=re.I)
    line = re.sub(r"^(should|must)\s+(?!see|not)", "verify ", line, flags=re.I)
    return line or None


def split_steps(testcase):
    """Split ONE pasted test case into (title, steps).

    Accepts numbered lines (1. / 1) / Step 1:), bullets, or plain lines.
    Lines starting with # are comments. 'Test:' / 'Scenario:' lines become the title.
    """
    title, steps = None, []
    for raw in testcase.splitlines():
        line = raw.strip()
        m = CASE_LINE.match(line)
        if m:
            title = _case_title(m)
            continue
        if SUITE_LINE.match(line) or BACKGROUND_LINE.match(line):
            continue
        step = clean_step(raw)
        if step:
            steps.append(step)
    return title, steps


def split_cases(text):
    """Split pasted text into a suite: (suite_title, [(case_title, steps), ...]).

    A new test case starts at every "Test: ..." / "Scenario: ..." / "TC01: ..." line.
    "Feature: ..." or "Suite: ..." names the whole suite. Steps under "Background:"
    (before the first test case) run at the start of every test case.
    Steps written before the first test case line, without a Background, belong to the first test case.
    """
    suite, background, cases = None, [], []
    in_background, current = False, None
    loose = []  # steps before any test case line
    for raw in text.splitlines():
        line = raw.strip()
        sm = SUITE_LINE.match(line)
        if sm and current is None:
            suite = sm.group("text").strip()
            continue
        if BACKGROUND_LINE.match(line) and current is None:
            in_background = True
            continue
        m = CASE_LINE.match(line)
        if m:
            in_background = False
            current = (_case_title(m), [])
            cases.append(current)
            continue
        step = clean_step(raw)
        if not step:
            continue
        if current is not None:
            current[1].append(step)
        elif in_background:
            background.append(step)
        else:
            loose.append(step)
    if not cases:
        return suite, [(None, background + loose)] if (background or loose) else []
    if loose:
        cases[0][1][:0] = loose
    if background:
        cases = [(t, background + st) for t, st in cases]
    return suite, [(t, st) for t, st in cases if st]


def case_text(title, steps):
    """Rebuild one test case as text for a runner."""
    return "\n".join(([f"Test: {title}"] if title else []) + steps)


def substitute(text, variables):
    """Replace {{name}} placeholders with saved variables."""
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), text)


class StepResult(dict):
    pass


def run_steps(steps, handlers, ctx, on_unknown):
    """Run each step through the first matching handler.

    handlers: list of (compiled_regex, function(ctx, match, step) -> (message, code))
    Returns list of step results. Stops after the first failure (later steps are 'skipped').
    """
    results, failed = [], False
    for i, original in enumerate(steps, 1):
        step = substitute(original, ctx.get("vars", {}))
        res = StepResult(index=i, step=original, status="skipped", message="", code="", ms=0)
        if failed:
            res["message"] = "Skipped because an earlier step failed"
            results.append(res)
            continue
        start = time.time()
        try:
            # match the step's wording only, so quoted values like "Page title" can't change its meaning
            shape = QUOTED.sub('""', step)
            for pattern, fn in handlers:
                m = pattern.search(shape)
                if m:
                    out = fn(ctx, m, step) or ("", "")
                    res["message"], res["code"] = out[0], out[1]
                    if len(out) > 2 and out[2]:
                        res.update(out[2])
                    res["status"] = "passed"
                    break
            else:
                raise ValueError(on_unknown)
        except (AssertionError, ValueError) as e:
            res["status"], res["message"] = "failed", str(e) or "Check failed"
            failed = True
        except Exception as e:  # noqa: BLE001 - report any runner error on the step
            res["status"], res["message"] = "failed", f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
            failed = True
        res["ms"] = int((time.time() - start) * 1000)
        results.append(res)
    return results


def summary(results, started):
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("passed", "failed", "skipped")}
    return {
        "status": "failed" if counts["failed"] else "passed",
        "total": len(results),
        **counts,
        "duration_ms": int((time.time() - started) * 1000),
    }
