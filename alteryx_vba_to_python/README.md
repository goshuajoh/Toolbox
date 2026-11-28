# Alteryx and VBA to Python

Two command-line tools for any Alteryx workflow and any Excel VBA module:

- **`ayx2py`** reads a workflow (`.yxmd`, or the same XML saved as `.txt`).
- **`vba2py`** reads VBA text (`.bas`, `.cls`, `.txt`), or the macros inside an `.xlsm` / `.xls` without opening Excel.

Each writes two things:

1. **A document that explains the logic**, written as plain Markdown (readable as text) and as one HTML page for Chrome or any other browser. The page needs no network and runs no scripts; its sections fold open and closed.
   - For Alteryx it is a **migration guide** for an engineer who has never opened Designer: what each container does and the files (sheet, cells) it opens and saves, then one compact table per output with the steps that change its rows or values, each with its rule.
   - For VBA it is a step-by-step description of each Sub.
2. **Python code** that does the same work (pandas for Alteryx, openpyxl for VBA). Every step it could not convert is marked and stops with an error when reached. For Alteryx there is also a **pandas notebook template**: it loads every file the workflow opens and has one cell per branch, with the steps as comments to paste the guide's pandas lines under, so the workflow can be rebuilt and checked step by step.

Neither tool depends on a particular workflow or macro. They work from the tool kinds and VBA statements they find.

`REPORT.md` says how far each conversion can be trusted. Everything here was tested on synthetic workflows and macros only. Nothing has run on Windows or on a real file yet.

## Start with the examples

| Input | Document |
|---|---|
| [examples/dataset_audit.yxmd](examples/dataset_audit.yxmd): 3 containers, one with a nested container; one input fans out into six annotated filter branches that meet in a Union | [dataset_audit_guide.md](examples/dataset_audit_guide.md), [.html](examples/dataset_audit_guide.html); notebook template [dataset_audit_steps.ipynb](examples/dataset_audit_steps.ipynb) |
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
| `workflow_guide.html` | The same page for a browser, readable at phone width and in dark mode. Each step's rule and pandas, a lookup's own preparation, and the Detail section fold open and closed; every output, working table and container name is a link. |
| `workflow_generated.py` | One Python function per distinct tool, each with its tool ID and original expression as a comment; `STEPS`, every tool in run order with its container and step number; and `run()`, which runs them all or one container at a time. |
| `workflow_steps.ipynb` | A plain pandas notebook to rebuild the workflow by hand, branch by branch (see [Rebuilding it in pandas, step by step](#rebuilding-it-in-pandas-step-by-step)). |
| `ayx_runtime.py` | The library the generated code calls. The Alteryx behaviours it has to assume are named switches in `SEM`. |

Options:

- `--no-code`: write only the documents and the pandas notebook (no generated code or runtime).
- `--brief`: also write `workflow_logic.md` and `.html`, a shorter stage-by-stage summary without cards.
- `--runner-notebook`: also write `workflow_test.ipynb`, which runs the generated code container by container on your files (see [Running the generated code container by container](#running-the-generated-code-container-by-container)).
- `--tool-listing`: also write `workflow_tools.txt`, every tool with every setting in flow order, for tracing a single tool.
- `--name NAME`: prefix of the output files.
- `--enabled-only`: leave out containers that are disabled in the saved file. By default they are documented and converted like the others: teams often run one container at a time by disabling the rest before saving.
- `--diagnose REPORT.txt`: also write a short structure-only report: tool kinds, containers (enabled or disabled, size, nesting), anchors, loops, dropped ordering preferences, and every error the converter met. It contains no rules, file names, field names, captions or notes, so it can be shared when the workflow cannot.
- `--and-before-or`: see probe item P08.

### How the guide is organised

The guide is written for someone who has never opened Designer, and it reads without a legend. A contents list at the top shows the whole shape: the containers, and each container's outputs.

1. **What this workflow does**: one block per container. A one-line summary ("It opens 3 sources, filters rows, matches tables and saves 6 tables, in 319 steps"), then what it **opens**, **saves** and **runs**, one line per file with its sheets after it ("`Breaks.xlsx` › By desk, In USD"), and which containers it uses or feeds (by a connection, or through a file one saves and another opens). A disabled container keeps its badge.
2. **How each output is made**: one compact table per output (file › sheet), read from the files it starts from to the save, in run order. **A row is shown only for a step that changes which rows or which values reach the output**: a filter, a rule that sets columns, data cleansing, a match or lookup with another table, stacking, totals, duplicates removed, a sample, a reshape, a Select that renames or removes columns, a sort that the result depends on, a row number that is used, a Run Command, and the save. Each row gives:
   - what happens, with the rule: in words where that is safe ("the last 2 characters of [A] equal [B]", "[A] with ".xlsx" removed"), otherwise the Alteryx rule as written (a long one shows its start; all of it is under **Rule and pandas**);
   - the columns it sets, renames (→) or removes (−);
   - in small print, the Designer tool, its step number and tool ID (to find it in Designer and in the steps notebook);
   - for a split, where each part goes ("rows that pass → row 4 · rows that fail → **Table B**"; for a join, "matched rows", "rows only in the first input", "rows only in the second input");
   - **Rule and pandas**, folded: the exact rule, the same step in pandas, and whether the generated Python converts it exactly.

   **Presumed, never a row of their own:** opening the file a table starts from (it is named at the top: "Starts from: `Trades.csv`", or "every file in `Input` named `*.csv`, each read like `template.csv`"); a Select that only keeps, reorders or retypes nothing; Block Until Done (see Run order); a sort nothing depends on; a row number nobody uses; a lookup's own file and filters (said in the step that uses it: "Matches the rows with `Rates.xlsx › Rates`, prepared in rows 1a–1b (only rows where [Rate] is not empty) on [Ccy]"); a settings value attached to every row by Append Fields (named where it is used: "the setting [T-1 Sheet] (`Settings.xlsx › Settings`)"). Nothing disappears: each table ends with a folded "N housekeeping steps not shown" that lists them with their pandas.

   To keep a 300-tool container readable:
   - **Neighbours of one kind are one row**: Formulas in a row ("Sets 3 columns", one line each), Selects in a row, filters in a row ("and").
   - **Parallel paths stacked by a Union are one row**, with a small table, one line per path (which rows, what is done to them). Fifty filter-and-label checks are one row, not 150.
   - **Shared work is told once** as **Table A, B…** (per container), with a line on where it comes from and its last action (or the annotation a person wrote). The tables that use it start from it.
   - **Repeated saves to one sheet** are one block: "Saved 10 times in this container. Each save replaces the last, so all but the last are most likely there only to control the run order."
   - **A Run Command** is described from its own settings: the program and arguments, or, when it writes its incoming rows to a file and runs that, "Writes the text in [Script] to `run_import.bat` and runs it".
   - **Other places are named, not numbered**: an output, Table A, a lookup's file; inside a table, "row 3". A step number appears only in small print.

   Steps that feed no output get one line at the end of their container, with the steps folded under it.
3. **Run order**: which container must run after which (and why), what a Block Until Done tool forces, programs run after a save, and the disabled containers.
4. **Files, sheets and scripts**: one table of every file and program: sheet, cells, how it is used, its columns, and the story that uses it.
5. **Detail**, folded: how to read the pandas, the two helpers it uses (`eq`: Alteryx compares text ignoring case; `empty`: null or ""), **Before relying on this** (steps not converted, partial-match lookups, AND/OR mixed without brackets, Block Until Done ordering, the Alteryx behaviours the Python assumes, A1, A2…), the columns each container creates or changes and where each is used later, and every step by number.

About the pandas lines: `df` is the table flowing through the step; other tables are named after the step that made them (`s2_7`, or `s2_7_true` for its rows that pass); `src_…` is a file as the notebook's Load cell reads it, and `FOLDER` the workflow's folder. They are written to be pasted into the steps notebook, which is laid out by Designer's branches; find a step in the guide by its number. Where an expression has no honest pandas form (a `\G` regex, `FindString`, a multi-row reference), the line is a comment naming the generated function instead. The tests check that the one-liners compute what the converter's runtime computes for the common expressions, and that the notebook runs top to bottom with every line pasted in.

### What the guide does with awkward workflows

- **Containers disabled in the saved file** are documented and converted in full, with a "disabled in the saved file" badge; the summary counts them. The generated `run(ctx)` skips them, as Alteryx does, and `run(ctx, only=3)` runs one. In both notebooks they come in a last section, after Save and compare.
- **Run order** follows the connections. Block Until Done only decides which ready branch goes first. A Block Until Done preference that the connections contradict is dropped and listed ("could not honour: step 1.123 before step 1.120"). It never shows up as a loop. Only a real loop in the connections is reported as one, with its connections.
- **Dynamic Input** "read a list of data sources" is described and converted for each way the listed value is used: the whole path, a file name in the template's folder, or a prefix or suffix on the template's file name. The template's sheet is kept. Any other mode is described and marked not converted.
- **Run Command** is described as a step ("runs `ImportAll.vbs` outside Alteryx: write this step by hand"), with its program, arguments, folder and the files named in its settings. In the generated code it only logs and passes its rows on, so the container keeps running. The last section and the command line list these steps once.
- **If the converter fails on a step**, the step is still listed with its tool, what it does, and its settings from the file. The last section names the error and where it came from.

### Long and deeply nested rules

There is no length or depth limit. The converter does not recurse on Python's call stack (each recursive step is a generator driven by a loop), so a rule with 20,000 terms or 10,000 nesting levels needs neither a deeper stack nor a higher recursion limit. What it writes is flat too:

- **AND/OR of one kind:** one test. A filter that ORs 5,000 `[Cpty] = "C…"` tests reads "[Cpty] is one of …" in the guide and is `df["Cpty"].str.casefold().isin([...])` in pandas.
- **AND and OR mixed, or + and - mixed:** Alteryx reads these left to right: `a OR b AND c` is `(a OR b) AND c`. The guide lists such a rule top down, one term per line, each line joined to everything above it. The Python computes it a statement at a time (`v = a`, then `v = v or b`, then `v = v and c`), and stops early as Alteryx does. The pandas lines do the same with a mask (`t1 = …`, `t1 = t1 | …`, `t1 = t1 & …`).
- **Nested IF, functions and brackets:**
  - `IF … ELSE IF … ENDIF ENDIF` becomes one IF with ELSEIFs.
  - Parts nested deeper than 50 levels move into small helper functions (Python) or variables (pandas), so Python never sees deep nesting.

If the Python for a step still cannot be made, only that step's Python line says "not converted". Its row, what it does, its columns and its whole rule stay in the guide. The command line's larger stack and recursion limit remain only as a safety net.

### Size

Measured on a synthetic 930-tool workflow with a production workflow's mix of tool kinds:

| File | Lines | Characters |
|---|---|---|
| migration guide (`.md`) | 8,378 | 528,000 |
| migration guide (`.html`) | 4,887 | 858,000 |
| pandas notebook | 560 cells (298 code) | |
| stage summary (`--brief`) | 3,612 | 157,000 |
| tool listing (`--tool-listing`) | 1,100 | |
| generated code | 7,858 | 299,000 |

The guide tells 868 steps in 452 rows over 45 tables (outputs and working tables); every step appears once, as a row or in its table's housekeeping list. Each copy of the repeated 47-tool scenario tree is told in its own output's table. On screen, use the HTML page: the pandas and Detail start folded, and the contents list links to every output.

### Rebuilding it in pandas, step by step

On Windows, with Python 3.12 and VS Code (with its Jupyter extension). Once:

```bash
python -m pip install pandas openpyxl ipykernel
```

Write the guide and the notebook into a new folder (here the workflow is `C:\work\flow\Workflow.yxmd`):

```bash
cd C:\path\to\Toolbox\alteryx_vba_to_python
```

```bash
python -m ayx2py C:\work\flow\Workflow.yxmd C:\work\flow_py --name flow
```

```bash
code C:\work\flow_py\flow_steps.ipynb
```

Open `flow_guide.html` in a browser next to it. The notebook uses pandas only:

1. **Settings**: `FOLDER` is the workflow's folder; below it, one variable per file the workflow opens (`DATASETS_EXPORT_CSV = os.path.join(FOLDER, "Input", "datasets_export.csv")`), with the steps that open it. Change a path to your own copy.
2. **Helpers**: the guide's `empty` and `eq`.
3. **Load**: each file read once, the way the workflow reads it (sheet, header row, cells), into a `src_…` table, and its shape printed.
4. **One cell per branch**, under a table of its steps. The cell starts from the table the branch receives and lists the steps as comments:

   ```python
   # Branch 2.5 — Data Quality
   df = s2_6
   # 2.16 Filter “Data Quality” — keeps only the rows where [DQScore] < 0.8   (paste the pandas lines from the guide)

   # 2.17 Formula — fills column Issue = "Low data quality: " + ToString([DQScore], 2)   (paste the pandas lines from the guide)

   # 2.18 RegEx — applies the pattern `(\w+)_v\d+` to [Name] (ParseSimple)   (paste the pandas lines from the guide)

   s2_18 = df
   df.head()
   ```

   Paste each step's lines from the guide's folded line under the branch's table, run the cell, and compare `df.head()` with what Designer shows for that tool. The cells come in an order where every table a cell starts from was made by an earlier cell.
5. **Save and compare**: the `to_excel` line for each Output step (commented out; it writes into `OUT`), and a few lines that compare one output with the file the Alteryx workflow saved.

### Running the generated code container by container

With `--runner-notebook`, `flow_test.ipynb` runs the generated code (not your own pandas) one container at a time on your files:

1. **Settings** cell: `INPUTS` already lists every Input step with the path the workflow uses, and its step number and container. Change each path to a copy of that file (an Excel sheet is `book.xlsx|||Sheet1$`, a range `book.xlsx|||Sheet1$A1:F200`). `WRITE = False` keeps every output in memory. `CHAIN = True` lets an Input that reads a file an earlier Output wrote take that Output's rows.
2. **Run All.** Each container has one cell, which prints one line per step (rows and columns out, or the error). A step that fails is recorded and only the steps that need its rows are skipped. To get past a step that is not converted, give it a stand-in in `STUBS` and re-run that container's cell.
3. `look("2.16")` shows a step's columns (with their Alteryx types) and first rows; `look("2.16", output="False")` shows a filter's other output.
4. The last cell compares each Output step with the file the workflow wrote: `compare("2.27", r"C:\legacy\Dataset audit.xlsx", "Issues")`. Text is compared ignoring case, null and "" count as the same, and numbers may differ by a relative 1e-6. `key=["ID"]` lines rows up by key columns.

If that notebook cannot import `flow_generated`, set `CODE_DIR` in its settings to the output folder.

### Running the generated code

```python
import ayx_runtime as rt, workflow_generated as wf

ctx = rt.Context(workflow_dir=r"C:\path\to\workflow_folder", write=False)   # write=False: no file is written
streams = wf.run(ctx)                  # streams["1395"]["Join"] is the J output of tool 1395
result = ctx.outputs["2290"]           # what Output tool 2290 would have written
s = wf.run(ctx, only=2, s=streams, keep_going=True, report=True)   # container 2 again, one line per step
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
| `examples/` | Two workflows and a macro, with their documents, and a pandas notebook template | no |
| `probe/` | The workflow that checks the assumed Alteryx behaviours | once |
| `tests/`, `run_tests.py` | 123 tests on synthetic input | optional |
| `REPORT.md` | Feasibility report | no |

The tools use the standard library only (written and tested on Python 3.12). The code they generate needs pandas (Alteryx side) and openpyxl (VBA side, and Excel files on the Alteryx side).

## Tests

```bash
python run_tests.py
```

123 tests, about 6 seconds. They need pandas and openpyxl, and no real data. `python examples/make_examples.py` and `python probe/make_probe.py` re-create the example and probe files; the tests check that they are current.
