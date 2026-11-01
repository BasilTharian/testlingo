"""Read test cases from Excel (.xlsx) or CSV files, and build the Excel template.

Two sheet layouts are understood, and can be mixed:

1. One row per test case
   | Test Case ID | Title          | Steps (one per line in the cell)        | Expected Result              | Run |
   | TC01         | Valid login    | Type "tester" into "Username" ...       | Verify page contains "Hi"    | Y   |

2. One row per step (the ID / title is filled on the first row, or repeated, or merged)
   | Test Case ID | Title       | Step No | Step                           | Expected Result |
   | TC01         | Valid login | 1       | Type "tester" into "Username"  |                 |
   |              |             | 2       | Click "Login"                  | Verify page ... |

The header row is found automatically (it can sit below a few title rows), every sheet
with a Steps column is read, and a Run / Execute column with N / No skips that row.
"""
import csv
import io
import re

COLS = {
    "id": re.compile(r"^(id|#|no\.?|s\.?\s?no\.?|sl\.?\s?no\.?|tc\s?id|tc\s?no\.?|test\s?(case\s?)?(id|no\.?|number)|"
                     r"case\s?id|key|test\s?key|issue\s?key)$", re.I),
    "title": re.compile(r"^(test\s?case(\s?(name|title|summary))?|title|summary|name|scenario(\s?name)?|"
                        r"test\s?(name|title|scenario)|case\s?(name|title))$", re.I),
    "description": re.compile(r"^(description|test\s?description|objective|purpose)$", re.I),
    "step": re.compile(r"^(test\s?)?(steps?|actions?|procedure|step\s?(description|action|details))(\s?to\s?(reproduce|execute))?$", re.I),
    "step_no": re.compile(r"^(step\s?(no\.?|#|number|num))$", re.I),
    "expected": re.compile(r"^(expected(\s?(result|results|outcome|behaviou?r))?)$", re.I),
    "run": re.compile(r"^(run|execute|include|active|enabled|automate)\??(\s?\(y/n\))?$", re.I),
}
STEP_WORDS = re.compile(r"^\s*(\d+\s*[.)\-:]\s*)?(verify|assert|check that|ensure|expect|confirm|should|must|see|"
                        r"i should|the user should|then|and|take a screenshot|wait|save)\b", re.I)
GUIDE_SHEET = re.compile(r"^(how to.*|instructions?|help|read ?me|guide|notes?|phrases?)$", re.I)
NO = {"n", "no", "false", "0", "skip", "skipped", "x", "off", "disabled"}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_tables(filename, data):
    """Return [(sheet_name, rows)] where rows are lists of strings."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
            out.append((ws.title, [r for r in rows if any(r)]))
        wb.close()
        return out
    if name.endswith(".xls"):
        raise ValueError("Old .xls files can't be read. In Excel choose File > Save As > Excel Workbook (.xlsx) and upload that.")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.split("\n", 1)[0]
    delim = "\t" if name.endswith(".tsv") or first.count("\t") > max(first.count(","), first.count(";")) else \
        ";" if first.count(";") > first.count(",") else ","
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    return [("Sheet1", [r for r in rows if any(r)])]


def _find_header(rows):
    for i, row in enumerate(rows[:15]):
        cols = {}
        for j, h in enumerate(row):
            h = re.sub(r"\s+", " ", h.strip().replace("_", " ")).strip(" :*")
            for key, rx in COLS.items():
                if key not in cols and rx.match(h):
                    cols[key] = j
                    break
        if "step" in cols:
            return i, cols
    return None, None


def _lines(text):
    return [ln.strip() for ln in re.split(r"\r?\n", text or "") if ln.strip()]


def tables_to_text(tables, file_title=""):
    """Turn sheets into Testlingo test case text. Returns a dict with the text and what happened."""
    cases, notes = [], {"skipped": [], "comments": 0, "empty": [], "sheets_used": [], "sheets_ignored": []}
    for sheet, rows in tables:
        if GUIDE_SHEET.match(sheet.strip()):
            continue  # the template's own instructions
        h, cols = _find_header(rows)
        if h is None:
            if rows:
                notes["sheets_ignored"].append(sheet)
            continue
        notes["sheets_used"].append(sheet)
        get = lambda r, k: r[cols[k]].strip() if k in cols and cols[k] < len(r) else ""  # noqa: E731
        current = None
        for r in rows[h + 1:]:
            cid, title = get(r, "id"), get(r, "title") or get(r, "description")
            starts_new = (cid or title) and (current is None or (cid and cid != current["id"]) or
                                             (not cid and title and title != current["title"]))
            if starts_new:
                current = {"id": cid, "title": title, "steps": [], "run": True, "sheet": sheet}
                cases.append(current)
            elif current is None:
                # steps before any test case id/title: one case named after the sheet
                current = {"id": "", "title": sheet, "steps": [], "run": True, "sheet": sheet}
                cases.append(current)
            if get(r, "run").lower() in NO:
                current["run"] = False
            current["steps"] += _lines(get(r, "step"))
            for exp in _lines(get(r, "expected")):
                if STEP_WORDS.match(exp):
                    current["steps"].append(exp)
                else:
                    current["steps"].append(f"# Expected: {exp}")
                    notes["comments"] += 1

    out, n = [], 0
    for c in cases:
        name = c["title"] or c["id"] or f"Test case {n + 1}"
        if c["id"] and c["id"].lower() not in name.lower():
            name = f"{c['id']} {name}"
        name = re.sub(r"\s+", " ", name)
        if not c["run"]:
            notes["skipped"].append(name)
            continue
        if not any(not s.startswith("#") for s in c["steps"]):
            notes["empty"].append(name)
            continue
        out += ["", f"Test: {name}"] + c["steps"]
        n += 1
    text = "\n".join(out).strip()
    if text and file_title:
        text = f"Suite: {file_title}\n\n{text}"
    return {"text": text, "cases": n, **notes}


# ---------------------------------------------------------------- Excel template

TEMPLATE_CASES = {
    "web": [
        ("TC01", "Login with a valid user",
         ['Type "tester" into "Username"', 'Type "Pass@123" into "Password"', 'Click "Login"'],
         ['Verify page contains "Welcome, tester"', 'Verify URL contains "dashboard"']),
        ("TC02", "Login with a wrong password",
         ['Type "tester" into "Username"', 'Type "wrong" into "Password"', 'Click "Login"'],
         ['Verify page contains "Invalid username or password"']),
        ("TC03", "Add an item to the cart",
         ['Type "tester" into "Username"', 'Type "Pass@123" into "Password"', 'Click "Login"',
          'Select "Bags" from "Category"', 'Click "Add to cart"'],
         ['Verify page contains "Cart: 1"', 'Take a screenshot']),
    ],
    "mobile": [
        ("TC01", "Login with a valid user",
         ['Wait for "Login"', 'Type "tester" into "Username"', 'Type "Pass@123" into "Password"', 'Hide keyboard', 'Tap "Login"'],
         ['Verify "Welcome" is displayed']),
        ("TC02", "Login with a wrong password",
         ['Type "tester" into "Username"', 'Type "wrong" into "Password"', 'Hide keyboard', 'Tap "Login"'],
         ['Verify "Invalid" is displayed']),
    ],
    "api": [
        ("TC01", "List users", ['Send GET request to "/users"'],
         ['Verify status code is 200', 'Verify response field "data" is a list']),
        ("TC02", "Log in and create a user",
         ['Send POST request to "/login" with body {"username": "tester", "password": "Pass@123"}',
          'Save response field "token" as "token"', 'Set header "Authorization" to "Bearer {{token}}"',
          'Send POST request to "/users" with body {"name": "Dee Smith"}'],
         ['Verify status code is 201', 'Verify response field "data.name" equals "Dee Smith"']),
        ("TC03", "Unknown user returns 404", ['Send GET request to "/users/999"'],
         ['Verify status code is 404', 'Verify response field "error" equals "User not found"']),
    ],
}

PHRASES = {
    "web": ['Open "/login"', 'Click "Login"', 'Type "tester" into "Username"', 'Select "Bags" from "Category"',
            'Check "Remember me"', 'Hover over "Menu"', 'Press Enter', 'Wait 2 seconds', 'Wait for "Done"',
            'Verify page contains "Welcome"', 'Verify page does not contain "Error"', 'Verify title contains "Home"',
            'Verify URL contains "/home"', 'Verify "Email" has value "a@b.com"', 'Verify 3 elements ".product"',
            'Take a screenshot'],
    "mobile": ['Tap "Login"', 'Type "tester" into "Username"', 'Long press "Photo"', 'Hide keyboard', 'Press back',
               'Swipe up', 'Scroll to "Settings"', 'Wait for "Home"', 'Verify "Welcome" is displayed',
               'Verify "Error" is not displayed', 'Verify "Balance" has text "£20"', 'Take a screenshot'],
    "api": ['Send GET request to "/users"', 'Send POST request to "/users" with body {"name": "Ann"}',
            'Set header "X-Api-Key" to "abc"', 'Set bearer token "abc123"', 'Set query param "page" to "2"',
            'Verify status code is 200', 'Verify response time is less than 1000 ms', 'Verify response contains "Ann"',
            'Verify response field "data.0.name" equals "Ann"', 'Verify response field "data" has 5 items',
            'Verify response field "id" is a number', 'Save response field "data.id" as "userId"',
            'Send GET request to "/users/{{userId}}"'],
}


PHRASE_GUIDE = {
    "web": [
        ("Go to a page", 'Open "/login"', "Opens a page of the site (or a full https:// address). Every test case already starts on the URL you pasted."),
        ("Go to a page", "Go back", "Browser Back button."),
        ("Go to a page", "Refresh the page", "Reloads the page."),
        ("Go to a page", "Scroll down", "Also: Scroll up, Scroll to top, Scroll to bottom."),
        ("Do something", 'Click "Login"', "Clicks a button, link, tab or menu item by the text you can see on it."),
        ("Do something", 'Double click "Row 1"', "Double-clicks it."),
        ("Do something", 'Type "tester" into "Username"', "Types into a box found by its label, placeholder, name or id."),
        ("Do something", 'Clear "Search"', "Empties a box."),
        ("Do something", 'Select "Bags" from "Category"', "Picks an option in a drop-down."),
        ("Do something", 'Check "Remember me"', "Ticks a checkbox. Uncheck \"...\" unticks it."),
        ("Do something", 'Hover over "Menu"', "Moves the mouse over it."),
        ("Do something", "Press Enter", "Presses a key: Enter, Tab, Escape, Space, arrow keys…"),
        ("Wait", "Wait 2 seconds", "Pauses."),
        ("Wait", 'Wait for "Order placed"', "Waits up to 15 seconds for that text to appear."),
        ("Check (Verify)", 'Verify page contains "Welcome"', "Passes if the text is on the page (waits up to 5 seconds)."),
        ("Check (Verify)", 'Verify page does not contain "Error"', "Passes if the text is NOT on the page."),
        ("Check (Verify)", 'Verify title contains "Dashboard"', "Checks the browser tab title. Verify title is \"...\" checks it exactly."),
        ("Check (Verify)", 'Verify URL contains "/home"', "Checks the address bar."),
        ("Check (Verify)", 'Verify "Email" has value "a@b.com"', "Checks what is typed in a box."),
        ("Check (Verify)", 'Verify 3 elements ".product"', "Counts elements matching a CSS selector."),
        ("Other", "Take a screenshot", "Adds a screenshot to the results."),
        ("Finding things", 'Click "#submit"   /   Click "//button[2]"', "A CSS selector (starts with # . or [) or an XPath (starts with //) works anywhere a name does."),
    ],
    "mobile": [
        ("Do something", 'Tap "Login"', "Taps an element found by its text, accessibility id, resource id or description."),
        ("Do something", 'Type "tester" into "Username"', "Types into a field."),
        ("Do something", 'Type "1234" into "field 2"', "Types into the 2nd text field on the screen."),
        ("Do something", 'Long press "Photo"', "Touches and holds."),
        ("Do something", 'Clear "Search"', "Empties a field."),
        ("Do something", "Hide keyboard", "Closes the on-screen keyboard."),
        ("Do something", "Press back", "Android Back button."),
        ("Move", "Swipe up", "Also: Swipe down, Swipe left, Swipe right."),
        ("Move", 'Scroll to "Settings"', "Scrolls until that text is on screen."),
        ("Move", "Rotate to landscape", "Also: Rotate to portrait."),
        ("Move", "Put app in background for 3 seconds", "Sends the app to the background, then back."),
        ("Wait", "Wait 2 seconds", "Pauses."),
        ("Wait", 'Wait for "Home"', "Waits up to 20 seconds for it to appear."),
        ("Check (Verify)", 'Verify "Welcome" is displayed', "Passes if it is on the screen."),
        ("Check (Verify)", 'Verify "Error" is not displayed', "Passes if it is NOT on the screen."),
        ("Check (Verify)", 'Verify "Balance" has text "£20"', "Checks the text an element shows."),
        ("Other", "Take a screenshot", "Adds a screenshot to the results."),
        ("Finding things", 'Tap "//android.widget.Button[1]"', "An XPath (starts with //) works anywhere a name does. Use Appium Inspector to see names and ids."),
    ],
    "api": [
        ("Send", 'Send GET request to "/users"', "Sends a request. The path is added to the API address you pasted. Also POST, PUT, PATCH, DELETE."),
        ("Send", 'Send POST request to "/users" with body {"name": "Ann"}', "Sends JSON in the body (write the JSON on the same line)."),
        ("Send", "Send GET request", "Sends to the pasted address itself."),
        ("Set up", 'Set header "X-Api-Key" to "abc123"', "Adds a header to the next requests."),
        ("Set up", 'Set bearer token "abc123"', "Adds Authorization: Bearer abc123."),
        ("Set up", 'Set query param "page" to "2"', "Adds ?page=2 to the next request."),
        ("Check (Verify)", "Verify status code is 200", "Checks the status. 'is 200 or 201' allows either."),
        ("Check (Verify)", "Verify response time is less than 1000 ms", "Checks speed."),
        ("Check (Verify)", 'Verify response contains "Ann"', "Searches the whole response body."),
        ("Check (Verify)", 'Verify response field "data.0.name" equals "Ann"', "Checks one value. data.0.name = first item of data, its name."),
        ("Check (Verify)", 'Verify response field "data" has 5 items', "Counts a list."),
        ("Check (Verify)", 'Verify response field "id" is a number', "Also: is text, is a list, is not empty, does not exist, contains \"...\"."),
        ("Check (Verify)", 'Verify header "Content-Type" contains "json"', "Checks a response header."),
        ("Chain requests", 'Save response field "data.id" as "userId"', "Remembers a value from the response…"),
        ("Chain requests", 'Send GET request to "/users/{{userId}}"', "…and uses it in a later step of the same test case."),
    ],
}

COLUMN_NOTES = {
    "Test Case ID": "Any ID you like: TC01, LOGIN-3, 101… Optional.",
    "Title": "A short name for the test case. Required (or an ID).",
    "Steps": "What to do, one step per line inside this cell.\nNew line: Alt+Enter (Windows) or Option+Return (Mac).\nPut names and values in \"double quotes\".\nSee the 'How to write' sheet for every phrase.",
    "Expected Result": "Checks, one per line, starting with Verify.\nExample: Verify page contains \"Welcome\"\nText that doesn't start with Verify is kept as a note, not checked.",
    "Run": "Y = run this test case, N = leave it out.",
}


def template_xlsx(platform="web"):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    platform = platform if platform in TEMPLATE_CASES else "web"
    st = _styles()
    wb = Workbook()

    # ---- sheet 1: the test cases to fill in (with examples to copy)
    ws = wb.active
    ws.title = "Test cases"
    head = ["Test Case ID", "Title", "Steps", "Expected Result", "Run"]
    rows = [[cid, title, "\n".join(steps), "\n".join(exp), "Y"] for cid, title, steps, exp in TEMPLATE_CASES[platform]]
    _sheet(ws, head, rows, (14, 32, 70, 50, 8))
    for c in ws[1]:
        c.comment = Comment(COLUMN_NOTES[c.value], "Testlingo", width=320, height=140)
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("E2:E1000")
    # light yellow rows to show where new test cases go
    fill = PatternFill("solid", fgColor="FFFBEA")
    for r in range(ws.max_row + 1, ws.max_row + 21):
        for col in range(1, 6):
            cell = ws.cell(row=r, column=col)
            cell.fill, cell.border, cell.alignment = fill, st["border"], st["wrap"]
    ws.auto_filter.ref = None

    # ---- sheet 2: how to write test cases
    g = wb.create_sheet("How to write")
    for col, w in zip("ABC", (20, 58, 78)):
        g.column_dimensions[col].width = w
    h1, h2 = Font(bold=True, size=15), Font(bold=True, size=12)
    wrap = Alignment(wrap_text=True, vertical="top")

    def put(values, font=None, fill_=None, height=None):
        g.append(values)
        r = g.max_row
        for i in range(1, 4):
            cell = g.cell(row=r, column=i)
            cell.alignment = wrap
            if font:
                cell.font = font
            if fill_:
                cell.fill = fill_
        if height:
            g.row_dimensions[r].height = height
        if len(values) == 1:  # a heading or sentence: use the whole width
            g.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)

    put([f"How to write Testlingo test cases in Excel ({'API' if platform == 'api' else platform.title()})"], h1, height=24)
    put(["Fill in the 'Test cases' sheet, save it, then click Upload Excel in Testlingo (or drop the file onto the test case box)."])
    put([])
    put(["The rules"], h2)
    for rule in [
        "1. One row = one test case. Give it a Title (and an ID if you use them).",
        "2. In the Steps cell, write one step per line. Press Alt+Enter (Windows) or Option+Return (Mac) for a new line in the same cell.",
        "3. Put names and values in \"double quotes\": Type \"tester\" into \"Username\".",
        "4. Write checks in Expected Result, starting with Verify: Verify page contains \"Welcome\".",
        "5. Run = N leaves a test case out of the run.",
        "6. Each test case runs on its own, from the start. If one step fails, the rest of that test case is skipped and the next test case still runs.",
        "7. You can use several sheets (one per feature, say). Every sheet with a Steps column is read. This 'How to write' sheet is ignored.",
    ]:
        put([rule], height=20)
    put([])
    put(["Other layout: one row per step"], h2)
    put(["If you prefer, put each step on its own row. Write the ID and Title on the first row of the test case and leave them empty below it:"], height=30)
    example = TEMPLATE_CASES[platform][0]
    put(["Test Case ID  |  Title", "Steps", "Expected Result"], Font(bold=True, color="FFFFFF"), st["head_fill"])
    put([f"{example[0]}  |  {example[1]}", example[2][0], ""])
    for stp in example[2][1:]:
        put(["", stp, ""])
    put(["", "", example[3][0]])
    put([])
    put(["Phrases Testlingo understands"], h2)
    put(["What", "Write it like this", "What it does"], Font(bold=True, color="FFFFFF"), st["head_fill"])
    last = None
    for cat, phrase, what in PHRASE_GUIDE[platform]:
        put([cat if cat != last else "", phrase, what])
        g.cell(row=g.max_row, column=2).font = Font(name="Consolas")
        last = cat
    put([])
    put(["Tips"], h2)
    for tip in [
        "• Not sure a step is right? Paste the test cases into Testlingo first: steps it doesn't understand fail with a hint.",
        "• Expected results written as plain sentences (\"User sees the dashboard\") are kept as notes because they can't be checked automatically. Rewrite them as Verify steps.",
        "• Click Download Excel in Testlingo to save the test cases from the box in this same format; Download Excel results after a run adds the pass/fail of every test case.",
    ]:
        put([tip], height=30)

    for sheet in (ws, g):  # print on landscape pages, one page wide
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    wb.properties.title = "Testlingo test cases"
    return _save(wb)


# ---------------------------------------------------------------- Excel export

_ID_RE = re.compile(r"^((?:TC|TEST|CASE)[\s_\-]*[A-Z0-9]*\d+|[A-Z][A-Z0-9]+-\d+)\b[\s:.\-]*(.*)$", re.I)
_CHECK_RE = re.compile(r"^(verify|assert|check that|ensure|expect|confirm|should|see|i should|take a screenshot|#\s*expected)", re.I)


def _split_title(title):
    m = _ID_RE.match(title or "")
    return (m.group(1), m.group(2) or title) if m else ("", title or "")


def _split_steps(steps):
    """Steps in order; the trailing run of checks goes to Expected Result (re-importing keeps the order)."""
    cut = len(steps)
    while cut > 0 and _CHECK_RE.match(steps[cut - 1]):
        cut -= 1
    if cut == 0:  # only checks: keep them all as steps
        return steps, []
    exp = [re.sub(r"^#\s*expected:\s*", "", s, flags=re.I) for s in steps[cut:]]
    return steps[:cut], exp


def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    line = Side(style="thin", color="D9D7D0")
    return {
        "head_font": Font(bold=True, color="FFFFFF"), "head_fill": PatternFill("solid", fgColor="1B1D22"),
        "wrap": Alignment(wrap_text=True, vertical="top"), "border": Border(top=line, bottom=line, left=line, right=line),
        "fills": {"passed": PatternFill("solid", fgColor="E4F4EC"), "failed": PatternFill("solid", fgColor="FBE9E7"),
                  "skipped": PatternFill("solid", fgColor="F0F0F2"), "not run": PatternFill("solid", fgColor="F0F0F2")},
        "fonts": {"passed": Font(bold=True, color="167A4A"), "failed": Font(bold=True, color="C0392B"),
                  "skipped": Font(color="6B6F78"), "not run": Font(color="6B6F78")},
    }


def _sheet(ws, head, rows, widths, status_col=None):
    st = _styles()
    ws.append(head)
    for c in ws[1]:
        c.font, c.fill = st["head_font"], st["head_fill"]
    for r in rows:
        ws.append(r)
        row = ws[ws.max_row]
        tall = 1
        for c in row:
            c.alignment, c.border = st["wrap"], st["border"]
            tall = max(tall, str(c.value or "").count("\n") + 1)
        ws.row_dimensions[row[0].row].height = max(20, 16 * min(tall, 25) + 6)
        if status_col is not None:
            cell = row[status_col]
            key = str(cell.value or "").lower()
            if key in st["fills"]:
                cell.fill, cell.font = st["fills"][key], st["fonts"][key]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{chr(64 + len(head))}{ws.max_row}"


def _save(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def cases_xlsx(suite, cases):
    """Test cases as an Excel sheet in the same layout Testlingo imports."""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "Test cases"
    rows = []
    for n, (title, steps) in enumerate(cases, 1):
        cid, name = _split_title(title or f"Test case {n}")
        do, exp = _split_steps(steps)
        rows.append([cid or f"TC{n:02d}", name, "\n".join(do), "\n".join(exp), "Y"])
    _sheet(ws, ["Test Case ID", "Title", "Steps", "Expected Result", "Run"], rows, (14, 34, 70, 50, 8))
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("E2:E1000")
    if suite:
        wb.properties.title = suite
    return _save(wb)


def results_xlsx(job, case_steps):
    """A run's results: a summary sheet, one row per test case, and one row per step."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    cases = job["cases"]
    status = lambda c: "not run" if c["status"] in ("cancelled", "queued", "running") else c["status"]  # noqa: E731
    passed = sum(1 for c in cases if c["status"] == "passed")
    failed = sum(1 for c in cases if c["status"] == "failed")
    total_ms = sum(c["result"]["summary"]["duration_ms"] for c in cases if c["result"])
    info = [
        ("Suite", job.get("suite") or "Test run"),
        ("Platform", job["platform"].title() if job["platform"] != "api" else "API"),
        ("Run on", __import__("datetime").datetime.fromtimestamp(job["created"]).strftime("%Y-%m-%d %H:%M")),
        ("Result", "FAILED" if failed else "PASSED"),
        ("Test cases", len(cases)), ("Passed", passed), ("Failed", failed),
        ("Not run", len(cases) - passed - failed), ("Total time (s)", round(total_ms / 1000, 1)),
    ]
    for k, v in info:
        summary.append([k, v])
        summary.cell(row=summary.max_row, column=1).font = Font(bold=True)
    summary.cell(row=4, column=2).font = Font(bold=True, color="C0392B" if failed else "167A4A")
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 50

    rows, detail = [], []
    for c in cases:
        cid, name = _split_title(c["title"])
        steps = case_steps[c["index"]] if c["index"] < len(case_steps) else []
        do, exp = _split_steps(steps)
        r = c["result"]
        s = r["summary"] if r else None
        bad = next((x for x in (r["steps"] if r else []) if x["status"] == "failed"), None)
        rows.append([cid or f"TC{c['index'] + 1:02d}", name, "\n".join(do), "\n".join(exp), status(c),
                     f"{s['passed']}/{s['total']}" if s else "",
                     bad["step"] if bad else "", bad["message"] if bad else "",
                     round(s["duration_ms"] / 1000, 1) if s else ""])
        for x in (r["steps"] if r else []):
            detail.append([cid or f"TC{c['index'] + 1:02d}", name, x["index"], x["step"], x["status"], x["message"], x["ms"]])

    ws = wb.create_sheet("Test cases")
    _sheet(ws, ["Test Case ID", "Title", "Steps", "Expected Result", "Status", "Steps passed", "Failed step",
                "Reason", "Time (s)"], rows, (14, 32, 55, 40, 10, 12, 36, 50, 9), status_col=4)
    ws2 = wb.create_sheet("Step details")
    _sheet(ws2, ["Test Case ID", "Title", "Step #", "Step", "Status", "Detail", "Time (ms)"], detail,
           (14, 30, 8, 55, 10, 70, 10), status_col=4)
    return _save(wb)
