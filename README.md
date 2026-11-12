# Toolbox — `toolbank`

Generic Python building blocks for operations automation: read and clean data, Alteryx-style transforms, Excel
formulas, reconciliations, rules and flags, OCR, browsers, APIs, email, databases, Fabric/Delta, triggers and
scheduling, robot plumbing, recipes that generate robot code. No business logic, no data.

**Browse the toolbox:** <https://goshuajoh.github.io/Toolbox/> — a searchable catalogue of every function (real
signatures and docstrings, which optional library each one needs), tested code patterns, the delivery method and a
script builder. Generated from the code by `python make_index.py`, served from the `docs/` folder (see
[The toolbox website](#the-toolbox-website)).

**How to deliver a robot:** [METHODOLOGY.md](METHODOLOGY.md) takes a process from the first stakeholder call to
production in seven stages, each with a gate and the artefacts your governance asks for, and maps every design
decision to a block in this toolbox.

## Use it

```bash
git clone https://github.com/goshuajoh/Toolbox.git
pip install -r Toolbox/requirements.txt            # pandas, openpyxl, pyyaml
pip install -r Toolbox/requirements-optional.txt   # only what you need (see comments in the file)
```

**Without git:** recreate the same folders and copy the files from GitHub's **Raw** view, saved as UTF-8 (several
files contain characters such as `€`). You need every `.py` file in `toolbank/` (the modules import each other and
`__init__.py` loads all of them), plus `templates/robot_template.py` for robots and any `patterns/*.py` you use.
`tests/` is optional, to check your setup. Then `pip install pandas openpyxl pyyaml`.

In Jupyter / a script:

```python
import sys; sys.path.append(r"C:\path\to\Toolbox")   # the folder that contains toolbank/
import pandas as pd
from toolbank import *            # every function below
```

The repo is **Toolbox**; the Python package inside it is **`toolbank`**. Copy the whole `toolbank` folder: its
modules import each other, so a single file does not work on its own.

## Which module?

| I need to… | module |
|---|---|
| Read Excel / CSV / parquet / fixed-width, odd headers, merged cells, whole folders | `readers` |
| Parse JSON, XML, key: value text, SWIFT MT940/950, ISO 20022 camt.053, FIX, amounts, tenors | `parse` |
| Clean and normalise: types, dates, currencies, countries, buy/sell, Y/N, accents, outliers | `clean` |
| Sort, filter, formula columns, multi-row, running totals, text to columns / rows, regex columns | `transform` |
| Excel formulas with Excel's results: ROUND, NETWORKDAYS, WORKDAY, EOMONTH, TEXT, IFERROR… | `formula` |
| Join, lookup, union, fuzzy match, reconcile two sources | `combine` |
| Pivot, summarise, unpivot, subtotals | `reshape` |
| Conditions and flags kept in an Excel rules sheet, feature flags, decision tables | `rules` |
| Build / test regex, find ISIN, IBAN, emails, dates, amounts in text, mask them | `regexkit` |
| Validate ISIN, CUSIP, SEDOL, LEI, IBAN, BIC, currency codes | `identifiers` |
| Schema checks, validation, control totals, row hashes, audit columns, changed rows | `quality` |
| Write Excel reports and CSV; drive real Excel (macros, pivots, VBA); VBA modules | `excel_write`, `excel_app`, `vba/` |
| OCR: Tesseract, Windows built-in OCR, Azure Document Intelligence, scanned PDFs | `ocr` |
| Word and PDF text / tables | `docs` |
| Browser with Playwright: forms, downloads, popups, JavaScript, the JSON behind a page, HAR | `web` |
| Browser with Selenium (Edge / Chrome) | `webdriver` |
| HTML without a browser: tables, visible text, links, forms (hidden fields) | `htmlparse` |
| REST APIs: retry, pagination, OAuth2, Graph, signed webhooks, DevTools "Copy as cURL", HAR | `api` |
| Outlook (COM), .eml / .msg files, drafts for review, SMTP | `outlook`, `mail` |
| SQL Server, Fabric warehouse, SQLite: read, write, upsert, incremental loads | `db` |
| Fabric OneLake and Delta tables: read, write, merge, history | `lake` |
| Triggers: folder watch, every N minutes, cron, Windows Task Scheduler, webhook receiver, work queue, one-copy lock | `triggers` |
| Alteryx-style workflow with row counts and timings per step | `flow` |
| A robot as a YAML spec: validated against the function signatures, run as a Flow, or generated as a script | `recipe` |
| One screen step that checks itself, retries, falls back and gives up with evidence | `robust` (`attempt`, `RETRY_PROFILES`) |
| Parallel API calls, chunks, progress with ETA, smaller DataFrames | `perf` |
| Errors, retry, checkpoints, dry run, logs, config, secrets, Teams messages | `robust`, `logs`, `config`, `notify` |
| Business days, holidays, T+n, roll conventions, cut-offs, time zones | `dates` |
| Wait for a file, archive, hash, zip | `files` |
| Mask data, describe its shape, synthetic look-alike data for AI prompts | `privacy` |
| Maker-checker approval in Excel | `approval` |
| Desktop apps (pywinauto / pyautogui) | `desktop` |

| folder | contents |
|---|---|
| `toolbank/` | the functions (one module per topic) |
| `templates/robot_template.py` | robot skeleton: `--env --dry-run --business-date`, one-copy lock, exit codes |
| `patterns/` | tested code blocks for recurring robot decisions (retry and fallback, submit once, login, evidence…) |
| `METHODOLOGY.md` | the delivery method: stages, gates, discovery questions, design tables, test checklist, runbook |
| `vba/` | `.bas` modules — Excel: Developer > Visual Basic > File > Import |
| `tests/` | `pytest` — run after copying to check your environment |
| `docs/` | the website (generated: `index.html`, `toolbox.json`); `site/index.template.html` is its template |
| `.github/workflows/ci.yml` | lint, tests on Python 3.10 and 3.13, and a check that README and `docs/` match the code |

Tested on Python 3.10–3.13 and pandas 2.1–3.0 (with and without pyarrow), browser parts with Chromium. Windows-only
parts (`excel_app`, `desktop`, `outlook`, `ocr.windows_ocr`, running `schtasks`) load their libraries lazily and are
not run by the tests. Never commit data, screenshots, HAR or browser-session files: `.gitignore` blocks the usual ones.

## How the functions behave

The same rules everywhere, so a robot built from these parts fails the same way whatever the input:

- **Headers and column names are matched tolerant of case, spaces and punctuation.** `read_sheet(path,
  required=["Trade Date"])` accepts `TRADE_DATE`, `Trade  Date:` or `trade date`, renames the column to the spelling
  you asked for, and a miss raises `MissingColumns` naming the near misses and every column found. Use
  `standard_columns(df, {"trade_date": ["Trade Dt", "TradeDate"]})` when the same feed arrives with different headers.
- **Text is compared trimmed and case-insensitive** (lookups, rules, `in` lists, required columns; `reconcile` with
  `case_insensitive_text=True`) unless you pass `case=True`. Non-breaking spaces, tabs and zero-width characters count
  as spaces (`cleanse`, `clean_headers`, `lookup` keys). `lookup` treats `1234567`, `1234567.0` and `'1234567'` as the
  same key, never matches a blank key, and `default` fills only keys that are not in the reference table.
- **Blanks never match a condition** (NA, `''` or spaces; also not `!=`, `not in`, `not contains`, `is false`; only
  `is null` matches them) and **unparsable values become NaN / NaT**, never a wrong number: `to_number` understands
  `(1,000)`, `100.00-`, `1,000 DR` (a sign is applied once), `S$ 12`, `USD1,200`, `1.234,56`, unicode minus signs and
  `inf` (-> NaN); `to_date` understands day-first text, ISO with or without a zone, `2026-3-6`, `20260306`, Excel
  serials like `46087.5` and keeps `9999-12-31` open-ended dates (pandas 3).
- **Money is compared in cents** (`cents`, `reconcile(tolerance=...)`, `match_with_tolerance`), rounding follows Excel
  (`round_half_up`), business days follow your holiday file.
- **Errors are classified** so the robot knows what to do: `BusinessError` = the data is wrong, record it and skip the
  item; `SystemError_` = the environment failed (file locked, network), retry; any other `RobotError` or a plain
  Python error (`TypeError`, `AttributeError`...) = stop, it needs a fix. `process_all` applies this per item: every
  failure is returned with the function and line that raised and a `retryable` flag, only successes go into the
  checkpoint (a re-run retries and re-reports every failure), and a blank or repeated key is reported, never skipped.
  `classify` / `error_info` do it for any exception; the template turns it into exit codes (3 when anything is
  retryable, 2 for business exceptions or a rejected input, 5 for a bug).
- **Never retry a send** (`click`, `smtp_send`, `post_webhook` are not retried); retry reads. Writes go through a temp
  file and rename (`safe_write_excel`, `Checkpoint`, `watermark_set`), so a crash never leaves a half-written file.
- **Logs carry ids, never data**: every text value logged is redacted, also inside dicts and lists in `extra=`
  (`get_logger`; numbers are not masked, so pass an account number as text). Config and recipe YAML is read with
  `yaml_load`, so `00123`, `01` and `NO` stay text. A second copy of a robot exits with code 6 (`single_instance`);
  a re-run resumes from the checkpoint (`Checkpoint`, `watch_folder` ledger).

## Recipes: build a robot from a spec

`recipe` turns a small YAML / dict spec into a validated, runnable workflow, and into a plain script. Validation uses
the real function signatures, so a typo in a function or parameter name is reported before anything runs:

```yaml
name: fx_breaks
inputs:                                     # loaded first; refer to them as $rates
  rates: {fn: read_sheet, path: rates.xlsx}
steps:
  - {fn: read_sheet, path: in/trades.xlsx, required: [Trade Date, Amount, ccy]}
  - {fn: cleanse, case: upper}
  - {fn: filter_rows, conditions: [[ccy, in, "USD,SGD"]], pick: 0, label: majors}
  - {fn: lookup, ref: $rates, key: ccy, cols: rate}
  - {fn: safe_write_excel, path: out/breaks.xlsx, sheets: {Breaks: $df}, overwrite: true, as: report}
```

```python
recipe = load_recipe("fx_breaks.yaml")        # ValueError listing every problem, with "did you mean"
df, trace = run_recipe(recipe)                # Flow: rows in / out and ms per step in `trace`
print(recipe_to_code(recipe))                 # the same steps as a script: from toolbank import * ...
```

A step receives the current frame when the function's first parameter is `df` (`cleanse`, `lookup`, `filter_rows`...);
functions that take frames under other names get it explicitly (`reconcile` with `left: $df`). `$df` names the frame
inside any parameter and `$name` an input or an earlier `as:` result (`$$1,000` is the literal text `$1,000`);
`pick: n` takes element n of a tuple result (`filter_rows`, `join3`); `as: name` keeps a result under a name instead
of replacing the frame; `label:` names the step in the trace. Dates typed in YAML become `pd.Timestamp(...)` in the
script. The website's **Build a script** tab writes both the script and the YAML.

## Patterns: blocks for the decisions every robot faces

Functions answer "how do I read this file". Patterns answer "how should this step behave when things go wrong": how
many tries, what to check after an action, the fallback, when to stop, what evidence to keep. Each file in
`patterns/` holds a copyable block, the decisions it encodes (with numbers and reasons), the controls it gives you,
and a `demo()` that runs it offline. The tests run every demo, so a block that stops working fails the build.

The core block is `attempt`:

```python
attempt(
    lambda: ctl_click(window, title="Save", control_type="Button"),   # the action
    verify=saved,                          # what the screen shows when it worked
    recover=close_popup,                   # between tries: Esc, re-focus
    fallbacks=[lambda: hotkey("ctrl", "s")],   # another way to do the same thing
    already_done=saved,                    # never repeat a save that landed
    on_give_up=lambda: screen_capture(run_folder / "save_failed.png"),
    **RETRY_PROFILES["ui"],                # 3 tries, 2 s apart, each checked for up to 10 s
)
```

After the tries and fallbacks it raises `SystemError_` STEP_FAILED with every try in its context; `process_all`
stops after 3 such failures in a row and the template exits 3. The retry profiles (`ui`, `page_load`, `file_lock`,
`read`, `submit`, `login`) keep how many tries and how long to wait in one reviewed place.

## The toolbox website

`docs/index.html` is a self-contained page (no CDN, works offline and behind a proxy) built from the docstrings:
a function finder with search and module filter, the "I need to…" table, every signature with its full docstring,
source links, `pip install` hints, the patterns with copy buttons, the delivery method, a script builder, the robot
skeleton and exit codes.

- Regenerate after changing code or docstrings: `python make_index.py` (rewrites the README index and `docs/`).
  `python make_index.py --check` fails when they are stale; CI and `tests/test_package.py` run it.
- Publish on GitHub Pages once: repository **Settings → Pages → Build and deployment → Source: Deploy from a
  branch → Branch: `main`, folder: `/docs` → Save**. The page is then at <https://goshuajoh.github.io/Toolbox/> and
  updates with every push to `main`. The branch must contain `docs/`: until a feature branch is merged, select that
  branch instead.
- `docs/toolbox.json` holds the same catalogue as data (name, signature, parameters, docstring, needs) for any other
  tool, e.g. an internal wiki or a code generator.

## Develop

```bash
pip install -r requirements.txt pytest ruff markdown
ruff check .                 # lint (rules in pyproject.toml)
python -m pytest -q          # tests; optional libraries skip their tests when absent
python make_index.py         # README index + docs/ from the docstrings, patterns and METHODOLOGY.md
```

A new pattern is one file in `patterns/` with a YAML docstring (title, order, when, tags, needs, blocks, decisions,
governance) and a `demo(tmp)`; copy `patterns/ui_step.py` as the starting point. `tests/test_patterns.py` checks the
format, that `blocks` lists exactly what the file imports from toolbank, and runs the demo.

Every public function needs a one-paragraph docstring (a test checks it): it is the index entry, the website card and
the `copy` snippet. Keep functions generic (no business logic, no data), raise `BusinessError` for bad data and
`SystemError_` for environment failures, and put optional library imports inside the function so the module loads
without them.

## Function index

<!-- INDEX:START -->

### Robust robots (errors, retry, checkpoint, dry run) — `toolbank/robust.py`

| function | what it does |
|---|---|
| `RobotError(…)` | Base error with a short code; context = ids only, never data. |
| `BusinessError(…)` | Data is wrong -&gt; record and skip the item, never retry. |
| `SystemError_(…)` | Environment failed (file locked, timeout) -&gt; retry, abort if persistent. |
| `classify(exc)` | Map plain Python errors onto the robot's three kinds: OSError / timeouts -&gt; SystemError_ (retry); ValueError / KeyError (what bad data raises) -&gt; BusinessError BAD_DATA; anything else, TypeError and AttributeError included (almost always a bug) -&gt; plain RobotError (stop, needs a fix). |
| `error_where(exc)` | 'function (file.py:123)' of the deepest frame in your own code (not pandas, the standard library or toolbank) where exc was raised; the deepest frame of all when none is yours. '' without a traceback. |
| `error_info(exc)` | Plain dict for logs and exception reports: type, code, message, retryable, where (see error_where), context (ids given to RobotError). Works for any exception; plain errors are classified first. |
| `retry(max_attempts=3, base_delay=1.0, backoff=2.0, retry_on=(SystemError…)` | Decorator: call the function up to max_attempts times (so 2 retries by default) with exponential backoff + jitter. Use on reads only, never around sends/submits. log=print (or logger.warning) reports each retry: 'retry 1/2 in 2.1s after OSError: ...'. |
| `attempt(action, verify=None, *, tries=3, wait_s=2.0, backoff=1.0, verify_t…)` | One robot step done safely: action(), then wait until verify() is truthy (the screen / file / record shows it worked). On an error or a failed check: recover() (e.g. press Esc, close a pop-up, re-focus the window), wait, try again, up to `tries`; then each of `fallbacks` (another way to do the same thing: keyboard instead of mouse, API instead of screen) once; then on_give_up() (screenshot, close the app) and SystemError_ STEP_FAILED with every try in .context['history'] (process_all counts it towards its stop-after-N rule; the template exits 3). already_done() is checked before every try, also the first: if it is truthy the step is not repeated. Give it for anything that must not happen twice (submit, pay, send) or keep tries=1. BusinessError (the data is wrong) and a non-retryable RobotError (AUTH) stop at once. Returns action()'s result (None when already done). Profiles: attempt(click, verify, \*\*RETRY_PROFILES['ui']). |
| `process_all(items, handler, max_consecutive_system=3, checkpoint=None, key=str…)` | Run handler(item) for each item and record what happened, so the robot can report and resume: BusinessError (data is wrong) -&gt; recorded with retryable False and skipped; SystemError_ / OSError (environment) -&gt; recorded with retryable True, and N in a row -&gt; abort with SYSTEM_STREAK naming the last error; any other RobotError or a plain bug (AttributeError, auth failure, bad SQL...) -&gt; stop at once, since every item would fail the same way (the error carries item= and where=). Only successes are written to the optional Checkpoint, so a re-run retries every failure and reports it again until it is fixed; with a checkpoint a blank key or a key repeated in the same run is recorded as MISSING_KEY / DUPLICATE_KEY, never silently skipped. log=print reports each failure as it happens. Returns (done_items, failures_df) with columns item, code, message, type, where, retryable: failures.retryable.any() -&gt; re-run later (exit 3); other failures -&gt; report to the owner (exit 2). |
| `Checkpoint(…)` | Remember done keys in a JSON file so a re-run resumes. Key = business id, never row number. |
| `act(fn, *args, dry_run=False, log=print, **kwargs)` | Call a side-effecting function unless dry_run; then only log what would happen (argument names, not values: they may hold data). |
| `step(name, log=print)` | `with step('load'):` -&gt; START / DONE (ms) / FAIL lines (FAIL names the function and line that raised). |
| `run_dir(robot, business_date=None, root='work')` | work/&lt;robot&gt;/&lt;YYYY-MM-DD&gt;/ — logs, checkpoint and outputs for one run. |

### Clean & normalise (Data Cleansing / Auto Field) — `toolbank/clean.py`

| function | what it does |
|---|---|
| `to_number(series)` | Lenient number parse for bank exports -&gt; float64; unparsable -&gt; NaN. Handles '1,234.50', '(1,000)', '100.00-' (trailing minus), '1,000 DR' / 'CR' (DR = negative, a sign is applied once), 'S$ 12', 'USD1,200', NBSP and unicode minus signs; 'inf' -&gt; NaN. Decimal mark as in parse_amount: '1,234' is thousands, '1.234,56' / '10,5' decimal comma. |
| `to_date(series, dayfirst=True)` | Dates from whatever the file holds -&gt; datetime64[us]; unparsable -&gt; NaT. ISO / year-first text (2026-3-6, 2026/08/09) is read year-first, everything else day-first (06/03/2026 = 6 March) value by value. Also 20260306 (yyyymmdd, text or number), Excel serial numbers (46087, 46087.5) that leak into CSV exports, and ISO text with a zone ('...T14:30:00Z', '+08:00' -&gt; UTC wall clock). 9999-12-31 'open-ended' dates are kept where pandas supports them (pandas 3), a date without a year -&gt; NaT. |
| `excel_serial_to_date(series)` | 46087 -&gt; 2026-03-06 (Excel serial numbers that leak into CSV exports). |
| `yyyymmdd_to_date(series)` | 20260306 / '20260306' -&gt; date (mainframe style). |
| `cents(x)` | Money as integer cents; compare these, never floats. |
| `snake_columns(df)` | Column names -&gt; snake_case ('Amount (SGD)' -&gt; 'amount_sgd'). |
| `select(df, keep=None, drop=None, rename=None, dtypes=None, order=None)` | Keep / drop / rename / retype / reorder columns in one call (Alteryx Select). |
| `dynamic_rename(df, pattern=None, repl='', func=None)` | Rename every column by regex (strip ' (SGD)') or by a function (str.upper). |
| `auto_types(df, threshold=0.95)` | Text columns &gt;=95% numeric -&gt; numbers, &gt;=95% date-like -&gt; dates. Zero-padded ids stay text. |
| `cleanse(df, cols=None, case=None, remove_punct=False, blanks_to_nan=True)` | Text columns: NBSP/zero-width/tabs -&gt; space, collapse spaces, trim; optional case ('upper'/'lower'/'title') and punctuation removal; '' -&gt; NaN. |
| `drop_empty(df)` | Remove rows and columns that are entirely blank. |
| `fill_down(df, cols, by=None)` | Fill blanks from the row above (group label printed once in report exports). |
| `impute(df, col, method='median', value=None)` | Fill nulls with 'mean' / 'median' / 'mode', or method='value' with value. |
| `map_values(series, mapping, case_insensitive=True, default=None)` | Standardise labels via dict ('U.S.A.' -&gt; 'US'); unmapped values kept (or set to default). |
| `find_replace(series, table, find_col, replace_col, mode='exact')` | Lookup-table replace: 'exact' = whole value, 'contains' = substrings (longest first). |
| `normalise_key(series, width=None)` | Matching key: upper, letters/digits only, optional zero-pad (fixes '123' vs ' 0123 '). |
| `normalise_name(series)` | Company names for matching: upper, &amp; -&gt; AND, drop punctuation + legal suffixes, collapse spaces. |
| `composite_key(df, cols, sep='\|')` | One key column from several (for lookups/joins on multiple fields). |
| `MissingColumns(…)` | Raised when required columns are not in a table; the message names the near misses. |
| `norm_label(text)` | Canonical form of a header or label for tolerant matching: accents stripped, case folded, NBSP / tabs / punctuation -&gt; single spaces, trimmed; letters of any script are kept ('金額' stays '金額'). 'Trade Date:' == 'TRADE_DATE' == 'trade date'; blank -&gt; ''. |
| `clean_headers(df)` | Tidy column names as exports deliver them: text, NBSP / tabs / newlines -&gt; one space, trimmed, blank or 'Unnamed: 3' -&gt; col_4, duplicates numbered ('Amount', 'Amount' -&gt; 'Amount', 'Amount_2'; a number already taken is skipped, so names are always unique). |
| `find_column(df, name, required=True)` | The actual column name for `name`, matched exactly first, then tolerant of case / spaces / punctuation (norm_label). Two columns that both match -&gt; MissingColumns (ambiguous). Not found -&gt; MissingColumns naming the near misses, or None when required=False. |
| `resolve_columns(df, names)` | {wanted: actual} for several names (see find_column); every missing name is reported in one error, and two wanted names that land on the same column are an error too. |
| `require_columns(df, names, source='')` | Check that columns exist (tolerant of case / spaces / punctuation) and rename them to the spelling you asked for, so the rest of the robot can use df['Trade Date'] whatever the file called it. Missing -&gt; MissingColumns (a BusinessError once classified) listing near misses and the columns found. |
| `standard_columns(df, aliases, strict=False, keep_others=True)` | Rename columns to your standard names from a list of aliases, tolerant of case / spaces / punctuation: aliases = {'trade_date': ['Trade Date', 'TradeDt', 'Trd Dt'], 'amount': ['Amount (SGD)', 'Amt']}. The standard name itself always counts as an alias. strict=True raises MissingColumns when a standard column is not found; keep_others=False drops columns that are not in the mapping. |
| `normalise_bool(series)` | 'Y'/'Yes'/'TRUE'/'1'/'x' -&gt; True, 'N'/'No'/'0' -&gt; False, anything else -&gt; &lt;NA&gt;. |
| `normalise_currency(series, extra=None)` | 'US$' / 'us dollar' / 'S$' / '€' / 'rmb' -&gt; ISO code; 3-letter codes upper-cased; unknown kept (upper). |
| `normalise_side(series)` | 'B'/'Bot'/'Purchase'/'1' -&gt; BUY, 'S'/'Sold'/'2' -&gt; SELL, 'SS'/'5' -&gt; SELL_SHORT; unknown -&gt; &lt;NA&gt;. |
| `normalise_country(series, extra=None)` | Country names / 3-letter codes -&gt; ISO alpha-2 ('Singapore' -&gt; 'SG', 'U.K.' -&gt; 'GB'); unknown kept (upper). |
| `strip_accents(series)` | 'Café Société' -&gt; 'Cafe Societe' (for matching names across systems). |
| `standardise_dates(series, fmt='%Y-%m-%d', dayfirst=True)` | Mixed date text -&gt; one text format (ISO by default); unparsable -&gt; &lt;NA&gt;. |
| `minmax_scale(series)` | Scale numbers to 0..1 (constant column -&gt; 0). |
| `zscore(series)` | (x - mean) / std — how unusual each value is (\|z\| &gt; 3 is a common outlier flag). |
| `outliers_iqr(series, k=1.5)` | True where a value is outside Q1 - k\*IQR .. Q3 + k\*IQR (robust outlier flag). |

### Read (Input / Directory) — `toolbank/readers.py`

| function | what it does |
|---|---|
| `read_sheet(path, sheet=0, header_row=1, required=(), **kw)` | Excel sheet with header on row N (1-based); blank rows dropped, headers tidied (clean_headers). required = columns that must exist, matched tolerant of case / spaces / punctuation and renamed to the spelling you asked for; missing -&gt; MissingColumns naming the near misses. sheet=None or a list -&gt; {sheet: df}. |
| `read_csv_smart(path, required=(), **kw)` | CSV with unknown encoding (BOM/cp1252/latin-1) and delimiter (, ; tab \|; or sep= to force one); headers tidied and `required` checked as in read_sheet. An empty file -&gt; ValueError (a BusinessError once classified), not a crash. |
| `read_any(path, sheet=0, header_row=1, required=(), **kw)` | Read xlsx/xlsm/xls/xlsb/csv/txt/parquet by extension (xls needs xlrd, xlsb needs pyxlsb); headers tidied and `required` checked as in read_sheet. |
| `list_sheets(path)` | Sheet names of a workbook. |
| `read_all_sheets(path, header_row=1, sheet_col='sheet')` | Every sheet stacked into one table, with a column naming the source sheet. |
| `read_folder(pattern, reader=None, source_col='source_file')` | All files matching a glob ('inbox/\*.xlsx') stacked by column name, with the source file name. |
| `find_header_row(path, sheet=0, must_contain=(), scan_rows=30)` | 1-based row number of the first row containing all the given labels. |
| `read_multirow_header(path, sheet=0, header_rows=(1, 2), sep=' \| ')` | Flatten stacked headers ('Q1' above 'Sales') into 'Q1 \| Sales'; blank/merged top cells carried right. |
| `unmerge_fill(path, sheet=0, header_row=1)` | Read a sheet with merged cells; every cell in a merged block gets the block's value. |
| `read_excel_table(path, table_name)` | Read a named Excel Table (Insert &gt; Table) wherever it sits in the workbook. |
| `read_named_range(path, name)` | Read a workbook defined name (e.g. 'FX_Rates'); first row = header. |
| `read_fixed_width(path, spec, encoding='cp1252')` | Fixed-width mainframe file; spec = [("account", 0, 10), ("amount", 10, 25)] (end exclusive). All text. |
| `csv_in_chunks(path, func, chunksize=200000, **read_kw)` | Apply func(chunk) -&gt; DataFrame to a CSV too big for memory; results stacked. |
| `read_parquet(path, columns=None)` | Parquet file (Fabric/OneLake exports). Needs pyarrow. |
| `write_parquet(df, path)` | DataFrame -&gt; Parquet (smaller and typed; what Fabric ingests). Needs pyarrow. |
| `workbook_info(path)` | Sheets with size, visibility, tables, merged-cell count; plus defined names and whether it has macros. |
| `find_in_workbook(path, text, exact=False, case=False)` | Every cell containing text (or equal to it) across all sheets -&gt; DataFrame sheet, cell, value. |
| `read_formulas(path, sheet=0)` | All formulas in a sheet -&gt; DataFrame cell, formula (audit what a legacy workbook calculates). |
| `read_cell(path, sheet, cell, formulas=False)` | One cell's value ('B3'); cached result of a formula unless formulas=True. |
| `read_range(path, sheet, cell_range, header=True)` | A rectangular range ('B4:F20') -&gt; DataFrame (first row = header if header). |

### Parse JSON / XML / text / SWIFT / camt.053 / FIX / amounts / tenors — `toolbank/parse.py`

| function | what it does |
|---|---|
| `read_json(source)` | Path, JSON text, bytes or an already-parsed object -&gt; Python object. |
| `read_jsonl(path)` | JSON Lines file (one JSON object per line) -&gt; list of objects. |
| `flatten_json(obj, sep='.', prefix='')` | Nested dict/list -&gt; flat dict {'a.b': 1, 'items.0.id': 7}. |
| `json_to_df(source, record_path=None, meta=None, sep='.')` | JSON (path/text/object) -&gt; DataFrame. record_path='items' or ['data', 'rows'] picks the list of records; meta=['id', ['account', 'name']] copies parent fields onto every record. Nested dicts become 'a.b' columns. |
| `json_column_to_columns(df, col, prefix=None, sep='.')` | A column holding JSON text or dicts -&gt; flattened columns (prefix defaults to 'col.'). |
| `df_to_json_records(df, path=None, date_format='%Y-%m-%d', indent=None)` | DataFrame -&gt; list of dicts (dates as text, NaN as null), optionally saved to a JSON file for an API upload. |
| `xml_to_df(source, row_tag, sep='/')` | Every element named row_tag (namespaces ignored) -&gt; one row. Descendants become columns: 'Amt', 'Amt@Ccy', 'Dbtr/Nm'; repeated children get [1], [2]. |
| `xml_find(el, path)` | First element at a slash path of local names ('Acct/Id/IBAN'), ignoring namespaces; None if absent. |
| `xml_text(el, path, default=None)` | Text of the first element at path (see xml_find), stripped. |
| `df_to_xml(df, root_tag='rows', row_tag='row', path=None)` | DataFrame -&gt; simple XML (one element per row, one child per column); returns the XML string. |
| `parse_key_values(text, keys=None, next_line=True)` | 'Trade Date: 06/03/2026' / 'Amount = 1,000' lines -&gt; dict. With keys (case-insensitive), only those labels are kept and, if next_line, a label alone on its line takes the next non-empty line as its value (PDF layouts). |
| `key_values_to_df(texts, keys=None, source_names=None)` | Many documents' text -&gt; one row each (columns = keys). |
| `parse_swift_fields(text)` | SWIFT MT message (or just block 4) -&gt; list of (tag, value) in order; multi-line values joined with '\n'. |
| `parse_swift_balance(value)` | 'C260306SGD1234,56' (:60F:/:62F:) -&gt; dict(mark, date, ccy, amount, signed). |
| `parse_mt940(text)` | MT940/MT950 statement(s) -&gt; (statements_df, transactions_df). Transactions: value/entry date, mark (C/D/RC/RD), signed amount (credit +), type, references, :86: info. |
| `parse_camt053(source)` | ISO 20022 camt.053 bank statement XML -&gt; DataFrame of entries (Ntry): account, signed amount, ccy, booking/value date, references, status, counterparty, remittance text. Works across camt.053 versions. |
| `parse_fix(msg, sep=None, names=True)` | FIX message ('8=FIX.4.4\|35=8\|55=VOD.L\|...', SOH or '\|') -&gt; dict; repeated tags become lists. |
| `fix_checksum_ok(msg, sep=None)` | True if the FIX 10= checksum matches (sum of bytes before '10=' mod 256). |
| `fix_to_df(messages, sep=None)` | Many FIX messages -&gt; DataFrame (one row each, tag names as columns). |
| `parse_amount(text, decimal=None)` | 'USD 1,200.50' / '1.200,50 EUR' / '(1,000.00)' / '1,000.00 DR' / 'S$ 12' -&gt; (amount, ccy). decimal='.' or ',' forces the decimal mark; otherwise the last of '.'/',' wins and '1,234' is thousands. |
| `parse_amounts(series, decimal=None)` | Vectorised parse_amount -&gt; DataFrame(amount, ccy) aligned to the input index. |
| `parse_shorthand(series)` | '1.5m' / '250k' / '2bn' / '3mm' / '5 million' / '1,200' -&gt; float (finance size shorthand). |
| `parse_percent(series, plain_is_percent=False)` | '5%' -&gt; 0.05, '250bp' / '250 bps' -&gt; 0.025, '0.05' -&gt; 0.05 (or 0.0005 if plain_is_percent). |
| `parse_tenor(tenor)` | '3M' -&gt; (3, 'M'); 'ON'/'TN'/'SN'/'SW' returned as (0, code). Units: D, W, M, Y. |
| `tenor_to_date(start, tenor, holidays=(), roll='modified_following')` | End date of a tenor from start. ON=+1bd, TN=+2bd, SN=+3bd, SW=spot(+2bd)+1W; D/W/M/Y are calendar periods then rolled to a business day (default modified following). |

### Sort / Filter / Formula / Multi-Row / Text To Columns / RegEx / Unique / Sample — `toolbank/transform.py`

| function | what it does |
|---|---|
| `filter_split(df, mask)` | Returns (rows where mask True, rows where False/blank). |
| `case_when(df, rules, default=None)` | IF/ELSEIF/ELSE column: rules = [(mask, value_or_series), ...]; first match wins. |
| `multi_field(df, cols, func)` | Apply the same function to several columns. |
| `prev_value(df, col, by=None, n=1)` | Value from n rows above (within group). Sort first. |
| `running_total(df, col, by=None)` | Cumulative sum (within group). Sort first. |
| `record_id(df, start=1, by=None, name='record_id')` | Row number overall or restarting per group. |
| `tile(series, n=4, labels=None)` | n equal-count bands (quartiles, deciles). |
| `buckets(series, edges, labels)` | Fixed bands, e.g. ageing: edges=[-np.inf,0,3,5,10,np.inf], labels=['current','1-3','4-5','6-10','&gt;10']. |
| `expand_dates(df, start_col, end_col, freq='D', name='date')` | One row per day ('B' business day, 'MS' month start) between start and end. |
| `text_to_columns(df, col, sep, names=None, regex=False)` | Split one column into several. |
| `text_to_rows(df, col, sep)` | 'A;B;C' in one cell -&gt; three rows. |
| `regex_extract(df, col, pattern)` | Named groups become columns: r'(?P&lt;ccy&gt;[A-Z]{3})\s\*(?P&lt;amt&gt;[\d,.]+)'. |
| `regex_flag(series, pattern, case=False)` | True where text matches the pattern. |
| `find_duplicates(df, keys)` | All rows whose key appears more than once (every copy kept, for review). |
| `unique_split(df, keys)` | (first occurrence of each key, the later duplicates). |
| `keep_latest(df, keys, order_by, ascending=False)` | One row per key: the latest / highest by order_by. |
| `sample_rows(df, n, how='first', by=None, seed=42)` | First / last / random n rows, overall or per group. |
| `sort_natural(df, col, ascending=True)` | 'A2' before 'A10' (human order, not text order). |
| `sort_custom(df, col, order, then=None)` | Sort by a business order, e.g. order=['HIGH', 'MEDIUM', 'LOW']; unknown values last; then= extra sort cols. |
| `top_n(df, n, sort_col, by=None, ascending=False)` | Top n rows overall or per group by sort_col. |
| `next_value(df, col, by=None, n=1)` | Value from n rows below (within group). Sort first. |
| `rank_within(df, col, by=None, ascending=False, method='min')` | Rank within group (1 = largest by default). |
| `rolling_within(df, col, window, by=None, func='mean', min_periods=1)` | Moving mean/sum/max... over the last `window` rows within group. Sort first. |
| `pct_change_within(df, col, by=None)` | Change vs previous row within group, as a fraction. Sort first. |
| `nest(df, by, col, name=None)` | Collapse rows to one per group with a list of values (opposite of text_to_rows / explode). |
| `first_last(df, by, order_col)` | First and last row of each group by order_col -&gt; (first_rows, last_rows). |
| `filter_rows(df, *conditions, how='all', case=False)` | Alteryx Filter with several conditions: filter_rows(df, ("ccy", "in", "USD,SGD"), ("amount", "&gt;", 1e6)). One condition may be given flat: filter_rows(df, "ccy", "in", "USD,SGD"). how='all' (AND) or 'any' (OR); operators as rules.OPS. Returns (matching, not_matching). |
| `filter_text(df, text, cols=None, case=False, regex=False)` | Rows where any of cols (default all) contains text — a search box. |
| `filter_between(df, col, low=None, high=None)` | Rows with low &lt;= col &lt;= high; either end optional ('2026-01-31' works on date columns). |

### Combine (Join / Lookup / Union / Fuzzy Match / Reconcile / Diff) — `toolbank/combine.py`

| function | what it does |
|---|---|
| `join3(left, right, left_on, right_on=None, suffixes=('', '_r'), validate…)` | Returns (left-only, joined, right-only). validate='many_to_one' raises if right keys repeat. |
| `lookup(df, ref, key, cols, ref_key=None, default=None, as_text=True, allo…)` | XLOOKUP: add/overwrite cols from ref by key. Keys compared as trimmed text, ignoring case and repeated spaces like Excel, 1234567 == 1234567.0 == '1234567' (case=True to match exactly; as_text=False to compare raw values). A blank key never matches. default fills keys not found in ref; a blank value in ref stays blank. Raises on duplicate keys in ref (VLOOKUP silently takes the first), naming the spellings that clash. |
| `sumifs(df, ref, keys, value_col, agg='sum', name=None, ref_keys=None)` | SUMIFS / COUNTIFS / AVERAGEIFS: aggregate ref by keys, attach to each df row (0 if none). |
| `group_value(df, by, col, agg='sum')` | Group aggregate repeated on every row (share-of-total, 'group has &gt;1 row' flags). |
| `outer_join(left, right, on_left, on_right=None)` | Full outer join; '_merge' column says left_only / right_only / both. |
| `join_multiple(frames, on, how='outer')` | Join several tables on the same key. |
| `union(frames, names=None, source_col='source')` | Stack tables by column name (missing columns -&gt; blank); optional source label. |
| `append_fields(df, other)` | Every row of df with every row of other (cross join; keep `other` small). |
| `fuzzy_match(left, right, threshold=0.85, normalise=normalise_name)` | Best match in right for each value in left (names, not ids). Returns value, match, score. |
| `match_with_tolerance(L, B, *, amt_l, amt_r, date_l, date_r, tol_cents=1, window_days=2)` | Reconcile: L row matches a B row if amount within tol_cents and B date in [L date, +window_days]. Each B row used once; earliest date then smallest diff wins. Returns (matched, unmatched_L, unmatched_B). |
| `compare_frames(old, new, key, cols=None)` | Diff two versions of a table by key -&gt; (added, removed, changed[key, column, old, new]). |
| `reconcile(left, right, on, compare=None, tolerance=None, suffixes=('_l', '_r…)` | Two-way reconciliation by key. Every key gets status MATCHED / BREAK / LEFT_ONLY / RIGHT_ONLY, 'break_cols' lists the fields that differ, and '&lt;col&gt;_diff' holds numeric differences. tolerance = {'amount': 0.01} (absolute, compared in cents-safe rounding); text compared trimmed. |
| `recon_summary(recon)` | Counts per status from reconcile(). |

### Reshape (Summarize / Cross Tab / Transpose) — `toolbank/reshape.py`

| function | what it does |
|---|---|
| `summarize(df, by, **aggs)` | Group and aggregate: summarize(df, ['ccy'], total=('amount','sum'), n=('ref','count')). A ['amount', 'sum'] list (as a YAML recipe gives it) works like the tuple. |
| `pivot(df, index, columns, values, aggfunc='sum', totals=True, fill=0)` | Excel-style pivot with optional Total row/column; flat column names ('v \| USD' for several values). |
| `unpivot(df, id_cols, var_name='field', value_name='value')` | Wide -&gt; long (months across -&gt; one row per month). |
| `add_subtotals(df, group_col, value_cols, label='Subtotal', grand_total=True)` | Subtotal row after each group + Grand Total row (report layout). |
| `share_of_total(df, col, by=None)` | Each row as a fraction of the (group) total. |

### Excel formulas with Excel's behaviour — `toolbank/formula.py`

| function | what it does |
|---|---|
| `round_half_up(x, ndigits=0)` | Excel ROUND: 2.5 -&gt; 3, 0.125 -&gt; 0.13, -2.5 -&gt; -3. (Python's round() and pandas .round() give 2 and 0.12.) |
| `roundup(x, ndigits=0)` | Excel ROUNDUP: away from zero. |
| `rounddown(x, ndigits=0)` | Excel ROUNDDOWN: towards zero (truncate). |
| `ceiling(x, ndigits=0)` | Round towards +infinity at ndigits. |
| `floor(x, ndigits=0)` | Round towards -infinity at ndigits. |
| `iferror(series, func, default=None)` | Apply func to each value; errors give default (Excel IFERROR). |
| `coalesce(*series)` | First non-blank value across columns, left to right (blank strings count as blank). |
| `textjoin(df, cols, sep=', ', skip_blank=True)` | Excel TEXTJOIN across columns per row. |
| `safe_divide(a, b, default=nan)` | a / b with default where b is 0 or blank (no inf, no ZeroDivisionError). |
| `edate(dates, months)` | Excel EDATE: same day n months later; clipped to month end (31 Jan + 1 -&gt; 28/29 Feb). |
| `eomonth(dates, months=0)` | Excel EOMONTH: last day of the month, n months from dates. |
| `networkdays(start, end, holidays=())` | Excel NETWORKDAYS: business days from start to end, both counted; negative if end &lt; start; blank -&gt; &lt;NA&gt;. |
| `workday(start, days, holidays=())` | Excel WORKDAY: the date `days` business days after (or before, if negative) start; start not counted. |
| `datedif(start, end, unit='D')` | Excel DATEDIF: whole 'D' days, 'M' months or 'Y' years between start and end. |
| `excel_format_to_strftime(fmt)` | 'dd/mm/yyyy hh:mm' -&gt; '%d/%m/%Y %H:%M' ('mm' is minutes after hh or before ss, otherwise month; single d/m are zero-padded). |
| `excel_text(dates, fmt='dd/mm/yyyy')` | Excel TEXT(date, fmt) for a date column. |
| `rank(series, ascending=False, method='min')` | Excel RANK.EQ: 1 = largest by default; ties share the best rank. |
| `sumproduct(*series)` | Excel SUMPRODUCT of equally long columns. |
| `sign(series)` | -1 / 0 / 1. |
| `left(series, n)` | Excel LEFT. |
| `right(series, n)` | Excel RIGHT. |
| `mid(series, start, n)` | Excel MID (start is 1-based). |
| `proper(series)` | Excel PROPER: 'ACME pte ltd' -&gt; 'Acme Pte Ltd'. |
| `isnumber(series)` | True where the value is (or parses as) a number. |

### Rules, conditions & flags (rules kept in Excel) — `toolbank/rules.py`

| function | what it does |
|---|---|
| `truthy(v)` | 'Y'/'Yes'/'TRUE'/'1'/'on'/'x' -&gt; True, 'N'/'No'/'0'/''/None -&gt; False. |
| `feature_flag(name, cfg=None, default=False, env_prefix='FLAG_')` | On/off switch: env var FLAG_&lt;NAME&gt; beats cfg[name] beats default (e.g. FLAG_SEND_EMAIL=0 for a test run). |
| `condition(df, column, op, value=None, case=False)` | Boolean mask for one condition, e.g. condition(df, 'ccy', 'in', 'USD,SGD'). Blanks (NA, '' or spaces) never match, also not 'not in' / '!=' / 'not contains' / 'is false'; only 'is null' matches them. Text comparisons ignore case unless case=True. Ops: see OPS. |
| `validate_rules(rules, df=None)` | Problems with a rules table ([] = OK): unknown operators, columns missing from df. |
| `apply_rules(df, rules, prefix='flag_', flags_col='flags', case=False)` | Apply a rules table: columns code, column, op, value (+ optional active, severity, description). Rows with the same code are ANDed. Adds one bool column per code, 'flags' ('CODE1;CODE2') and 'flags_count'. |
| `rules_from_excel(path, sheet=0)` | Load a rules sheet (code \| column \| op \| value \| active \| description) and check its operators. Type operators as words in Excel (eq, ne, gt, ge, lt, le, in, contains, is blank): '=' starts a formula. |
| `flag_summary(df, prefix='flag_')` | Count of rows per flag, highest first. |
| `first_match(df, rules_in_order, default=None, case=False)` | Decision table: first rule whose conditions all hold gives the value. rules_in_order = [(value, [(column, op, value), ...]), ...]. |

### Data quality & audit — `toolbank/quality.py`

| function | what it does |
|---|---|
| `profile(df)` | Per column: type, nulls, distinct, min, max, longest text. |
| `check_schema(df, expected)` | expected = {'amount': 'number', 'value_date': 'date', 'ref': 'text', 'x': 'any'}; returns problems ([] = OK). |
| `validate(df, rules, key_col)` | rules = {'MISSING_AMOUNT': lambda d: d['amount'].isna(), ...} (vectorised masks). Returns one row per failure: key, code. |
| `control_totals(df, amount_col, key_col=None)` | Rows, sum, absolute sum (and distinct keys) — compare input vs output. |
| `assert_totals_match(a, b, tol=0.005)` | Raise if two control_totals differ (counts exact, sums within tol). |
| `assert_unique(df, keys)` | Raise if key columns have duplicates (shows the first few). |
| `row_hash(df, cols=None)` | SHA-256 per row over cols (all by default) — detect changed rows between runs, prove what was processed. |
| `add_audit_columns(df, run_id, source=None, cols=None)` | Add run_id, processed_at (UTC), source and row_hash columns for evidence. |
| `changed_rows(old, new, key, cols=None)` | Rows of new that are added or whose content changed vs old (by row_hash), keyed by key. |

### Identifiers (ISIN / CUSIP / SEDOL / LEI / IBAN / BIC / currency) — `toolbank/identifiers.py`

| function | what it does |
|---|---|
| `currency_valid(code)` | True for an ISO 4217 currency code (plus CNH offshore yuan and XAU/XAG metals). |
| `luhn_check_digit(payload)` | Check digit that makes payload + digit pass Luhn. |
| `luhn_valid(number)` | Luhn mod-10 check (card numbers, ISIN digit string). |
| `isin_check_digit(first11)` | 12th character of an ISIN from its first 11. |
| `isin_valid(isin)` | 2-letter country + 9 alphanumerics + check digit, e.g. US0378331005. |
| `cusip_check_digit(first8)` | 9th character of a CUSIP from its first 8. |
| `cusip_valid(cusip)` | 9 characters, last is the check digit, e.g. 037833100. |
| `cusip_to_isin(cusip, country='US')` | US/CA ISIN from a CUSIP: country + CUSIP + check digit. |
| `sedol_check_digit(first6)` | 7th character of a SEDOL from its first 6 (no vowels allowed). |
| `sedol_valid(sedol)` | 7 characters (consonants/digits) with check digit, e.g. 0263494. |
| `sedol_to_isin(sedol, country='GB')` | GB/IE ISIN from a SEDOL: country + '00' + SEDOL + check digit. |
| `lei_valid(lei)` | ISO 17442 LEI: 18 alphanumerics + 2 check digits, mod 97 == 1. |
| `lei_check_digits(first18)` | Two check digits that complete an LEI. |
| `iban_valid(iban, check_length=True)` | IBAN mod-97 check (spaces allowed), with country length check for common countries. |
| `bic_valid(bic, check_country=True)` | SWIFT BIC: 4 bank + 2 country + 2 location (+ 3 branch), e.g. DBSSSGSG / DBSSSGSGXXX; country must be ISO. |
| `validate_ids(series, kind)` | Boolean Series: is each value a valid &lt;kind&gt; (isin/cusip/sedol/lei/iban/bic/card)? Blanks -&gt; False. |
| `identify_id(value)` | Best guess of which identifier a value is ('ISIN', 'CUSIP', 'SEDOL', 'LEI', 'IBAN', 'BIC') or None. |

### Regex toolkit — `toolbank/regexkit.py`

| function | what it does |
|---|---|
| `regex_test(pattern, samples, flags=0)` | Try a pattern on sample strings -&gt; table of sample, matched, first match, named/numbered groups, all matches. Use it to build and debug patterns before running them on real data. |
| `regex_replace(series, pattern, repl, flags=0)` | Regex replace on a column; repl may use \1 or \g&lt;name&gt;. |
| `regex_extract_all(series, pattern, flags=0)` | All matches per value -&gt; Series of lists (explode() for one row per match). |
| `regex_split(series, pattern, maxsplit=0)` | Split each value on a regex -&gt; Series of lists. |
| `find_patterns(text, kinds=None, validate=True)` | Scan text for known patterns (PATTERNS keys) -&gt; DataFrame kind, value, start, end. validate=True drops ISIN/CUSIP/SEDOL/LEI/IBAN hits with a wrong check digit and BIC hits with no real country. |
| `mask_patterns(text, kinds=('email', 'sg_nric', 'iban', 'sg_phone'), mask='***')` | Replace matches of the given pattern kinds with &lt;kind:\*\*\*&gt;. |

### Write Excel / CSV (openpyxl) — `toolbank/excel_write.py`

| function | what it does |
|---|---|
| `write_report(path, sheets, number_formats=None, header_color='1F4E78')` | {sheet: df} -&gt; workbook with bold coloured header, frozen top row, column widths, number formats ({'amount': '#,##0.00', 'date': 'dd/mm/yyyy'}). |
| `safe_write_excel(path, sheets, overwrite=False, **report_kw)` | write_report via temp file + rename (never a half-written file); refuses to overwrite unless told. |
| `write_into_template(template, output, sheets, start_cell='A2')` | Fill an existing formatted workbook (logos, styles, formulas elsewhere kept). sheets={'Data': df}. Old values in the target block are cleared first. |
| `format_as_table(path, sheet, name='Table1', style='TableStyleMedium2')` | Make the used range an Excel Table (filters, banding). Headers must be unique text. |
| `add_conditional_format(path, sheet, cell_range, formula, fill='FFC7CE', font='9C0006')` | Highlight where an Excel formula is TRUE: range 'A2:H500', formula '$F2&lt;0' colours whole rows. |
| `add_dropdown(path, sheet, cell_range, options, allow_blank=True)` | Drop-down list in cells (e.g. APPROVED/REJECTED in a checker column). |
| `add_formula_column(path, sheet, header, formula, first_row=2)` | Live Excel formula down a new column, e.g. '=D{r}-E{r}'. Values appear when Excel recalculates. |
| `add_totals_row(path, sheet, columns, label='Total')` | Bold SUBTOTAL(109) row under the data for the named header columns (respects filters). |
| `highlight_cells(path, sheet, cells, color='FFFF00')` | Fill specific cells, e.g. ['C5', 'F12'] from compare_frames. |
| `protect_sheet(path, sheet, password, editable_cols=())` | Lock a sheet except the given column letters (take password from a secret store, not code). |
| `split_to_files(df, by, out_dir, fmt='xlsx', prefix='')` | One file per value of `by` (e.g. per counterparty); returns paths. |
| `to_csv_strict(df, path, sep=',', date_format='%Y-%m-%d', decimals=2, quote_all=F…)` | CSV in the exact shape an upload system expects (dates, decimals, quoting, CRLF, optional BOM). |
| `write_cell(path, sheet, cell, value)` | Set one cell ('B3') in an existing workbook (keeps everything else). |
| `autofit_columns(path, sheet=None, max_width=60)` | Set column widths from content for one or all sheets. |
| `unhide_all_sheets(path)` | Make every hidden / very-hidden sheet visible (legacy workbooks hide lookup tabs). |
| `sheets_to_csv(path, out_dir, encoding='utf-8-sig')` | Every sheet -&gt; its own CSV (values only); returns paths. |
| `col_letter(n)` | 1 -&gt; 'A', 28 -&gt; 'AB'. |
| `col_index(letter)` | 'A' -&gt; 1, 'AB' -&gt; 28. |

### Real Excel via COM (Windows) — macros, pivots, VBA — `toolbank/excel_app.py`

| function | what it does |
|---|---|
| `excel_app(visible=False)` | Separate Excel instance that is ALWAYS quit (never touches the user's open Excel, no zombie EXCEL.EXE). |
| `excel_open(xl, path, read_only=False)` | Open a workbook in an excel_app() instance (absolute path required by COM). |
| `excel_recalculate(path)` | Recalculate all formulas and save (openpyxl writes formulas but cannot compute them). |
| `excel_refresh(path)` | Refresh Power Query / connections / pivots, wait for them, save. |
| `excel_convert(path, to='.xlsx')` | Convert with Excel itself: .xls/.xlsb/.csv -&gt; .xlsx/.xlsm/.csv/.pdf (csv = active sheet only). |
| `excel_run_macro(path, macro, *args, save=True)` | Run a VBA Sub/Function in the workbook with optional arguments; returns the Function result. |
| `excel_pivot(path, source_sheet, pivot_sheet, rows, values, columns=(), filters…)` | Build a real Excel PivotTable (users can drill in) from a sheet's data; replaces pivot_sheet. |
| `excel_sheet_to_pdf(path, sheet, pdf_path=None)` | Export one sheet to PDF (report pack for email). |
| `vba_add_module(path, code, module_name='PyModule')` | Insert VBA source into a workbook and save as .xlsm. Needs Excel: File &gt; Options &gt; Trust Center &gt; 'Trust access to the VBA project object model'. |
| `vba_import_bas(path, bas_files)` | Import .bas module files (e.g. from the vba/ folder) into a workbook and save as .xlsm (Trust access needed). |
| `excel_save_as_csv_utf8(path, sheet, csv_path=None)` | Save one sheet as UTF-8 CSV using Excel itself (keeps Excel's own number/date display text). |
| `vba_extract(path)` | {module: source} from .xlsm/.xls without opening Excel (pip install oletools) — for porting macros. |

### Dates, business days & cut-offs — `toolbank/dates.py`

| function | what it does |
|---|---|
| `load_holidays(path, col='date')` | Holiday dates from a CSV/Excel file with a 'date' column (maintain one per market). ISO / year-first text is read year-first, other text day-first; an unreadable date raises (a missed holiday = a wrong value date). |
| `is_business_day(d, holidays=())` | Mon-Fri and not a holiday (dates, Timestamps or 'YYYY-MM-DD' text all work). |
| `add_business_days(d, n, holidays=())` | T+n (or T-n) skipping weekends and holidays. |
| `next_business_day(d, holidays=(), inclusive=False)` | Same day if inclusive and it is a business day, else the next one. |
| `business_days_between(a, b, holidays=())` | Business days in (a, b] — ageing. Negative if b &lt; a. |
| `adjust_business_day(d, holidays=(), convention='modified_following')` | Roll a non-business day: 'following', 'preceding', 'modified_following' (stay in month), 'modified_preceding'. |
| `convert_tz(series, from_tz, to_tz, naive_result=True, ambiguous='raise', none…)` | Convert naive timestamps from one zone to another (e.g. NY trade times to SG): DST handled. The repeated hour when clocks go back raises unless ambiguous='NaT' (blank it) or a bool array (True = summer time). |
| `month_end(d, business=False, holidays=())` | Last calendar (or business) day of d's month. |
| `assert_before_cutoff(cutoff='17:30', tz='Asia/Singapore', now=None, grace_minutes=0)` | Raise RobotError(PAST_CUTOFF) if local time in tz is past the deadline. |
| `today_in(tz='Asia/Singapore')` | Today's date in a named time zone (not the server's). |

### Files — `toolbank/files.py`

| function | what it does |
|---|---|
| `wait_for_file(pattern, timeout_s=600, poll_s=10, stable_s=10, min_mtime=None)` | Block until a file matching the glob exists, is newer than min_mtime (datetime), and has stopped growing. |
| `newest_file(pattern)` | Most recently modified file matching a glob, or None. |
| `is_locked(path)` | True if the file is open/locked by someone else (Windows Excel lock). |
| `archive(path, archive_dir)` | Move a processed input to archive_dir/YYYYMMDD_name; no-op if already there. |
| `file_sha256(path)` | SHA-256 of a file (evidence that the input/output was not changed). |
| `safe_filename(text, max_len=80)` | Text usable as a Windows file name; a long name is shortened before its extension ('.pdf' is kept). |
| `unique_path(folder, name, max_len=80)` | folder/&lt;safe name&gt; that does not exist yet: report.pdf, report_2.pdf, ... (never overwrite). |
| `zip_files(paths, zip_path)` | Zip a list of files (flat) into zip_path. |
| `unzip(zip_path, out_dir)` | Extract a zip; refuses entries that would escape out_dir. Returns extracted paths. |

### Logging — `toolbank/logs.py`

| function | what it does |
|---|---|
| `get_logger(log_path, run_id=None, name='robot', console=True)` | Logger writing one JSON object per line to log_path (redacted, tagged with run_id); optional console. Every key passed in extra={...} lands in the JSON line (the usual ones first: event, rows, code, item_id, path, count, status, ms), redacted also inside dicts and lists; ts, level, run_id, msg and exc are the line's own keys and cannot be overridden. An exception logged with exc_info adds 'exc'. |

### Privacy / AI-safe data — `toolbank/privacy.py`

| function | what it does |
|---|---|
| `redact(text, patterns=[('nric', '\\b[STFGM]\\d{7}[A-Z]\\b'), ('email', '\…)` | Mask NRIC/FIN, emails, IBANs, 8-19 digit numbers. Reference codes like GL-00012345 survive. |
| `redact_frame(df, cols)` | Replace whole columns with '\*\*\*' (before sharing a sample). |
| `shape_of(v)` | 'GL-000123' -&gt; 'AA-999999' (A upper, a lower, 9 digit). |
| `describe_shape(df)` | Column type, null %, uniqueness, pattern / length / rounded range — no cell values. Safe to paste into AI. |
| `fake_from_pattern(pattern, rng)` | 'GL-999999' -&gt; 'GL-482913' style fake value. |
| `synth(n, spec, seed=42)` | Fake rows. spec = {"ref": ("pattern", "GL-999999"), "amt": ("number", -1e4, 1e4), "d": ("date", "2026-01-01", "2026-12-31"), "ccy": ("choice", ["SGD", "USD"]), "id": ("seq", 1)}. |

### Maker-checker — `toolbank/approval.py`

| function | what it does |
|---|---|
| `fingerprint(values)` | Short SHA-256 of a dict of proposed values; detects edits by the checker. |
| `propose(path, actions, protect_password=None)` | Maker: write actions to an Excel sheet with decision=PENDING and a dropdown for the checker. actions = [{"action_id": "T1:AFFIRM", "action": "AFFIRM", "trade_id": "T1", "price": 101.25}, ...] |
| `read_decisions(path)` | Checker read-back: DataFrame with an 'ok' column (APPROVED + checker id + fingerprint intact) and 'why'. |
| `approved_actions(path)` | (approved action_ids, rejected/pending action_ids) — execute only the first list. |

### Word & PDF — `toolbank/docs.py`

| function | what it does |
|---|---|
| `fill_docx(template, output, values, strict=True)` | Replace {{placeholders}} in a Word file (body, tables, headers, footers). strict: error if any left. |
| `docx_text(path)` | All paragraph + table text of a Word file. |
| `docx_tables(path, header=True)` | Every table in a Word file as a DataFrame (first row = header). |
| `df_to_docx_table(path, df, heading=None, style='Table Grid')` | Append a DataFrame as a table to a Word file (created if missing). |
| `pdf_text(path, pages=None)` | Text of a (non-scanned) PDF; pages = list of 0-based page numbers or None for all. pip install pdfplumber. |
| `pdf_tables(path, pages=None)` | Tables found in a PDF as DataFrames (first row = header). pip install pdfplumber. |
| `pdf_find(path, pattern, flags=re.IGNORECASE)` | All regex matches in a PDF's text (e.g. trade refs, amounts) as a list of dicts of named groups or strings. |

### OCR — `toolbank/ocr.py`

| function | what it does |
|---|---|
| `set_tesseract(path)` | Point pytesseract at tesseract.exe, e.g. r'C:\Program Files\Tesseract-OCR\tesseract.exe'. |
| `prepare_image(img, scale=2.0, grayscale=True, threshold=None, sharpen=False, bor…)` | Clean an image for OCR: flatten transparency, grayscale, upscale small text, optional sharpen and black/white threshold (0-255), white border. Path or PIL image -&gt; PIL image. |
| `ocr_image(img, lang='eng', psm=6, prepare=True, config='')` | Image -&gt; text with Tesseract (every page of a multi-page TIFF, joined by a form feed). psm: 6 one block, 4 columns, 11 sparse text, 7 one line. |
| `ocr_data(img, lang='eng', psm=6, prepare=True, min_conf=-1)` | Words with position + confidence -&gt; DataFrame(text, conf, left, top, width, height, page_num, block, par, line, word); page_num counts TIFF pages. Coordinates are on the prepared image when prepare=True. |
| `ocr_lines(words)` | ocr_data words -&gt; one row per line: text, conf (mean), left, top, right, bottom (top-to-bottom order). |
| `low_confidence(words, threshold=60)` | Words Tesseract was unsure about (conf &lt; threshold): route these to a human check. |
| `ocr_regions(img, boxes, lang='eng', psm=7, prepare=True)` | Fixed-layout forms: {'field': (left, top, right, bottom)} in original image pixels -&gt; {'field': text}. |
| `pdf_to_images(path, dpi=300, pages=None)` | PDF pages -&gt; PIL images (no Poppler needed). pages = 0-based indexes, None = all. |
| `ocr_pdf(path, lang='eng', dpi=300, min_chars=25, pages=None, psm=6, engine…)` | PDF -&gt; DataFrame(page, method, text): the text layer where a page has one, OCR only for scanned pages. engine = fn(PIL image) -&gt; text to use another OCR (e.g. windows_ocr). |
| `windows_ocr(img, lang='en-US')` | Windows 10/11 built-in OCR (pip install winocr). Image path or PIL -&gt; text. Runs in a worker thread so it also works inside Jupyter. |
| `docintel_analyze(path, endpoint, session, model='prebuilt-layout', pages=None, feat…)` | File -&gt; analyzeResult dict. ONLY with your bank's approved resource (the file leaves your PC). session = api_key_session(key, header='Ocp-Apim-Subscription-Key') or bearer_session(token). model: prebuilt-read (text) \| prebuilt-layout (tables) \| prebuilt-invoice \| your custom model id. |
| `docintel_text(result)` | Full text of an analyzeResult. |
| `docintel_tables(result)` | analyzeResult tables -&gt; list of DataFrames (columnHeader rows become column names, spans filled). |
| `docintel_fields(result)` | Fields of prebuilt/custom models -&gt; DataFrame(doc, field, content, value, confidence, type). |

### Config & secrets — `toolbank/config.py`

| function | what it does |
|---|---|
| `yaml_load(text)` | YAML text -&gt; Python, like yaml.safe_load but with YAML 1.2 numbers and booleans: '00123' and '01' stay text, NO / on / yes stay text, only true / false are booleans. Use it for every config and recipe file. |
| `load_config(config_dir='config', env=None, env_prefix='BOT_')` | default.yaml, then &lt;env&gt;.yaml merged on top (nested sections key by key), then BOT_&lt;KEY&gt; environment variables on top: BOT_RETRIES=3 -&gt; cfg['retries'] = 3 (typed like YAML), BOT_DB\_\_HOST=x -&gt; cfg['db']['host']. Returns dict (cfg['env'] = the environment name). |
| `require(cfg, *keys)` | Raise a clear error if config keys are missing. |
| `get_secret(name, service='robot')` | Secret from env var, else Windows Credential Manager / OS keyring (pip install keyring). Never hard-code. |

### Notify (Teams) — `toolbank/notify.py`

| function | what it does |
|---|---|
| `teams_message(webhook_url, title, facts=None, color='0078D7', dry_run=False, pos…)` | Post a card to a Teams channel webhook. facts = {"Status": "PARTIAL", "Exceptions": 3}. |

### APIs, webhooks, DevTools cURL / HAR — `toolbank/api.py`

| function | what it does |
|---|---|
| `http_session(retries=3, backoff=0.5, timeout=30, verify=None, proxies=None, hea…)` | requests.Session with automatic retry + backoff (GET/PUT/DELETE; POST only if retry_post), a default timeout, corporate CA bundle (verify='C:/certs/corp.pem', or env REQUESTS_CA_BUNDLE) and proxies. |
| `safe_url(url)` | URL without its query string, for logs and errors (query strings carry sig= / token= secrets). |
| `raise_for_status(status, url='', body='')` | Classified error for an HTTP status: 5xx/408/423/425/429 -&gt; SystemError_ (retry later), 401/403 -&gt; RobotError AUTH (fix credentials/permissions), other 4xx -&gt; BusinessError (fix the request/data). |
| `check_response(resp)` | raise_for_status for a requests.Response; returns resp when OK. |
| `api_call(session, method, url, *, json_body=None, params=None, headers=None…)` | One request -&gt; parsed JSON (None for empty body), or the Response if expect_json=False. Network failures -&gt; SystemError_; SSL certificate problems -&gt; RobotError SSL (set verify= to the corporate CA). |
| `api_get(session, url, params=None, **kw)` | GET -&gt; JSON (see api_call). |
| `api_post(session, url, json_body=None, **kw)` | POST JSON -&gt; JSON (not retried unless the session was built with retry_post=True). |
| `paginate(session, url, params=None, style='next_link', items_key=None, next…)` | Yield every record across pages. style: 'next_link' response holds the next URL (Graph/OData '@odata.nextLink', or next_key='links.next') 'link_header' HTTP Link: &lt;...&gt;; rel="next" (GitHub-style) 'page' page=1,2,... until an empty page (size_param='pageSize' sends the size) 'offset' offset/limit until an empty page (a server may cap pages below `size`) 'cursor' cursor token from response key cursor_key sent back as cursor_param items_key: where the list is ('value', 'data.items'); None = auto-detect common keys or a bare list. |
| `api_to_df(records, sep='.')` | List/generator of JSON records -&gt; flat DataFrame ('a.b' columns for nested fields). |
| `TokenCache(…)` | OAuth2 client-credentials token that refreshes itself 60 s before expiry. Azure AD / Entra ID: token_url=f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', scope='https://graph.microsoft.com/.default'. |
| `  .get()` | Current access token (fetches a new one when missing or about to expire). |
| `bearer_session(token, **session_kw)` | Session that sends 'Authorization: Bearer &lt;token&gt;' on every request; token = string, TokenCache or callable. |
| `api_key_session(key, header='X-API-Key', **session_kw)` | Session that sends an API key header on every request. |
| `windows_auth_session(**session_kw)` | Session using your Windows login for intranet sites (Kerberos/NTLM SSO). pip install requests-negotiate-sspi (preferred on Windows) or requests_ntlm. |
| `download_file(session, url, dest, sha256=None, chunk=65536, **kw)` | Stream a URL to dest atomically (temp file, then rename); optional SHA-256 check. Returns the Path. |
| `upload_file(session, url, path, field='file', data=None, **kw)` | Multipart file upload (POST form field `field`) -&gt; JSON response. |
| `RateLimiter(…)` | At most `rate` calls per `per` seconds (thread-safe). Call .wait() before each request. |
| `sign_body(body, secret)` | 'sha256=&lt;hex&gt;' HMAC of the raw body bytes with a shared secret. |
| `verify_signature(body, secret, signature)` | Constant-time check of a 'sha256=&lt;hex&gt;' signature against the raw body. |
| `post_webhook(url, payload, secret=None, session=None, header='X-Signature', dry…)` | POST JSON to a webhook (internal service, Power Automate 'When an HTTP request is received', Teams workflow). With secret, adds an HMAC signature header the receiver can verify. Not retried (may have been delivered). |
| `graph_get_all(session, path, params=None, base='https://graph.microsoft.com/v1.0')` | All records from a Graph list endpoint ('/sites/{id}/drives', '/users/{id}/messages'), following nextLink. |
| `graph_download(session, drive_id, item_path, dest, base='https://graph.microsoft.…)` | Download a SharePoint/OneDrive file by path inside a drive. |
| `graph_upload(session, drive_id, item_path, local, base='https://graph.microsoft…)` | Upload a file (&lt; 4 MB) to a SharePoint/OneDrive path; returns the drive item JSON. |
| `graph_send_mail(session, sender, to, subject, html, cc=(), attachments=(), base='h…)` | Send mail from a mailbox the app may send as (Mail.Send). Attachments are file paths. |
| `curl_to_request(curl_text, drop='cookie\|authorization\|token\|secret\|api[-_]?key\|sub…)` | DevTools 'Copy as cURL' (bash or cmd) -&gt; dict(method, url, params, headers, json \| data, dropped) for api_call. Headers / query params that look like secrets (cookies, tokens, keys, sig=) are dropped and listed in 'dropped': authenticate with your own session or config, never paste secrets into code. |
| `har_sanitize(path, keep_post_data=False)` | Strip secrets from a HAR file in place: cookies, auth / token / key headers, sig= / token= values in URLs, request bodies (a login form holds your password) unless keep_post_data, response bodies holding tokens. Browser(record_har=...) runs this automatically. Returns the number of entries. |
| `har_entries(path)` | HAR file (Playwright record_har, or Edge DevTools &gt; Network &gt; Export HAR (sanitized)) -&gt; DataFrame of calls: started, method, url, status, mime, type, size, ms. Find the JSON API behind a page. |
| `har_json(path, url_re='.*')` | JSON bodies recorded in a HAR whose URL matches url_re: [{'url', 'method', 'status', 'data'}]. |

### HTML without a browser — `toolbank/htmlparse.py`

| function | what it does |
|---|---|
| `html_tables(html, header='auto', match=None)` | Every &lt;table&gt; -&gt; DataFrame (colspan/rowspan expanded, &lt;br&gt; -&gt; newline). header: 'auto' (thead or all-&lt;th&gt; first row), True (first row), False (numbered columns). match: regex a table's text must contain. |
| `html_text(html)` | Visible text of a page (scripts/styles/title removed, one line per block or table row, spaces tidied). |
| `strip_tags(series)` | Remove HTML tags/entities from a column of HTML snippets. |
| `html_links(html, base_url=None, pattern=None)` | All &lt;a href&gt; -&gt; DataFrame text, url (absolute if base_url); pattern = regex the url must match. |
| `html_forms(html, base_url=None)` | Every &lt;form&gt; -&gt; dict(action, method, id, fields) with current/hidden values (e.g. CSRF tokens), so you can replay the post: session.post(form['action'], data={\*\*form['fields'], 'user': 'x'}). |
| `html_select(html, css, attr=None)` | CSS selector query -&gt; list of texts (or attribute values). Needs beautifulsoup4. |
| `html_json(html)` | JSON shipped inside a page, read without a browser: &lt;script type="application/json"&gt; / ld+json blocks (e.g. `__NEXT_DATA__`) and `window.__STATE__ = {...}` / `var rows = [...]` assignments -&gt; [{'source', 'data'}]. Only valid JSON is returned (JavaScript-only syntax is skipped). |

### Browser (Playwright): actions, JavaScript, network — `toolbank/web.py`

| function | what it does |
|---|---|
| `Browser(…)` | `with Browser("shots") as b: b.goto(url); b.click("text=Export")`. Failures screenshot + raise SystemError_. record_har='run.har' saves all traffic (secrets stripped unless har_raw=True); block=('image','font','media') speeds up heavy pages. In Jupyter use b.run(lambda page: ...) for raw Playwright calls. |
| `  .run()` | fn(page) on the browser's thread: raw Playwright calls, e.g. b.run(lambda p: p.get_by_role("button").count()). |
| `  .screenshot()` | Full-page PNG into screenshots_dir; returns path (None if it failed). |
| `  .goto()` | Navigate (retried). wait_until: 'domcontentloaded' \| 'load' \| 'networkidle'. |
| `  .wait()` | Wait for an element (retried once). state: visible \| attached \| hidden \| detached. |
| `  .wait_text()` | Wait until some element shows this text. |
| `  .wait_url()` | Wait until the URL matches a regex (e.g. after login redirects). |
| `  .wait_idle()` | Wait until the network is quiet (no requests for 500 ms). |
| `  .exists()` | True if the selector appears within ms. |
| `  .click()` | Click (NOT retried: it may have submitted). |
| `  .fill()` | Type into an input (replaces content); blank values (None / NaN) type ''. |
| `  .fill_form()` | Fill several inputs: {selector: value}. |
| `  .select()` | Choose an &lt;option&gt; by value or label. |
| `  .check()` | Tick / untick a checkbox or radio. |
| `  .press()` | Press a key in an element, e.g. press('#search', 'Enter'). |
| `  .hover()` | Hover (opens hover menus). |
| `  .login()` | Fill credentials (never logged; a recorded HAR is stripped of request bodies), submit, optionally wait for a post-login element. |
| `  .download()` | Click something that starts a download; save it to save_to. |
| `  .upload()` | Set a file on an &lt;input type=file&gt;. |
| `  .popup()` | Click something that opens a new tab/window; returns that page (and makes it current if switch). |
| `  .tabs()` | Open pages (tabs) in this context. |
| `  .switch_tab()` | Make tab `index` current (-1 = newest). |
| `  .accept_dialogs()` | Auto-answer alert/confirm/prompt boxes on every tab (Playwright dismisses them by default); their messages go to .dialogs. |
| `  .text()` | Visible text of an element (retried once). |
| `  .texts()` | Visible text of every match (list). |
| `  .attr()` | Attribute value of the first match (e.g. href, value, data-id). |
| `  .count()` | Number of elements matching sel. |
| `  .html()` | Current page HTML (after JavaScript ran). |
| `  .table()` | HTML &lt;table&gt; -&gt; list of dicts keyed by header text (last &lt;thead&gt; row, or a first row of &lt;th&gt;); row-header &lt;th&gt; cells count as data. |
| `  .tables()` | Every &lt;table&gt; on the page -&gt; list of DataFrames (colspan/rowspan handled); match = regex filter. |
| `  .frame()` | An iframe (legacy portals) to call .click/.fill on. |
| `  .pdf()` | Save the page as PDF (headless Chromium/Edge only). |
| `  .js()` | Run JavaScript in the page and return its (JSON-able) result: js('() =&gt; document.title'), js('n =&gt; window.scrollBy(0, n)', 500). |
| `  .scroll_to_bottom()` | Keep scrolling until the page stops growing (infinite-scroll lists); returns rounds used. |
| `  .local_storage()` | window.localStorage as a dict (some portals keep tokens/settings there). |
| `  .capture_json()` | Start collecting JSON responses whose URL matches url_re (the API behind the page). Returns a list that fills while you browse: [{'url', 'status', 'method', 'data'}]. Stop with stop_capture(). |
| `  .log_network()` | Start logging requests of these resource types: [{'method','url','status','type','content_type'}] -&gt; pd.DataFrame(log) shows which API calls a page makes. |
| `  .stop_capture()` | Remove capture_json / log_network listeners. |
| `  .wait_json()` | Do action() (e.g. lambda: b.click('text=Search')) and return the JSON of the first response whose URL matches url_re. Not retried (the action may have submitted). |
| `  .get_json()` | GET an API with the browser's cookies/SSO (no new login) -&gt; JSON. Errors classified like api_call. |
| `  .set_headers()` | Extra HTTP headers on every request from this context. |
| `  .cookies()` | Cookies of this context (list of dicts). |
| `  .requests_session()` | requests.Session carrying this browser's cookies + user agent: log in once in the browser, then call the site's APIs fast with api_get / paginate. session_kw go to api.http_session (verify=, proxies=). |
| `  .save_session()` | Save cookies/SSO so the next run can skip login (treat the file as a password). |
| `  .load_session()` | Start a fresh context from a saved session. |

### Browser (Selenium WebDriver) — `toolbank/webdriver.py`

| function | what it does |
|---|---|
| `webdriver_start(browser='edge', headless=True, download_dir=None, driver_path=None…)` | Start Edge (default) or Chrome; download_dir = where downloads land without a prompt. |
| `webdriver_session(screenshots_dir='screenshots', **start_kw)` | `with webdriver_session(browser="edge") as d:` — screenshot on error, browser always closed. |
| `wd_wait(d, css, timeout=20, state='visible')` | Wait for a CSS selector (state: visible \| clickable \| present) -&gt; element. |
| `wd_click(d, css, timeout=20)` | Wait until clickable, click (not retried: it may have submitted). |
| `wd_fill(d, css, value, timeout=20)` | Clear an input and type value. |
| `wd_text(d, css, timeout=20)` | Visible text of an element. |
| `wd_select(d, css, value, timeout=20)` | Choose a &lt;select&gt; option by value, else by visible text. |
| `wd_js(d, script, *args)` | Run JavaScript (function body, use `return`): wd_js(d, "return document.title"). |
| `wd_tables(d, match=None)` | Every &lt;table&gt; on the page -&gt; list of DataFrames. |
| `wd_accept_alert(d, accept=True, timeout=5)` | Answer an alert/confirm box; returns its message. |
| `wd_frame(d, css=None, timeout=20)` | Switch into an iframe by CSS selector; css=None switches back to the main page. |
| `wd_window(d, index=-1)` | Switch to a tab/window (-1 = newest). |
| `wd_wait_download(folder, timeout_s=120, pattern='*', since=None)` | Wait for a finished download in folder (no .crdownload / .tmp / hidden temp file left) -&gt; newest Path. |
| `wd_requests_session(d, **session_kw)` | requests.Session with the browser's cookies + user agent (log in once, then call APIs with requests). |
| `wd_screenshot(d, path)` | Save a PNG of the current window; returns the path (None if it failed). |

### Desktop (pywinauto / pyautogui) — `toolbank/desktop.py`

| function | what it does |
|---|---|
| `app_connect(title_re=None, exe=None, backend='uia')` | Attach to a running window by title regex, or start an exe. Returns pywinauto Application. |
| `win(app, title_re, timeout=30)` | Top-level window, waited until ready. |
| `dump_controls(window, depth=3)` | Print the control tree — how you find title / auto_id / control_type for the locators below. |
| `ctl_click(window, timeout=15, **locator)` | Click a control: ctl_click(w, title='Save', control_type='Button') or auto_id='btnOK'. |
| `ctl_type(window, text, timeout=15, **locator)` | Clear a field and type text into it. |
| `ctl_text(window, timeout=15, **locator)` | Read a control's text/value. |
| `ctl_select(window, item, timeout=15, **locator)` | Pick an item in a combo/list box. |
| `wait_for_window(app, title_re, timeout=60)` | Wait for a dialog/popup to appear; returns it. |
| `screen_find(image_path, confidence=0.9, timeout=30)` | Centre (x, y) of an on-screen image (saved screenshot of a button); raises if not found. |
| `screen_click(image_path, **kw)` | Click the centre of an on-screen image. |
| `screen_type(text, interval=0.02)` | Type into whatever has focus (ASCII; use clipboard_paste for long/Unicode text). |
| `hotkey(*keys)` | hotkey('ctrl', 's'), hotkey('alt', 'f4'). |
| `clipboard_paste(text)` | Put text on the clipboard and Ctrl+V. |
| `clipboard_read()` | Current clipboard text (e.g. after Ctrl+A, Ctrl+C on a Citrix grid). |
| `screen_capture(path)` | Screenshot of the whole screen. |

### Outlook (COM) — `toolbank/outlook.py`

| function | what it does |
|---|---|
| `outlook_folder(path=('Inbox',), mailbox=None)` | Folder by path, e.g. ("Inbox", "Confirms"); mailbox = shared mailbox display name, else your own. |
| `sender_smtp(mail)` | Sender's SMTP address, also for internal Exchange senders (whose SenderEmailAddress is an /O=... path). |
| `outlook_mails(folder=None, unread_only=False, subject_re=None, sender_re=None, n…)` | Yield mail items newest first, filtered by unread / subject regex / sender regex. |
| `outlook_save_attachments(mail, out_dir, pattern='.*\\.(xlsx\|xlsm\|xls\|csv\|pdf)$')` | Save attachments whose name matches pattern (safe names; two files with the same name never overwrite each other); returns paths. |
| `outlook_mark_read(mail, read=True)` | Mark a mail read/unread. |
| `outlook_mail_info(mail)` | Plain dict of a mail item: subject, sender, received, body text, attachment names. |
| `outlook_move(mail, folder)` | Move a mail to another folder (e.g. 'Processed') so it is not picked up twice. |
| `outlook_send(to, subject, body_html, attachments=(), cc='', send_as=None, dry_r…)` | Send (or display for review) an email from the signed-in profile. |
| `df_to_html(df, max_rows=50)` | DataFrame -&gt; simple HTML table for an email body. |
| `outlook_poll(handler, folder=None, subject_re=None, sender_re=None, processed_f…)` | Email trigger: for each UNREAD matching mail call handler(mail); on success mark it read and (optionally) move it to processed_folder so it is never handled twice. Failed mails stay unread for the next poll. Returns (handled_count, failures) where failures = [{'subject', 'sender', 'error'}]. |

### Email files (.eml / .msg) & SMTP — `toolbank/mail.py`

| function | what it does |
|---|---|
| `read_eml(path)` | .eml -&gt; dict(subject, from, to, cc, date, text, html, attachments, message_id). text falls back to the HTML body's visible text. |
| `read_eml_folder(folder, pattern='*.eml')` | Every .eml in a folder -&gt; DataFrame (one row per email, plus file name). |
| `eml_attachments(path, out_dir, pattern='.*')` | Save attachments whose file name matches the regex; returns paths (names made safe, never overwritten). |
| `read_msg(path)` | Outlook .msg -&gt; same dict as read_eml (pip install extract-msg). |
| `msg_attachments(path, out_dir, pattern='.*')` | Save .msg attachments whose name matches the regex; returns paths. |
| `build_email(sender, to, subject, html=None, text=None, cc=(), bcc=(), attachme…)` | EmailMessage with a plain-text body and optional HTML alternative + file attachments. |
| `save_eml(msg, path, draft=True)` | Write the message to .eml. draft=True adds X-Unsent so Outlook opens it as an unsent draft with a Send button: the robot prepares, a human reviews and sends. |
| `smtp_send(msg, host, port=25, starttls=False, user=None, password=None, time…)` | Send through an SMTP relay. Refused recipients/sender or a permanent 5xx reply (e.g. a DLP block) -&gt; BusinessError, login failure -&gt; RobotError AUTH, 4xx / connection problems -&gt; SystemError_. Not retried (a retry could send twice). Returns refused recipients. |

### SQL databases (SQL Server / Fabric / SQLite) — `toolbank/db.py`

| function | what it does |
|---|---|
| `odbc_conn_str(server, database, driver='ODBC Driver 18 for SQL Server', trusted=…)` | pyodbc connection string. trusted=True = your Windows login. authentication='ActiveDirectoryIntegrated' or 'ActiveDirectoryInteractive' for Entra ID (Azure SQL / Fabric); authentication='token' leaves login out for make_engine(url, token=...). |
| `mssql_url(server, database, **odbc_kw)` | SQLAlchemy URL for SQL Server via pyodbc (odbc_kw as odbc_conn_str). |
| `fabric_sql_url(endpoint, database, authentication='ActiveDirectoryInteractive', u…)` | Fabric Warehouse / Lakehouse SQL analytics endpoint (copy the 'SQL connection string' host from Fabric). The Lakehouse endpoint is read-only; write through a Warehouse or Delta (lake.py). |
| `access_token_attrs(token)` | pyodbc attrs_before for an Entra ID access token (SQL_COPT_SS_ACCESS_TOKEN). |
| `make_engine(url, fast_executemany=True, token=None, **kw)` | SQLAlchemy engine. url: 'sqlite:///x.db', 'postgresql://...' or mssql_url(...). SQL Server gets fast_executemany (bulk inserts much faster). token = string or callable (e.g. TokenCache.get) giving an Entra ID token for https://database.windows.net/.default; refreshed on every new connection. |
| `sql_read(engine, sql, params=None, dtype=None, parse_dates=None)` | SELECT -&gt; DataFrame: sql_read(e, "select \* from trades where desk = :desk", {"desk": "FX"}). |
| `sql_read_chunks(engine, sql, params=None, chunksize=50000)` | Large SELECT as a generator of DataFrames. |
| `sql_exec(engine, sql, params=None, dry_run=False)` | INSERT/UPDATE/DELETE/DDL in one transaction (commit, or roll back on error). params: dict, or a list of dicts to run once per row. Returns rows affected (driver-dependent for lists). |
| `sql_write(df, table, engine, schema=None, if_exists='append', chunksize=1000…)` | DataFrame -&gt; table (if_exists: append \| replace \| fail) in one transaction. Returns rows written. |
| `sql_upsert(df, table, keys, engine, schema=None, dry_run=False)` | Update rows that match on keys, insert the rest, in one transaction. A staging table copies the target's column types (unicode text, datetime2 kept) and is dropped again, so it needs CREATE TABLE rights. SQL Server -&gt; MERGE WITH (HOLDLOCK); SQLite / PostgreSQL -&gt; ON CONFLICT (needs a unique index on keys). |
| `in_clause(name, values)` | Safe IN list: in_clause('id', [1, 2]) -&gt; (':id_0, :id_1', {'id_0': 1, 'id_1': 2}); then sql_read(e, f"select \* from t where id in ({frag})", params). |
| `sql_tables(engine, schema=None)` | Table names in a database/schema. |
| `sql_columns(engine, table, schema=None)` | Columns of a table -&gt; DataFrame(name, type, nullable). |
| `watermark_get(path, key, default=None)` | Last value loaded (timestamp / id) for an incremental load, from a small JSON file. |
| `watermark_set(path, key, value)` | Save the new watermark — call it only after the load fully succeeded. Atomic write. |
| `read_since(engine, table, column, since=None, schema=None, columns='*')` | Rows where column &gt; since (None = all), ordered by column -&gt; (df, new_watermark as plain int/str/date). Date-time text watermarks are sent as datetimes (SQL Server DATETIME rejects 6-digit fractions as text). |

### Fabric OneLake & Delta tables — `toolbank/lake.py`

| function | what it does |
|---|---|
| `onelake_uri(workspace, item, path='', item_type='Lakehouse')` | abfss://&lt;workspace&gt;@onelake.dfs.fabric.microsoft.com/&lt;item&gt;.Lakehouse/&lt;path&gt;. Names or GUIDs (use GUIDs when names contain spaces). path: 'Tables/trades' (a Delta table) or 'Files/inbound'. |
| `azure_token(scope='https://storage.azure.com/.default', interactive=False)` | Entra ID access token with azure-identity (Windows SSO / Azure CLI / VS Code sign-in; interactive=True opens a browser sign-in). |
| `onelake_options(token=None)` | storage_options for deltalake on OneLake. |
| `delta_exists(path, storage_options=None)` | True if path holds a Delta table. |
| `read_delta(path, columns=None, version=None, filters=None, storage_options=None)` | Delta table -&gt; DataFrame. version= reads an older snapshot (audit / time travel); filters=[('desk', '=', 'FX')] reads only matching files. |
| `write_delta(df, path, mode='append', partition_by=None, schema_mode=None, stor…)` | DataFrame -&gt; Delta table. mode: append \| overwrite \| error \| ignore; schema_mode='merge' adds new columns. |
| `delta_merge(df, path, keys, storage_options=None, delete_missing=False)` | Upsert into a Delta table: update rows matching on keys, insert new ones (delete_missing=True also deletes target rows absent from df). Duplicate or blank keys are refused. Returns the merge metrics dict. |
| `delta_history(path, limit=None, storage_options=None)` | Versions of a Delta table (who/when/what operation) -&gt; DataFrame, newest first. |
| `delta_vacuum(path, retention_hours=168, storage_options=None, dry_run=True)` | Files older than retention that vacuum would delete (dry_run=True) or deletes them. |

### Triggers & scheduling (folder, timer, cron, webhook, queue) — `toolbank/triggers.py`

| function | what it does |
|---|---|
| `single_instance(name, lock_dir=None)` | `with single_instance("recon_bot"):` stops a second copy running (scheduler fired again while the last run is still going). OS lock, freed automatically even if the robot crashes. Raises SystemError_ ALREADY_RUNNING (try again later). |
| `poll_until(check, timeout_s=600, every_s=10, what='condition', sleep=sleep, c…)` | Call check() every every_s until it returns something truthy (returned), else SystemError_ TIMEOUT. E.g. wait for an upstream file/report, a status flag in a table, an API job to finish. |
| `wait_until(hhmm, now=now, sleep=sleep)` | Sleep until today's hh:mm (local time); returns at once if already past. Returns seconds waited. |
| `new_files(folder, pattern='*', ledger=None, settle_s=2, retry_failed=False, …)` | One scan: files matching pattern that are complete (size steady for settle_s, readable) and not yet in the ledger (by SHA-256, so a renamed copy is skipped too). Oldest first. Returns [(path, sha256)]. |
| `watch_folder(folder, handler, pattern='*', ledger=None, run_for_s=0, poll_s=30,…)` | File trigger: handler(path) for each new complete file. Ledger (default &lt;folder&gt;/.processed.jsonl) records done/failed so a restart never redoes a file. BusinessError -&gt; 'failed' (not retried); other errors are retried on the next scan. run_for_s=0 = one scan (best under Task Scheduler). Returns [{'file','status','error'}]. |
| `run_every(fn, every_s, run_for_s=None, max_runs=None, align=True, stop_on_er…)` | Call fn() now and then every every_s seconds (align=True: on clock boundaries, e.g. :00/:15/:30/:45) until run_for_s or max_runs. Overrun -&gt; missed slots are skipped. Errors are logged, loop continues unless stop_on_error. Returns [{'run','started','ok','error'}]. |
| `cron_parse(expr)` | '30 7 \* \* MON-FRI' -&gt; dict of allowed minutes/hours/days/months/weekdays (0 = Sunday). Also @daily etc. |
| `cron_matches(expr, when)` | True if the cron expression fires at this minute. |
| `cron_next(expr, after=None, n=1)` | Next run time after `after` (default now), local wall-clock; n &gt; 1 -&gt; list of the next n. |
| `run_on_cron(fn, expr, run_for_s=None, max_runs=None, stop_on_error=False, now=…)` | In-process scheduler: sleep until each cron time, call fn(). Errors logged, loop continues unless stop_on_error. For production prefer Task Scheduler + single_instance. Returns [{'run','due','ok','error'}]. |
| `cron_line(expr, command, log_file=None)` | crontab line for a Linux server (validates the expression). |
| `schtasks_create(name, command, schedule='DAILY', start_time='07:30', days=None, ev…)` | Windows Task Scheduler entry. schedule: MINUTE \| HOURLY \| DAILY \| WEEKLY \| MONTHLY \| ONCE \| ONLOGON; days='MON,TUE,WED,THU,FRI' (WEEKLY); every=n -&gt; /MO (every 15 with MINUTE). interactive=True (/IT) runs only while you are logged on (needed for Excel/Outlook COM). dry_run=True prints the command; False creates it. |
| `schtasks_delete(name, dry_run=True)` | Remove a scheduled task. |
| `schtasks_run(name, dry_run=False)` | Start a scheduled task now. |
| `schtasks_query(name=None)` | Scheduled tasks (all, or one) -&gt; DataFrame with next/last run time, last result, status. |
| `write_runner_bat(path, script, python_exe=None, args=(), workdir=None, log_file=None)` | .bat for Task Scheduler: go to the robot folder (UNC shares too), run python, append output to a log, pass the robot's exit code on. |
| `WebhookServer(…)` | HTTP trigger: POST JSON to http://127.0.0.1:&lt;port&gt;/webhook -&gt; handler(payload, headers) -&gt; JSON reply. secret= checks the X-Signature HMAC (api.sign_body); token= checks an X-Token header. GET /health -&gt; ok. Keep handler fast (e.g. FileQueue.put and return). `with WebhookServer(fn, port=8085, secret=s) as hook:`. |
| `  .start()` | Serve in a background thread; returns self (.url is the address). |
| `  .serve_forever()` | Serve in the foreground until Ctrl+C. |
| `FileQueue(…)` | Work queue in plain folders (pending / working / done / failed) to hand items between robots or retry later. Claiming is an atomic rename, so two workers never take the same item. Item = JSON-able dict. |
| `  .put()` | Add an item; returns its id. A given item_id already pending/working/done is not added again (None). |
| `  .pending_ids()` | Ids waiting in pending, oldest first. |
| `  .get()` | Claim the oldest pending item (or that item_id) -&gt; dict(id, payload, attempts, ...); None if none left. |
| `  .done()` | Mark a claimed item finished. |
| `  .fail()` | Failed item: back to pending if retry and attempts &lt; max_attempts, else to failed. |
| `  .requeue_stale()` | Items stuck in working (worker crashed) longer than older_than_s go back to pending; returns count. |
| `  .counts()` | {'pending': n, 'working': n, 'done': n, 'failed': n}. |
| `  .items()` | Items in a folder as a DataFrame (id, attempts, error, payload...). |
| `  .process()` | Work the items pending now: handler(payload) -&gt; result. BusinessError -&gt; failed; other errors go back to pending for the next run (up to max_attempts). Returns {'done': n, 'failed': n, 'retry': n}. |

### Workflows (Alteryx-style) — `toolbank/flow.py`

| function | what it does |
|---|---|
| `Flow(…)` | Flow("recon").step("load", read_sheet, "in.xlsx").step("fx", lambda d: d[d.desk == "FX"]).run() -&gt; output DataFrame; .trace = DataFrame(step, rows_in, rows_out, cols, ms, status, error); keep=True stores each step's output in .snapshots. |
| `  .step()` | Add a step: fn(df, \*args, \*\*kwargs) -&gt; DataFrame (the first step gets no df if run() had none). |
| `  .check()` | Stop the flow with a BusinessError when predicate(df) is False, e.g. .check("has rows", lambda d: len(d) &gt; 0). |
| `count_rows(df, label='rows', log=print)` | Log rows x cols and pass df through: df.pipe(count_rows, "after filter"). |
| `tee(df, path, **kw)` | Write df to path (.csv / .xlsx / .parquet) and pass it through, like an Output tool mid-workflow. |
| `cached(path, fn, *args, refresh=False, **kwargs)` | Saved result if path exists, else fn(\*args, \*\*kwargs) saved to path (.parquet / .pkl / .csv) and read back, so the first run and later runs see the same types. refresh=True (or delete the file) rebuilds. Speeds up development on slow inputs. Only load files you created. |

### Speed & size — `toolbank/perf.py`

| function | what it does |
|---|---|
| `parallel_map(fn, items, workers=4, processes=False)` | fn(item) for every item using a pool -&gt; [{'item','ok','result','error'}] in input order; one failure never stops the rest. Threads (default) for waiting work; processes=True for heavy CPU work (fn must live in a .py module, and on Windows the caller needs `if __name__ == "__main__":`). |
| `chunks(seq, size)` | Consecutive lists of `size` items (last one shorter). |
| `timer(label='block', log=print)` | `with timer("load"):` logs elapsed ms; the yielded dict gets 'ms' at the end. |
| `progress(iterable, total=None, every=100, label='', log=print, clock=monoto…)` | Pass items through, logging 'label 300/1,000 (30%) 52/s eta 0:13' every `every` items. |
| `memory_mb(df)` | Memory used by a DataFrame in MB (deep: counts text). |
| `shrink(df, category_ratio=0.5)` | Smaller DataFrame, same values: text columns with few distinct values -&gt; category (group with observed=True). Numbers are left alone (small integer types would overflow in later sums). |

### Recipes (spec -> validated workflow -> generated script) — `toolbank/recipe.py`

| function | what it does |
|---|---|
| `load_recipe(source)` | Recipe from a .yaml / .json path, YAML text, or a dict -&gt; dict. A missing file -&gt; FileNotFoundError; a bad spec -&gt; ValueError listing every problem found by validate_recipe, so it never half-runs. |
| `validate_recipe(recipe, with_input=False)` | Problems with a recipe ([] = OK): unknown functions and parameters (with 'did you mean'), missing required parameters, unresolvable $references, keys YAML turned into booleans, bad pick / as / label, a frame step before any frame exists. with_input=True when run_recipe will be given a frame for the first step. |
| `recipe_to_flow(recipe, inputs=None, name=None, keep=False, log=print)` | Flow for a validated recipe. inputs = {alias: value} already loaded (run_recipe does this for you). Every step is one Flow step, so .trace shows rows in / out and timings; a step that does not return a DataFrame (and has no as:) stops the flow with RobotError RECIPE. |
| `run_recipe(recipe, df=None, keep=False, log=print)` | Load the inputs, run the steps -&gt; (frame, trace DataFrame). Validates first (ValueError lists every problem). df = a frame for the first step when the recipe does not start with a reader. |
| `recipe_to_code(recipe, path=None)` | Plain Python script for a validated recipe (deterministic: same recipe, same text); saved to path if given. The script needs only `from toolbank import *`, so it runs and can be edited without the recipe. Dates from YAML become pd.Timestamp(...), so the script and run_recipe agree. |

### VBA — `vba/*.bas`

| module | what it does |
|---|---|
| `AddNumbers.bas` | Example Function callable from Python: excel_run_macro(path, "AddNumbers", 2, 3) -> 5 |
| `CombineWorkbooksInFolder.bas` | Append the first sheet of every .xlsx in a folder below the active sheet's data (header taken once). |
| `DeleteEmptyRows.bas` | Delete completely empty rows in the used range of the active sheet. |
| `ExportSheetsToCsv.bas` | Export every sheet to its own UTF-8 CSV next to the workbook. |
| `FillDownBlanks.bas` | Fill blanks in the selection from the cell above, then freeze as values. |
| `HighlightDuplicates.bas` | Highlight duplicate values in the selected column(s) with conditional formatting. |
| `RefreshAllAndWait.bas` | Refresh all queries/pivots and wait until finished. |
| `TrimAllCells.bas` | Trim spaces and non-breaking spaces from every text cell in the selection. |
| `UnmergeAndFill.bas` | Unmerge the selection; every cell gets the merged value. |
<!-- INDEX:END -->
