# Converting Alteryx workflows and Excel VBA to Python without a model: what is feasible

Written 21 Nov 2026. Everything measured below was measured on **synthetic** workflows, macros and workbooks built for this study on a Mac (Python 3.12.4, pandas 2.3.3, openpyxl 3.1.5). No real `.yxmd` or `.xlsm` was available. The "reference workflow" is a 938-tool production workflow whose tool counts and kinds of rule the synthetic ones copy; it is not part of this repository. Section 9 lists what that leaves unproven.

## 1. Recommendation

| | Outcome 1: generate Python directly | Outcome 2: extract the logic for a person or a model | Recommendation |
|---|---|---|---|
| **Alteryx** | Works for every data tool in the reference workflow's mix. On a 930-tool synthetic workflow with the reference workflow's tool counts, 857 of the 872 tools that run became Python and 15 (all Run Command) became stubs that raise. | Works. The same 930 tools give a 3,625-line logic document: stages of plain-language steps, every rule in full, repeats described once. | **Use both, with the generated code as the baseline.** Generate the code, settle the open Alteryx behaviours with the probe (section 5), then prove parity against a real run. Use the logic document to review, and to write cleaner code later if wanted. |
| **VBA** | Works for the mechanical idioms: 208 of 212 statements in macros written in the style of real recorded macros. The result imitates Excel statement by statement, on a library that is not Excel. | Works. 355 lines of VBA give a 233-line logic document with every Select/Activate chain resolved to a workbook, sheet and range. | **Outcome 2.** Implement from the logic document. Keep the generated code as a cross-check to run on copies of the workbooks, not as the code to maintain. |

Why the two answers differ:

- An Alteryx workflow is a closed data-flow language. Each tool is a function from tables to tables, and the whole configuration is in the XML. Translation is mechanical. What is uncertain is a short list of engine behaviours that Alteryx does not document (about 15, section 5). Each is one switch in one file, and each can be settled by one run in Designer.
- A VBA macro drives a live application. Its results depend on Excel's calculation engine, on the fonts used by AutoFit, on what Excel keeps when it saves, and on other programs such as Outlook. None of that is in the macro text. A translator can reproduce the macro's statements exactly and still not reproduce Excel.

"Exact" in this report means: the generated code follows behaviour that Alteryx documents, or that real output confirmed, and tests on synthetic data pass. It does not mean parity with a real run has been shown. That needs the work laptop.

## 2. What was built

| Tool | Reads | Writes | Needs |
|---|---|---|---|
| `ayx2py` | `.yxmd`, or the same XML saved as `.txt` | logic document (Markdown and HTML with a stage diagram); Python module with one function per tool; `ayx_runtime.py`; optional tool listing | standard library only |
| `vba2py` | VBA text, or `.xlsm` / `.xls` / `vbaProject.bin` read without Excel | logic document per Sub (Markdown and HTML); Python on openpyxl; `xl_runtime.py`; the extracted VBA text | standard library only |
| generated code | | | pandas (Alteryx side), openpyxl (VBA side, and Excel files on the Alteryx side) |

Both tools only read their sources. 73 tests pass (`python run_tests.py`). Neither depends on a particular workflow or macro: `examples/` runs them on a sales report and a month-end macro, and a test runs the sales report's generated Python against a hand-computed result.

Design decisions that matter when judging the result:

- **The expression translator is a tokenizer and a recursive-descent parser** (`ayx2py/expr.py`), not text substitution. It refuses what it does not know: an unknown function or a multi-row reference turns the whole tool into a stub.
- **Row logic does not use pandas operators.** Frames are pandas DataFrames, but every comparison, concatenation, sort and type change goes through `ayx_runtime.py`, one row at a time. pandas' own rules for nulls, text comparison, sort order and type promotion differ from Alteryx's, and those differences are what decides the output in practice. The cost is speed, which does not matter at a few hundred thousand rows.
- **Dates are ISO text inside frames** (`2026-11-17`), as in Alteryx itself, so date comparisons and `ToDate` behave as they do there.
- **A type mismatch is an error, not a guess.** `[Branch] = '101'` on a column that arrived as a number raises, with the field named. Alteryx would also reject it. The field types that Alteryx recorded in the workflow are applied to each input, so this fires only when an input really differs from what Alteryx saw.
- **Nothing passes through silently.** An unconverted tool, an unknown VBA member, a formula outside the evaluator's list, an XML layout that lacks an expected setting: each becomes an error that names the tool or line. A stub can be replaced by supplying a function for that tool ID.
- **Output tools write nothing by default** (`Context(write=False)`), and `xl.App(save=False)` records saves without doing them.

## 3. Alteryx tools

Counts are those of the reference workflow, a 938-tool production workflow. "Exact" and "caveat" are as defined in section 1.

| Tool (count in the reference workflow) | Converts | Notes |
|---|---|---|
| Formula (297) | exact | Each field is a Python function carrying the original expression as a comment. Result is converted to the field's declared type. See section 4 for the expression language. |
| Filter (83) | exact | Custom and basic mode. In basic mode the operand is read as the field's type. A null result goes to False. |
| Select (75) | exact for rename, drop, reorder, `*Unknown` | Type changes follow assumed rules for bad numbers and rounding (section 5). |
| Summarize (64) | caveat | Group By, Sum, Count, Count Non Null, Count Null, Count Distinct, Min, Max, First, Last, Avg, Median, Concat. Other actions become stubs. Not documented: output order, Sum of only nulls, Concat of nulls. |
| Find Replace (51) | caveat | Implemented as what it is: a substring match of each Source value inside the Target field, in Source order, case-sensitive unless ticked. It equals a join only when no searched value occurs inside another; every "any part of field" use is listed in the logic document. Which Source row wins on several matches is not documented. |
| Input Data (47) | caveat | csv, txt, xlsx, xlsm. Column types come from Alteryx's file driver; the types recorded in the workflow are applied when present. `.yxdb` and databases become stubs. |
| CReW Parallel Block Until Done (43) | caveat | Data passes through. The run order it enforces becomes ordering constraints on `run()` and is listed in the logic document. Anchor names are inferred from the connections. |
| Append Fields (34) | exact | Every Target row with every Source row, with the embedded Select. |
| Union (33) | caveat | By name and by position, with the configured order. Not documented: the type when inputs disagree (assumed: any string makes it a string, numbers are rewritten as text) and the order without an explicit setting (assumed: connection order). Manual mapping becomes a stub. |
| Multi-Field Formula (33) | exact | `[_CurrentField_]`, `[_CurrentFieldName_]`, copy-output and change-type options. |
| Output Data (22) | caveat | csv and xlsx values and types. Cell formats that Alteryx writes are not reproduced. |
| Transpose (22) | exact | Mixed types in the data fields become text, as with Union. |
| Run Command (15) | **no** | It runs another program. Its full command line is printed in the logic document and the generated stub raises. These are where the VBA macros are called from: the join between the two halves of this study. |
| Unique (12) | caveat | First row per key, case-sensitive. Output order assumed to be input order. |
| Dynamic Input (12) | caveat | "Read a list of data sources" with a full path. Other modes become stubs. |
| Join (8) | caveat | Three outputs, embedded Select with `Left_` / `Right_` names, case-sensitive keys, string joined to number refused. Not documented: output order (assumed sorted by key) and whether null matches null (assumed yes; one source says no). |
| Sort (7) | caveat | Stable. Assumed: nulls first, code-point order (upper case before lower). Dictionary order is not reproduced. |
| Sample (6) | exact | First N, Last N, Skip N, 1 of every N, with grouping. Random and percentage modes become stubs. |
| Cleanse macro (4) | caveat | The macro's check boxes appear in the XML only as numbers ("Check Box (84)"). The mapping to options is from memory and **must be confirmed** against `Cleanse.yxmc`; the raw values are printed next to the interpretation. |
| Message (4) | caveat | An Error message stops the run; other kinds are logged. |
| Record ID (3) | exact | String type is zero-padded to its size (documented). |
| Directory (3) | caveat | File times come from the operating system. |
| DateTime (1) | caveat | English formats such as `dd/MM/yyyy`. Output type for a date-only format assumed to be Date. |
| Text Input | exact | With the recorded types. |
| Tool Container, disabled | exact | Tools inside are left out and listed. A Union simply loses that input; any other tool that loses an input becomes a stub. |
| Browse, Text Box (58) | n/a | Not run. Notes on tools are kept in the logic document. |
| Other macros (`.yxmc`) | **no** | The macro's logic is in another file. A `.yxmc` is the same XML, so it can be run through `ayx2py` separately, but wiring it in is manual. |
| Multi-Row Formula, Cross Tab, Text To Columns, RegEx tool, Tile, Running Total, Generate Rows, Dynamic Rename | not in this prototype | Mechanical in principle. Each is a stub today. None is in the reference workflow. |
| Python, R, spatial, reporting, in-database, predictive tools | **no** | Not table-to-table rules that can be read from the XML. |

## 4. The Alteryx expression language

| Construct | Translation | Status |
|---|---|---|
| `IF c THEN a ELSEIF … ELSE b ENDIF`, `IIF` | Python `if` / `elif` / `return`; nested ones become conditional expressions. Only the chosen branch is evaluated. | exact |
| `AND` `OR` `&&` `\|\|` | **Same rank, grouped left to right**, as Alteryx documents: `a OR b AND c` is `(a OR b) AND c`. Every expression where this changes the meaning is listed in the logic document. `--and-before-or` switches to the conventional rule. | documented, surprising: check with probe item P08 |
| `NOT`, `!` | Ranked with `<` `>`, above `=`, as documented: `!a = b` is `(!a) = b`. Flagged where it matters. | documented |
| `=` `!=` `<` `>` on strings | Ignore case. Documented, and confirmed on real output (`'P'` matched `p`). | exact |
| `=` between a string and a number | Raises. Alteryx rejects the expression. | exact |
| `IN (…)`, `NOT IN` | Exact match, case-sensitive. | assumed (P03) |
| Nulls | Documented: null + string = the string; null + number = null; `<` `>` with a null are false; null = null is true; `<=` `>=` true only for two nulls. A null condition is false. | exact |
| Null = `''` | False. | assumed (P05) |
| `+` | Text concatenation or numeric addition by operand type; mixing raises. | exact |
| `Contains`, `StartsWith`, `EndsWith` | Ignore case unless told otherwise. | documented |
| `FindString`, `Replace`, `ReplaceFirst` | Case-sensitive. | assumed (P25, P26) |
| `REGEX_Replace`, `REGEX_Match`, `REGEX_CountMatches` | Python `re`, ignoring case by default; `REGEX_Match` must match the whole text; `$1` back-references. `\G` at the start of a pattern is emulated (the reference workflow uses `"\G;"`). POSIX classes, `\Q…\E`, possessive quantifiers and `\G` elsewhere raise instead of being run wrongly. | exact for the common syntax; whether `.` matches a newline is not known |
| `ToNumber` | Leading number of the text; other text gives 0; null gives 0 unless `keepNulls`. | documented |
| `ToString` | With decimals: documented. Without: assumed 15 significant digits, no trailing `.0`. | assumed (P09–P12) |
| `ToDate`, `ToDateTime` | Text, or an Excel serial day (`ToDate(7000)` = 1919-03-01, the documented example). | exact |
| `DateTimeDiff` | Days, hours, minutes, seconds: exact difference with the fraction dropped. Months, years: completed ones, time of day ignored. | documented (P16, P17) |
| `DateTimeAdd`, `DateTimeFormat`, `DateTimeParse`, `DateTimeTrim`, date parts | English, common `%` specifiers. | exact for those; other specifiers raise |
| `Round(x, mult)` | Nearest multiple, halves away from zero. | assumed (P21, P22) |
| Division by zero | Null. | reported by users, not documented (P15) |
| String literals | No escape sequences: `"\n"` is two characters. | assumed (P23) |
| Implicit conversion to the field's type | Number to text allowed; text into a numeric field is converted by the Select rules. | assumed |
| `[Engine.WorkflowDirectory]`, `[User.…]`, `[Question.…]` | Looked up in `Context(constants=…)`; missing ones raise. | exact |
| `DateTimeNow`, `DateTimeToday` | Translated and flagged: the result depends on the run date. `Context(now=…)` fixes it for tests. | caveat |
| `[Row-1:Field]`, unknown functions, spatial functions | Not translated; the tool becomes a stub. | no |

About 75 functions are mapped. The list is `FUNCS` in `ayx2py/expr.py`.

## 5. Where Alteryx's behaviour had to be assumed

Each item is a named switch in `ayx_runtime.SEM`. `probe/semantics_probe.yxmd` is a small workflow that exercises all of them; `probe/semantics_probe_expected.txt` holds what the runtime produces. Running the probe once in Designer and comparing settles every row below.

| Behaviour | Assumed | Basis |
|---|---|---|
| String `=`, `<`, `>` ignore case | yes | documented; real output |
| AND / OR left to right | yes | documented |
| Null rules for `+`, `<`, `>`, `=` | as in section 4 | documented |
| Record ID as String is zero-padded | yes | documented |
| `IN` ignores case | no | memory |
| Null = `''` | false | not found |
| Division by zero | null | user reports |
| Number to text without a format | 15 significant digits | not found |
| Join: null key matches null key | yes | memory; one user report says no |
| Join: outputs ordered by key | yes | common knowledge, not documented |
| Summarize: output ordered by group fields | yes | common knowledge, not documented |
| Summarize: Sum of only nulls | 0 | not found |
| Summarize: Concat keeps nulls as empty items | yes | the reference workflow's clean-up formulas remove `;;`, which suggests it |
| Union: type when inputs disagree | text wins | one user report |
| Find Replace: which Source row wins | first in Source order | one user report |
| Find Replace: empty Source values skipped | yes | not found |
| Unique: output order | input order | not found |
| Sort: nulls first; upper case before lower | yes | study guide, not official |
| DateTime tool: date-only format gives Date | yes | conflicting reports |
| CSV: empty field is null | yes | memory |
| Select: non-numeric text to number | null | conflicting reports |
| Field names in `[…]` | exact, then ignoring case | not found |
| Cleanse check-box numbers | see `CLEANSE_BOXES` in `workflow.py` | memory; confirm in `Cleanse.yxmc` |

## 6. VBA idioms

Measured on macros written in the style of real recorded macros (`tests/fixtures/config_macros.bas`): `FormatAll` and two `Format…` subs, `ImportAll`, `PurgeAllImport`, an import sub, `ImportLifecycle`, `UpdateLookupColumns`, `UpdateStats`, an Outlook sub.

| Idiom | Specification | Generated Python | Notes |
|---|---|---|---|
| `Sheets(x).Select` / `Range(y).Select` / `Selection.…` | Resolved: *Clear the contents of Format!F2:I2*. | exact | The runtime tracks the active workbook, sheet and selection as the statements change them, so loops and branches behave as in Excel. The specification's static tracking says so when a branch makes the state unknown. |
| `Range("A2:AQ" & n)`, `[B2]`, `Cells(r, c)`, `Rows("3:20000")`, `Columns("AT:AX")`, `With` blocks | Resolved to sheet and address. | exact | Plain addresses only. Defined names, multi-area ranges and intersections raise. |
| `Workbooks.Open`, `Set wb = ActiveWorkbook`, `wb.Activate`, `.Save`, `.Close` | `open workbook <format_file>`, with the cell the path was read from. | exact for the sequence | Saving goes through openpyxl, which does not keep everything Excel keeps (charts, shapes, pivot caches, some formats). Compare a saved copy before relying on it. |
| `Range(..).End(xlUp).Row`, `Cells(Rows.Count, "g").End(xlUp)` | "last filled cell going up from …". | exact | A cell holding an empty string counts as filled in Excel and in openpyxl alike. |
| `.Copy` then `.PasteSpecial xlPasteValues`, `ActiveSheet.Paste`, `Copy Destination:=` | `copy` / `paste values → target (from source)`. | exact for values, formulas, basic formats | The test reproduces the `ImportLifecycle` behaviour seen in a real macro: only the first row keeps its notional amounts. |
| Formula written to a range, then `.Value = .Value` | `formula …` then `to values …`. | exact **for a fixed list of functions** | openpyxl cannot calculate. A small evaluator handles `&`, arithmetic, comparisons, `RIGHT LEFT MID LEN TRIM UPPER LOWER CONCATENATE IF IFERROR IFNA ISBLANK AND OR NOT XLOOKUP VLOOKUP MATCH INDEX COUNTIF SUM ROUND ABS VALUE` with Excel's rules (text match ignores case, a date is its serial number). Anything else raises. Both `XLOOKUP` formulas met in real macros are covered. |
| `.FormulaR1C1 = "=RC[-10]&"" ""&RC[-4]"` | Shown as written. | exact | Converted to A1 per cell. |
| `FormatConditions.Add`, `SetFirstPriority`, `.Interior.Color / ThemeColor / TintAndShade`, `StopIfTrue` | Rule, range, formula, colour as `#RRGGBB`. | exact in the saved file | The test checks ranges, formulas, priorities and fills of the four tracker rules. Not opened in Excel. |
| `Columns.AutoFit` | `autofit …`. | **approximate** | Excel measures rendered text; the runtime estimates from text length and logs that it did. |
| `On Error GoTo label` … `Exit Sub` … `label:` | `on error jump to …`, handler listed. | exact for this shape | Becomes `try` / `except`. A statement that could not be converted is never swallowed by the macro's own handler. Other shapes (`Resume`, labels inside blocks) are refused. |
| `On Error Resume Next` | Stated. | caveat | Each following simple statement is skipped if it fails. Conditions of `If` and loops are not protected. |
| Status cells (`[F2] = "Successfully Formatted"`, `"Fail - " & Err.Description`) | Tagged `[status]`. | exact | The error text is Python's, not Excel's: where the status text matters to someone, it will differ. |
| Calls between Subs, arguments, Functions | Call tree; entry points identified. | exact | `ByRef` results are not carried back. |
| `If`, `For`, `For Each`, `Do`, `Select Case`, string and conversion functions | Shown as structure. | exact for about 40 functions | VBA comparison rules are kept (`Empty = ""` is true; `"Y" >= 1` is a type mismatch, as in VBA). `Format` supports a few patterns and refuses the rest. |
| Subs that differ only in cells | "same steps as FormatTradesA, with: B2→D2, F2→H2". | one function each | |
| `Cells.Find`, `AutoFilter`, `Sort`, pivot tables, charts, `WorksheetFunction`, defined names | Printed as VBA, listed as not converted. | **no**: raises | `UpdateStats` uses `Find` with `DateValue`: both depend on Excel and on regional settings. |
| Outlook and other programs (`CreateObject`), `MsgBox` with a choice, `InputBox`, `Shell`, file deletion | Flagged. | **no**: raises | No deterministic equivalent. `MailScan` is in this group. |
| User forms, class modules, events, `Declare`, `GoTo` | Listed. | **no** | |

## 7. Reading VBA out of an `.xlsm` without Excel

An `.xlsm` is a zip. Its `xl/vbaProject.bin` is an OLE compound file (MS-CFB). Storage `VBA` holds a stream `dir` listing the modules, and one stream per module. Both use the run-length compression of MS-OVBA section 2.4.1; each module's source begins at an offset given in `dir`.

A standard-library reader is practical and is implemented in `vba2py/ovba.py` (about 200 lines: `zipfile`, `struct`). `python -m vba2py Configuration.xlsm` reads the workbook, writes the VBA text, and analyses it. This removes the need to photograph the VBA editor.

- The decompressor reproduces the three worked examples in the MS-OVBA specification.
- The container reader was tested only against containers written by this study's own test helper. Its first run on a real workbook is the real test. If it fails, exporting the modules from the VBA editor (File > Export File) and giving the `.bas` files to `vba2py` works without it.
- A project "locked for viewing" can still be read: the lock is a flag for the editor. A workbook with a password to open cannot: the whole file is encrypted.
- Whether your organisation's rules allow reading macro source this way is a policy question, not a technical one. The tool opens the file read-only and writes only into its output folder.

## 8. Where determinism ends

A person or a model must decide these; no converter can.

1. **Which Alteryx behaviours are true.** The 15 or so assumed items in section 5. One probe run in Designer settles them.
2. **Whether the workflow is right.** The converter reproduces the workflow, including its quirks: `FindAny` used as a join, `Last` meaning stream order, a formula computed and never used. The logic document makes them visible (it lists every substring match and every precedence-sensitive expression); choosing to keep or fix each one is the migration decision that a hand-written design document tends to get wrong.
3. **Inputs as Alteryx saw them.** Column types and blank-versus-null come from Alteryx's file drivers. The recorded types remove most of this; the rest shows up as type-mismatch errors on the first real run.
4. **Everything behind a Run Command.** In the reference workflow that is the VBA.
5. **Excel itself**: calculation beyond the evaluator's list, AutoFit, `Find`, what a save keeps, regional date parsing.
6. **Other programs**: Outlook, shared drives, anything interactive.
7. **What the code should look like.** The generated Alteryx module is faithful and flat: one function per tool, 7,800 lines for 930 tools. Whether to keep it as is, or to write a smaller hand-structured version from the logic document and use the generated one as the reference to compare against, is a choice.

## 9. Verified only on synthetic input

- **Every Alteryx XML layout.** The synthetic workflows follow what an earlier extraction script reads (it ran on a real workflow) for Formula, Filter, Select, Join, Append Fields, Sort, Unique, Summarize, Transpose, Multi-Field Formula, Find Replace, Union, Text Input, Tool Container and the recorded field types. The layouts of Sample, Record ID, DateTime, Directory, Dynamic Input, Input and Output options, Message, the Cleanse values and the Parallel Block Until Done anchor names are from memory. If a required setting is not where expected, the tool becomes a stub with a message; an optional setting that is laid out differently could be missed silently. On the first real run, compare a few tools of each kind against the workflow's XML.
- **Every expected result.** Expected frames were computed by hand from the rules in sections 3 to 5. A wrong belief about Alteryx would pass these tests. Only the probe and a parity run can catch that.
- **The factoring of repeated sub-chains.** It found the tree repeated four times in the synthetic workflow (47 tools, 4 copies). Real copies that differ in one setting will split into smaller blocks around the difference; the logic document then shows more stages, never fewer rules.
- **Sizes.** The 3,625 lines assume that only the scenario tree repeats and every other rule is unique and of moderate length. A simpler extractor needed 1,114 lines for one of the reference workflow's four containers, without its Select tools, so expect 3,500 to 6,000 lines of logic document for a workflow of that size; the stage map is the table of contents.
- **The VBA tools**, against macros written in the style of real recorded macros, and workbooks built with openpyxl. The conditional formats were read back with openpyxl, not opened in Excel.
- **The `.xlsm` reader**, as described in section 7.
- **Windows.** Paths, encodings and console output were written for Windows and Python 3.12 but run only on macOS here.
- **The probe `.yxmd`** has not been opened in Designer. If it does not load, the expected file lists every expression to paste into a Formula tool by hand.

## 10. Suggested order on the work laptop

1. Run `python run_tests.py`. It needs no real data.
2. Open `probe/semantics_probe.yxmd` in Designer, run it, compare with the expected file. Change the switches that differ.
3. Run `ayx2py` on the real workflow. Read the logic document's summary, stage map and last section (what to check).
4. Run `vba2py` on `Configuration.xlsm`.
5. Run the generated module on one day's inputs with `write=False` and compare `ctx.outputs` with that day's real output files.
