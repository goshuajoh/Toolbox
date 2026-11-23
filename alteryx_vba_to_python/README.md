# Alteryx and VBA to Python

Two command-line tools for any Alteryx workflow and any Excel VBA module:

- **`ayx2py`** reads a workflow (`.yxmd`, or the same XML saved as `.txt`).
- **`vba2py`** reads VBA text (`.bas`, `.cls`, `.txt`), or the macros inside an `.xlsm` / `.xls` without opening Excel.

Each writes two things:

1. **A logic document** that says what the workflow or macro does, step by step, in plain words, with every rule written out. It comes as Markdown (readable as plain text) and as one HTML page for a browser. The HTML page has a diagram of the stages and links between them. It needs no network and runs no scripts.
2. **Python code** that does the same work (pandas for Alteryx, openpyxl for VBA), with every step it could not convert marked and made to stop with an error.

Neither tool depends on a particular workflow or macro. They work from the tool kinds and VBA statements they find. [examples/](examples/) shows both on a sales report and a month-end macro.

`REPORT.md` says how far each conversion can be trusted. Everything here was tested on synthetic workflows and macros only. Nothing has run on Windows or on a real file yet.

## Start with the examples

| Input | Logic document |
|---|---|
| [examples/sales_report.yxmd](examples/sales_report.yxmd) (17 tools) | [sales_report_logic.md](examples/sales_report_logic.md), [.html](examples/sales_report_logic.html) |
| [examples/month_end.bas](examples/month_end.bas) (5 Subs) | [month_end_logic.md](examples/month_end_logic.md), [.html](examples/month_end_logic.html) |

To see the HTML page, download it and open it in a browser.

## Alteryx

```bash
python -m ayx2py workflow.yxmd out_alteryx
```

Run the command from this folder. The output folder must not be the workflow's own folder.

| Output file | What it is |
|---|---|
| `workflow_logic.md` | The logic, stage by stage (see below). |
| `workflow_logic.html` | The same, with a diagram of the stages. Every stage number is a link. |
| `workflow_generated.py` | One Python function per distinct tool, each with its tool ID and original expression as a comment, and `run()`, which calls them in order. |
| `ayx_runtime.py` | The library the generated code calls. The Alteryx behaviours the code has to assume are named switches in `SEM`. |

Options:

- `--no-code`: write only the logic document.
- `--tool-listing`: also write `workflow_tools.txt`, every tool with every setting in flow order. This was the earlier, denser output; keep it for tracing a single tool.
- `--name NAME`: prefix of the output files.
- `--and-before-or`: see probe item P08.

### How the logic document is organised

- **Summary**, **Inputs**, **Outputs**: what the workflow reads, writes and runs.
- **Stage map**: a table (and in HTML a diagram) of the stages, with where each takes its rows from and sends them to.
- **Stages**: numbered steps.
  - A stage is a run of tools in which rows flow straight from one tool to the next. A new stage starts where streams split (a filter with both outputs used, a stream that feeds several tools) or meet (a join, a union, a lookup), and at each container.
  - Each tool becomes a sentence: *Keep the rows where [Status] ≠ "Cancelled"*; *Add **Category** from the first row of stage 5 whose [Product] equals this row's [Product]*.
  - Formulas are written as rules: `when [Gross] ≥ 10000 → 0.1`, one line per condition, with `otherwise` last. Repeated tests are grouped (`[CFICODE] is one of "A", "B", "C"`; `[RISK] contains any of …`; `ABS([X]) between … and …`).
  - Brackets are written where Alteryx applies them. Alteryx reads `a OR b AND c` as `(a OR b) AND c`, and the document shows it that way.
- **Repeats**: a group of stages repeated with identical rules is described once. Each copy is one short section saying which rows it takes and where it sends them.
- **Before relying on this**: tools that did not convert to Python, partial-match lookups, AND/OR mixed without brackets, Data Cleansing settings to confirm, and the Alteryx behaviours the Python assumes.
- **Tool IDs**: `(#1405)` after a step is the tool's ID in Alteryx, for finding it on the canvas.

### Size

Measured on a synthetic 930-tool workflow with a production workflow's mix of tool kinds:

| File | Lines | Characters |
|---|---|---|
| logic document (`.md`) | 3,625 | 163,000 |
| tool listing (`--tool-listing`) | 1,162 | 96,000 |
| generated code | 7,843 | 306,000 |

The logic document is three times longer than the old listing because it uses sentences, one step per line and blank lines between blocks. It is meant to be read with the stage map as a table of contents, which the HTML page makes clickable.

### Running the generated code

```python
import ayx_runtime as rt, workflow_generated as wf

ctx = rt.Context(workflow_dir=r"C:\path\to\workflow_folder", write=False)   # write=False: no file is written
streams = wf.run(ctx)                  # streams["1395"]["Join"] is the J output of tool 1395
result = ctx.outputs["2290"]           # what Output tool 2290 would have written
```

- `inputs={"1348": dataframe_or_path}` replaces what an input tool reads.
- `stubs={"2001": my_function}` supplies an implementation for a tool that was not converted.
- `rt.SEM["JOIN_NULLS_MATCH"] = False` changes an assumed behaviour everywhere.
- A `rt.AlteryxError` means Alteryx would have refused or flagged the same thing (most often a column whose type differs from what the workflow recorded). A `rt.NotConverted` means a stub was reached.

## The probe

`probe/semantics_probe.yxmd` is a small workflow that exercises every Alteryx behaviour the converter assumes. `probe/semantics_probe_expected.txt` holds what the converter's runtime produces for it. Open the `.yxmd` in Designer, run it, and compare each Browse with the expected file. Each line names the switch to change if Designer disagrees.

The `.yxmd` has not been opened in Designer yet. If it does not load, paste the expressions listed in the expected file into a Formula tool.

## VBA

```bash
python -m vba2py Configuration.xlsm -o out_vba
```

```bash
python -m vba2py Module1.bas Module3.bas -o out_vba
```

A workbook's macros are read without Excel. If that fails on a real file, export the modules from the VBA editor (File > Export File) and pass the `.bas` files.

| Output file | What it is |
|---|---|
| `Configuration_logic.md` | Summary, what calls what, the files and settings cells used, then each Sub. |
| `Configuration_logic.html` | The same as a page; Sub names are links. |
| `Configuration_generated.py` | One Python function per Sub, taking `app`; statement for statement the same as the VBA, with line numbers. |
| `xl_runtime.py` | A stand-in for Excel's object model on openpyxl. Its header says what it does not do: calculate arbitrary formulas, measure AutoFit, save the way Excel does. |
| `Configuration_vba_source.txt` | The VBA text, when it was read out of a workbook. |

Each Sub in the logic document starts with what it **reads** (cells used as settings), **opens**, **reports** (status cells, from the error handler), and the **Excel settings** it changes. Then come its **steps**, and **if any step fails**.

Recorded-macro noise is folded away:

- **Selecting and activating** are not steps: every step names its cells, for example `[{source_file}]{source_sheet}!A2:AQ{intActiveRow}`, where names in braces are values the macro worked out while running.
- **Copy and paste** become one step: *Copy … to Lifecycle!AJ3 (values only)*.
- **Conditional formatting**: the property lines behind each rule become one line with its condition, colour and final priority.
- **R1C1 formulas** are also shown in A1 notation.
- **Subs that differ only in a few cells** are described once: *Same steps as FormatTradesA, except: B2 → D2; F2 → H2*.

Size: 355 lines of VBA (11 procedures) give a 233-line logic document and 332 lines of Python.

Running the generated code, on copies of the workbooks:

```python
import xl_runtime as xl, Configuration_generated as macros

app = xl.App(r"C:\copy\Configuration.xlsm", save=False)   # save=False: Save calls are logged, not done
macros.FormatAll(app)
print(app.log)
```

## What is in the folder

| Path | What it is | Needed to run the tools |
|---|---|---|
| `ayx2py/` | Alteryx converter | yes |
| `vba2py/` | VBA converter | yes |
| `logicdoc.py` | Writes the logic documents (Markdown, HTML, diagram) | yes |
| `examples/` | A sales-report workflow and a month-end macro, with their logic documents | no |
| `probe/` | The workflow that checks the assumed Alteryx behaviours | once |
| `tests/`, `run_tests.py` | 73 tests on synthetic input | optional |
| `REPORT.md` | Feasibility report | no |

The tools use the standard library only (written and tested on Python 3.12). The code they generate needs pandas (Alteryx side) and openpyxl (VBA side, and Excel files on the Alteryx side).

## Tests

```bash
python run_tests.py
```

73 tests, about 6 seconds. They need pandas and openpyxl, and no real data. `python examples/make_examples.py` and `python probe/make_probe.py` re-create the example and probe files; the tests check that they are current.
