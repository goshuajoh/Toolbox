# Alteryx and VBA to Python

Two command-line tools. Each reads its source without changing it and writes a logic specification and Python code.
`REPORT.md` says how far each conversion can be trusted and what is still unproven. Read its sections 1 and 9 first.

Everything here was tested on synthetic workflows and macros only. Nothing has run on Windows or on a real file yet.

## What is in the folder

| Path | What it is | Needed to run the tools |
|---|---|---|
| `ayx2py/` | Alteryx converter (7 files) | yes |
| `vba2py/` | VBA analyser (7 files) | yes |
| `probe/` | A small workflow that tests the Alteryx behaviours the converter assumes, and the answers it expects | yes, once |
| `tests/`, `run_tests.py` | 64 tests on synthetic input | optional |
| `REPORT.md` | Feasibility report | no |

The two tool folders are 14 text files, about 270,000 characters. The tools themselves use the standard library only (Python 3.12). The code they generate needs pandas (Alteryx side) and openpyxl (VBA side, and Excel files on the Alteryx side).

## Alteryx

```bash
python -m ayx2py workflow.yxmd out_alteryx
```

The workflow may be a `.yxmd` or the same XML saved as `.txt`. Run the command from this folder. The output folder must not be the workflow's own folder.

| Output file | What it is |
|---|---|
| `workflow_spec.txt` | The logic specification. Top: tool counts, how many tools converted, what to check, inputs, outputs, forced run order, disabled containers. Then rules used more than once (`R1`, `R2`, …), repeated sub-chains (`B1`, …), and the flows by container in dependency order. Every rule is printed in full, once. |
| `workflow_generated.py` | One Python function per distinct tool, with the tool ID and original expression as a comment, and `run()` which calls them in order. A tool that could not be converted is a function that raises. |
| `ayx_runtime.py` | The library the generated code calls. All Alteryx behaviour lives here, as named switches in `SEM`. |

Options: `--width N` (wrap the specification at N characters, default 150), `--name NAME`, `--spec-only`, `--and-before-or` (see probe item P08).

Size, measured on a synthetic 930-tool workflow with a production workflow's mix of tool kinds, one 47-tool scenario tree repeated four times, and every other rule unique:

| File | Lines | Characters |
|---|---|---|
| specification | 1,162 | 96,000 |
| generated code | 7,843 | 306,000 |

Unique tools cost about 1.3 lines each; each repeated copy of a sub-chain costs one line. A real workflow has longer expressions, so expect 1,300 to 2,500 lines.

Reading the specification:

```text
#1395 Join <L←#2545.T, R←#2547.T  : keys: Left DEAL ID = Right DEAL ID  |  R1
B1 #1405 Filter <#1403  : Contains([SYSB_RISKCLASS],"TYPEA") OR …
[B1 copy 2 of 4] 47 tools, same rules and wiring as block B1; each tool ID = B1's +200; fed by #1599→#2221; feeds #2260→#2300
```

`<` lists the inputs. `.T` / `.F` are a filter's outputs, `.L` / `.J` / `.R` a join's, `.U` / `.D` a Unique's. Lines tagged `B1` are the first copy of a repeated sub-chain. `!! NOT CONVERTED` marks a stub.

Running the generated code:

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

`python probe/make_probe.py` re-creates both files; it needs the `tests/` folder and pandas.

## VBA

```bash
python -m vba2py Configuration.xlsm -o out_vba
```

```bash
python -m vba2py Module1.bas Module3.bas -o out_vba
```

A workbook is read without Excel. If that fails on a real file, export the modules from the VBA editor and pass the `.bas` files.

| Output file | What it is |
|---|---|
| `Configuration_vba_spec.txt` | Call tree, workbooks opened, statements that could not be converted, then each Sub as a list of steps with targets resolved, for example `paste values → [ThisWorkbook]Lifecycle!AJ3 (from …AT3:AT10000)`. Subs that differ only in cell addresses are printed once. |
| `Configuration_generated.py` | One Python function per Sub, taking `app`. Statement for statement the same as the VBA, with line numbers. |
| `xl_runtime.py` | A stand-in for Excel's object model on openpyxl. Read its header for what it does not do (calculate arbitrary formulas, measure AutoFit, save like Excel). |
| `Configuration_vba_source.txt` | The VBA text, when it was read out of a workbook. |

Size, measured on 355 lines of VBA (11 procedures): specification 174 lines, generated code 332 lines.

Running the generated code, on copies of the workbooks:

```python
import xl_runtime as xl, Configuration_generated as macros

app = xl.App(r"C:\copy\Configuration.xlsm", save=False)   # save=False: Save calls are logged, not done
macros.FormatAll(app)
print(app.log)
```

## Tests

```bash
python run_tests.py
```

64 tests, about 6 seconds. They need pandas and openpyxl, and no real data.
