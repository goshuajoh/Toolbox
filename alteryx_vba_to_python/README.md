# Alteryx and VBA to Python

Two command-line tools for any Alteryx workflow and any Excel VBA module:

- **`ayx2py`** reads a workflow (`.yxmd`, or the same XML saved as `.txt`).
- **`vba2py`** reads VBA text (`.bas`, `.cls`, `.txt`), or the macros inside an `.xlsm` / `.xls` without opening Excel.

Each writes two things:

1. **A document that explains the logic**, written as plain Markdown (readable as text) and as one HTML page for Chrome or any other browser. The page needs no network and runs no scripts; its sections fold open and closed.
   - For Alteryx it is a **migration guide**: containers, their branches, and one card per tool with fixed fields, for an engineer who has never opened Designer.
   - For VBA it is a step-by-step description of each Sub.
2. **Python code** that does the same work (pandas for Alteryx, openpyxl for VBA). Every step it could not convert is marked and stops with an error when reached.

Neither tool depends on a particular workflow or macro. They work from the tool kinds and VBA statements they find.

`REPORT.md` says how far each conversion can be trusted. Everything here was tested on synthetic workflows and macros only. Nothing has run on Windows or on a real file yet.

## Start with the examples

| Input | Document |
|---|---|
| [examples/dataset_audit.yxmd](examples/dataset_audit.yxmd): 3 containers, one with a nested container; one input fans out into six annotated filter branches that meet in a Union | [dataset_audit_guide.md](examples/dataset_audit_guide.md), [.html](examples/dataset_audit_guide.html) |
| [examples/sales_report.yxmd](examples/sales_report.yxmd): 17 tools | [sales_report_guide.md](examples/sales_report_guide.md), [.html](examples/sales_report_guide.html) |
| [examples/month_end.bas](examples/month_end.bas): 5 Subs | [month_end_logic.md](examples/month_end_logic.md), [.html](examples/month_end_logic.html) |

To see an HTML page, download it and open it in a browser.

## Alteryx

```bash
python -m ayx2py workflow.yxmd out_alteryx
```

Run the command from this folder. The output folder must not be the workflow's own folder.

| Output file | What it is |
|---|---|
| `workflow_guide.md` | The migration guide (see below). |
| `workflow_guide.html` | The same page for a browser. Containers, nested containers and branches fold open and closed; every step, branch and container number is a link; each container has a diagram of its branches. |
| `workflow_generated.py` | One Python function per distinct tool, each with its tool ID and original expression as a comment, and `run()`, which calls them in order. |
| `ayx_runtime.py` | The library the generated code calls. The Alteryx behaviours it has to assume are named switches in `SEM`. |

Options:

- `--no-code`: write only the documents.
- `--brief`: also write `workflow_logic.md` and `.html`, a shorter stage-by-stage summary without cards.
- `--tool-listing`: also write `workflow_tools.txt`, every tool with every setting in flow order, for tracing a single tool.
- `--name NAME`: prefix of the output files.
- `--and-before-or`: see probe item P08.

### How the guide is organised

1. **How to read this**, with the two pandas helpers the one-liners use: `eq` (Alteryx compares text ignoring case) and `empty` (null or "").
2. **Contents**: every container and branch, as links.
3. **Containers**, one table row each:
   - whether it runs (disabled containers are listed, not described);
   - the files it reads and writes, and the commands it runs;
   - which other containers feed it and which it feeds, by a connection or through a file one container writes and another reads (a Directory tool counts when the file is in its folder and matches its pattern).
   - The HTML page adds a diagram.
4. **One section per container**:
   - **Facts**: reads, writes, fed by, feeds, nested containers, steps not converted.
   - **Branches**: a table, and a diagram in HTML. A branch is a chain of tools with no split or merge, as Designer draws it. It is named after its first tool's annotation, its filter test, or what it does ("Join on [DEAL ID]", "passes: [Status] ≠ "Cancelled""). Branches are numbered in the order the rows flow (branch 3.4); a nested container's branches are grouped under "Inside …" at their place in the flow.
   - **Steps**: one card per tool, numbered within the container (step 3.14), with:
     - **In**: where the rows come from, with the output used (True / False of a filter; J matched, L left only, R right only of a join).
     - **Does**: one sentence.
     - **Rule**: the full expression, as `when … → …` lines.
     - **Reads**: the columns used.
     - **Writes**: the columns created or changed, with the type Alteryx recorded (and "new" or "changed" when the workflow recorded the incoming columns).
     - **Out**: where the rows go; an output nothing uses says "these rows are dropped".
     - **Python**: the pandas form of the step in one or a few lines, its conversion status, and the name of the exact function in the generated module.
   - **Columns created or changed**: each column, its type, the steps that set it, how, and every later step (anywhere in the workflow) that reads a column of that name, to trace a variable end to end.
5. **Repeats**: branches that repeat earlier ones with identical rules and wiring are one short section: which steps they mirror ("2.34–59 here is 2.8–33 there") and which rows they take instead.
6. **Before relying on this**:
   - steps not converted;
   - partial-match lookups;
   - AND/OR mixed without brackets;
   - Block Until Done ordering;
   - the Alteryx behaviours the Python assumes, numbered A1, A2… and referred to from the cards.

About the pandas lines: `df` is the table flowing through the branch; other tables are named after the step that made them (`s2_7`, or `s2_7_true` for a True output). They are written to be read and copied, not run as a whole. Where an expression has no honest one-line form (a `\G` regex, `FindString`, a multi-row reference), the card names the generated function instead. The tests check that the one-liners compute what the converter's runtime computes for the common expressions.

### Size

Measured on a synthetic 930-tool workflow with a production workflow's mix of tool kinds:

| File | Lines | Characters |
|---|---|---|
| migration guide (`.md`) | 11,724 | 514,000 |
| migration guide (`.html`) | 9,608 | 935,000 |
| stage summary (`--brief`) | 3,625 | 163,000 |
| tool listing (`--tool-listing`) | 1,162 | 96,000 |
| generated code | 7,843 | 306,000 |

The guide has 731 cards: every tool once; the three repeated copies of the 47-tool scenario tree are not printed again. On screen, use the HTML page: everything starts folded, the contents and the container table are links, and Chrome opens the folded sections when a link points inside them.

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

Each Sub in the VBA document starts with what it **reads** (cells used as settings), **opens**, **reports** (status cells, from the error handler), and the **Excel settings** it changes. Then come its **steps**, and **if any step fails**.

Recorded-macro noise is folded away:

- **Selecting and activating** are not steps: every step names its cells, for example `[{source_file}]{source_sheet}!A2:AQ{intActiveRow}`, where names in braces are values the macro worked out while running.
- **Copy and paste** become one step: *Copy … to Lifecycle!AJ3 (values only)*.
- **Conditional formatting**: the property lines behind each rule become one line with its condition, colour and final priority.
- **R1C1 formulas** are also shown in A1 notation.
- **Subs that differ only in a few cells** are described once: *Same steps as FormatTradesA, except: B2 → D2; F2 → H2*.

Size: 355 lines of VBA (11 procedures) give a 233-line document and 332 lines of Python.

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
| `logicdoc.py` | Writes the documents (Markdown, HTML with folding sections, diagrams) | yes |
| `examples/` | Two workflows and a macro, with their documents | no |
| `probe/` | The workflow that checks the assumed Alteryx behaviours | once |
| `tests/`, `run_tests.py` | 82 tests on synthetic input | optional |
| `REPORT.md` | Feasibility report | no |

The tools use the standard library only (written and tested on Python 3.12). The code they generate needs pandas (Alteryx side) and openpyxl (VBA side, and Excel files on the Alteryx side).

## Tests

```bash
python run_tests.py
```

82 tests, about 6 seconds. They need pandas and openpyxl, and no real data. `python examples/make_examples.py` and `python probe/make_probe.py` re-create the example and probe files; the tests check that they are current.
