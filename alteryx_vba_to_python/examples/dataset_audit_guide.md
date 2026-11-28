# How dataset_audit.yxmd works

Made by ayx2py from `dataset_audit.yxmd`, for someone who has never opened Alteryx Designer. 3 containers, 1 disabled in the saved file (documented anyway), 34 tools, 2 outputs.

**Python:** steps converted exactly 19, with a stated assumption 14, not converted 1. Before relying on it, read what to check in the Detail section.

- 1. What this workflow does
  - 1. Import and consolidate
  - 2. Dataset audit
  - 3. Old checks _(disabled)_
- 2. How each output is made
  - Container 1: Consolidated Datasets.xlsx › Datasets
  - Container 2: Dataset audit.xlsx › Issues
  - Container 3: no output
- 3. Run order
- 4. Files, sheets and scripts
- 5. Detail: how to read the pandas, what to check, columns, every step by number

## 1. What this workflow does

### 1. Import and consolidate

It opens 1 source, calculates columns and saves 1 table, in 4 steps.

- **Opens:** `datasets_export.csv`
- **Saves:** `Consolidated Datasets.xlsx` › Datasets
- **Used by:** container 2

### 2. Dataset audit

It opens 2 sources, filters rows, matches tables, totals rows, calculates columns and saves 1 table, in 28 steps.

It opens `Consolidated Datasets.xlsx`, which container 1 saves.

- **Opens:** the list of files in folder `Output` (files named \*.xlsx); the files listed in column [Source] (like `template.xlsx`)
- **Saves:** `Dataset audit.xlsx` › Issues

### 3. Old checks _(disabled in the saved file)_

It opens 1 source and calculates columns, in 2 steps.

Disabled in the saved file: Alteryx skips it until someone enables it (teams often run one container at a time this way). It is explained here anyway; in the generated code run it with `only=3`.

- **Opens:** `old_rules.csv`
- **Saves:** —

## 2. How each output is made

One story per output: only the steps on its path, in the order they run, from the files it starts from to the saved sheet. Work that several outputs share is told once as a working table, and the stories start from it by name. Click **Rule and pandas** under a step for its exact rule and the same step in pandas.

### Container 1. Import and consolidate

#### Consolidated Datasets.xlsx › Datasets

4 steps, from datasets_export.csv.

**1. Input Data** · step 1.1 · tool #1  
Opens `datasets_export.csv`.  
Columns: [Name] [Owner] [CreationMode] [Scenario] [Tags] [DQScore] +3 more  

- **1.1** Input Data #1 · converted, assuming A4 · `t1`

      # columns: Name (V_String), Owner (V_String), CreationMode (V_String), Scenario (V_String), Tags (V_String),
      # columns: DQScore (Double), Virtualizable (V_String), SizeGB (Double), Access (V_String)
      # note: Read as: UTF-8.
      # pandas:
      src_datasets_export = pd.read_csv(DATASETS_EXPORT_CSV, sep=',', encoding="utf-8-sig", dtype=str, keep_default_na=False).replace("", None).astype({"DQScore": float, "SizeGB": float})

**2. Data Cleansing** · step 1.2 · tool #2  
Cleans the columns [Name], [Owner], [Tags]: remove leading and trailing spaces.  
Looks at: [Name] [Owner] [Tags]  
Adds: [Name] [Owner] [Tags]  

- **1.2** Data Cleansing #2 · converted, assuming A1 · `t2`

      # note: Options read from the macro's numbered check boxes: confirm them in the tool's settings.
      # pandas:
      cols = ["Name", "Owner", "Tags"]
      df[cols] = df[cols].apply(lambda s: s.str.strip())

**3. Formula** · step 1.3 · tool #3  
Sets 1 column.  
Sets: **Load Date** = today's date  

- **1.3** Formula #3 · converted; note: [Load Date] uses datetimetoday(): the result depends on the run date · `t3`

      Load Date = DateTimeToday()
      # pandas:
      df["Load Date"] = pd.Timestamp.today().normalize()

**4. Output Data** · step 1.4 · tool #4  
Saves the table to `Consolidated Datasets.xlsx › Datasets` (replaces the sheet).  

- **1.4** Output Data #4 · converted, assuming A6 · `t4` · `s1_4 = df  # the rows this step saves; the notebook's last cell writes them to OUT`

### Container 2. Dataset audit

#### Dataset audit.xlsx › Issues

27 steps, from the file list of Output, the files listed in [Source], a typed table ([Owner], [Team]; 2 rows).

**1. Directory** “Newest consolidated file” · step 2.1 · tool #30  
Lists the files in folder `Output` named `*.xlsx`.  
Columns: [FullPath] [FileName] [CreationTime] [LastWriteTime]  

- **2.1** Directory #30 · converted, assuming A2 · `t30`

      # columns: FullPath, FileName, CreationTime, LastWriteTime
      # pandas:
      files = sorted(glob.glob(os.path.join(FOLDER, "Output", "*.xlsx")))
      df = pd.DataFrame({"Directory": [os.path.dirname(f) for f in files], "FileName": [os.path.basename(f) for f in files], "FullPath": files,
                         "CreationTime": [pd.Timestamp.fromtimestamp(os.path.getctime(f)) for f in files],
                         "LastWriteTime": [pd.Timestamp.fromtimestamp(os.path.getmtime(f)) for f in files],
                         "Size": [os.path.getsize(f) for f in files]})

**2. Filter** · step 2.2 · tool #31  
Keeps only the rows where [FileName] contains "Consolidated" and [FileName] does not contain ".bak".  
Looks at: [FileName]  

- **2.2** Filter #31 · exact · `t31`

      [FileName] contains "Consolidated" and [FileName] does not contain ".bak"
      # pandas:
      df = df[(df["FileName"].str.contains('Consolidated', case=False, regex=False, na=False) & ~(df["FileName"].str.contains('.bak', case=False, regex=False, na=False)))]

**3. Sort** · step 2.3 · tool #32  
Sorts by [CreationTime] Z→A.  
Looks at: [CreationTime]  

- **2.3** Sort #32 · converted, assuming A7 · `t32` · `df = df.sort_values(["CreationTime"], ascending=[False], kind="stable", na_position="first")`

**4. Sample** · step 2.4 · tool #33  
Keeps the first row.  

- **2.4** Sample #33 · exact · `t33` · `df = df.head(1)`

**5. Formula** · step 2.5 · tool #34  
Sets 1 column.  
Sets: **Source** = [FullPath] + "|||Datasets"  

- **2.5** Formula #34 · exact · `t34`

      Source = [FullPath] + "|||Datasets"
      # pandas:
      df["Source"] = (df["FullPath"] + '|||Datasets')

**6. Dynamic Input** · step 2.6 · tool #36  
Opens every file listed in column [Source] (the column holds the whole path; sheet as in the template `template.xlsx`, sheet `Datasets`), and stacks them.  
Columns: [Name] [Owner] [CreationMode] [Scenario] [Tags] [DQScore] +3 more  

- **2.6** Dynamic Input #36 · converted, assuming A3 · `t36`

      # columns: Name (V_String), Owner (V_String), CreationMode (V_String), Scenario (V_String), Tags (V_String),
      # columns: DQScore (Double), Virtualizable (V_String), SizeGB (Double), Access (V_String)
      # pandas:
      files = [pd.read_excel(os.path.join(FOLDER, p.split("|||")[0].replace("\\", os.sep)), sheet_name="Datasets") for p in df["Source"].dropna()]
      df = pd.concat(files or [pd.read_excel(os.path.join(FOLDER, "Output", "template.xlsx"), sheet_name="Datasets", nrows=0)], ignore_index=True)  # no file listed: the template's columns, no rows

**7. Filter** “CreationMode” · step 2.7 · tool #41  
Keeps only the rows where [CreationMode] is "Manual".  
Looks at: [CreationMode]  

- **2.7** Filter #41 · exact · `t41` · `df = df[eq(df["CreationMode"], 'Manual')]`

**8. Formula** · step 2.8 · tool #51  
Sets 1 column.  
Sets: **Issue** = "Created by hand"  

- **2.8** Formula #51 · exact · `t51`

      Issue = "Created by hand"
      # pandas:
      df["Issue"] = 'Created by hand'

**9. Select** · step 2.9 · tool #61  
Keeps only 3 columns.  
Keeps: [Name] [Owner] [Issue]  

- **2.9** Select #61 · exact · `t61`

      keep only these columns, in this order: Name, Owner, Issue
      # pandas:
      df = df[["Name", "Owner", "Issue"]]

**10. Filter** “Scenario” · step 2.10 · tool #42  
Keeps only the rows where [Scenario] is empty.  
Takes: step 6  
Looks at: [Scenario]  

- **2.10** Filter #42 · exact · `t42` · `df = df[(empty(df["Scenario"]))]`

**11. Formula** · step 2.11 · tool #52  
Sets 1 column.  
Sets: **Issue** = "No scenario"  

- **2.11** Formula #52 · exact · `t52`

      Issue = "No scenario"
      # pandas:
      df["Issue"] = 'No scenario'

**12. Summarize** · step 2.12 · tool #62  
Groups the rows by [Owner], [Issue] and calculates **Datasets**, **Name** (like a pivot table).  
Looks at: [Owner] [Issue] [Name]  
Adds: [Datasets] [Name]  

- **2.12** Summarize #62 · converted, assuming A8 · `t62`

      Datasets = number of rows
      Name = all [Name] joined with ", "
      # pandas:
      df = df.groupby(["Owner", "Issue"], sort=True, dropna=False).agg(**{"Datasets": ("Name", "size"), "Name": ("Name", lambda s: ', '.join(s.fillna("").astype(str)))}).reset_index()

**13. Filter** “Tags” · step 2.13 · tool #43  
Keeps only the rows where [Tags] does not contain "certified".  
Takes: step 6  
Looks at: [Tags]  

- **2.13** Filter #43 · exact · `t43` · `df = df[~(df["Tags"].str.contains('certified', case=False, regex=False, na=False))]`

**14. Formula** · step 2.14 · tool #53  
Sets 1 column.  
Sets: **Issue** = "Draft tag" when [Tags] contains "draft", otherwise "Not certified"  

- **2.14** Formula #53 · exact · `t53`

      Issue = "Draft tag" when [Tags] contains "draft", otherwise "Not certified"
      # pandas:
      df["Issue"] = np.select([df["Tags"].str.contains('draft', case=False, regex=False, na=False)], ['Draft tag'], default='Not certified')

**15. Filter** “Data Quality” · step 2.16 · tool #44  
Keeps only the rows where [DQScore] < 0.8.  
Takes: step 6  
Looks at: [DQScore]  

- **2.16** Filter #44 · exact · `t44` · `df = df[(df["DQScore"] < 0.8)]`

**16. Formula** · step 2.17 · tool #54  
Sets 1 column.  
Sets: **Issue**: worked out from [DQScore] (rule in the details)  

- **2.17** Formula #54 · exact · `t54`

      Issue = "Low data quality: " + ToString([DQScore], 2)
      # pandas:
      df["Issue"] = ('Low data quality: ' + df["DQScore"].map(lambda v: f"{v:.2f}"))

**17. RegEx** · step 2.18 · tool #64 · ⚠ not converted: write by hand  
Applies the pattern `(\w+)_v\d+` to [Name] (ParseSimple).  

- **2.18** RegEx #64 · **not converted**: tool kind RegEx has no converter · `t64`

      Field=Name; RegExExpression(value=(\w+)_v\d+); Method=ParseSimple
      # pandas:
      # no pandas form; write this step by hand

**18. Filter** “Virtualizable” · step 2.19 · tool #45  
Keeps only the rows where [Virtualizable] is "N" and [SizeGB] > 100.  
Takes: step 6  
Looks at: [Virtualizable] [SizeGB]  

- **2.19** Filter #45 · exact · `t45` · `df = df[(eq(df["Virtualizable"], 'N') & (df["SizeGB"] > 100))]`

**19. Formula** · step 2.20 · tool #55  
Sets 1 column.  
Sets: **Issue** = "Large and not virtualizable"  

- **2.20** Formula #55 · exact · `t55`

      Issue = "Large and not virtualizable"
      # pandas:
      df["Issue"] = 'Large and not virtualizable'

**20. Sort** · step 2.21 · tool #65  
Sorts by [SizeGB] Z→A.  
Looks at: [SizeGB]  

- **2.21** Sort #65 · converted, assuming A7 · `t65` · `df = df.sort_values(["SizeGB"], ascending=[False], kind="stable", na_position="first")`

**21. Filter** “Read/Write” · step 2.22 · tool #46  
Keeps only the rows where [Access] is one of "RW", "W".  
Takes: step 6  
Looks at: [Access]  

- **2.22** Filter #46 · exact · `t46` · `df = df[df["Access"].isin(['RW', 'W'])]`

**22. Formula** · step 2.23 · tool #56  
Sets 1 column.  
Sets: **Issue** = "Write only" when [Access] is "W", otherwise "Read/write"  

- **2.23** Formula #56 · exact · `t56`

      Issue = "Write only" when [Access] = "W", otherwise "Read/write"
      # pandas:
      df["Issue"] = np.select([eq(df["Access"], 'W')], ['Write only'], default='Read/write')

**23. Select** · step 2.24 · tool #66  
Deletes the columns [Access].  
Removes: [Access]  

- **2.24** Select #66 · exact · `t66`

      drop Access
      # pandas:
      df = df.drop(columns=["Access"])

**24. Join** · step 2.15 · tool #63  
Matches its rows with a typed table ([Owner], [Team]; 2 rows) on [Owner].  
Takes: step 14  
Looks at: [Owner]  
↳ **matched rows** → step 25 · **rows only in the first input** → dropped · **rows only in the second input** → dropped  

- **2.15** Join #63 · converted, assuming A5 · `t63`

      keys: left [Owner] = right [Owner]
      drop Right_Owner
      # pandas:
      right = src_typed_owner_team.rename(columns=lambda c: "Right_" + c if c in df.columns else c)  # as Alteryx: only clashing names get Right_
      m = df.merge(right, how="outer", left_on=["Owner"], right_on=[c if c in right.columns else "Right_" + c for c in ["Owner"]], indicator=True)
      m = m.drop(columns=["Right_Owner"], errors="ignore")
      s2_15_join = m[m["_merge"] == "both"].drop(columns="_merge")

**25. Union** “All issues” · step 2.25 · tool #90  
Stacks the tables step 9, step 12, step 24 (matched rows), step 17, step 20, step 23 (columns matched by name).  

- **2.25** Union #90 · converted, assuming A9 · `t90` · `df = pd.concat([s2_9, s2_12, s2_15_join, s2_18, s2_21, s2_24], ignore_index=True)`

**26. Formula** · step 2.26 · tool #91  
Sets 1 column.  
Sets: **Audit Date** = today's date  

- **2.26** Formula #91 · converted; note: [Audit Date] uses datetimetoday(): the result depends on the run date · `t91`

      Audit Date = DateTimeToday()
      # pandas:
      df["Audit Date"] = pd.Timestamp.today().normalize()

**27. Output Data** · step 2.27 · tool #92  
Saves the table to `Dataset audit.xlsx › Issues` (replaces the sheet).  

- **2.27** Output Data #92 · converted, assuming A6 · `t92` · `s2_27 = df  # the rows this step saves; the notebook's last cell writes them to OUT`

### Container 3. Old checks _(disabled in the saved file)_

**Not used by any output:** 2 steps (Input Data 1, Formula 1). They run, but nothing they make is saved.

> **The 2 steps**

> **1. Input Data** · step 3.1 · tool #100  
> Opens `old_rules.csv`.  

- **3.1** Input Data #100 · converted, assuming A4; note: the workflow holds no recorded field types for this input · `t100`

      src_old_rules = pd.read_csv(OLD_RULES_CSV, sep=',', dtype=str, keep_default_na=False).replace("", None)

> **2. Formula** · step 3.2 · tool #101  
> Sets 1 column.  
> Sets: **Old** = 1  

- **3.2** Formula #101 · exact · `t101`

      Old = 1
      # pandas:
      df["Old"] = 1

## 3. Run order

Inside a story, steps run in the order shown. What must finish before what:

1. Container 2 runs after container 1: it opens `Consolidated Datasets.xlsx`, which container 1 saves.
2. Container 3 is disabled in the saved file: Alteryx skips it until someone enables it.

## 4. Files, sheets and scripts

| File | Sheet | Cells | Used | Columns | Where |
|---|---|---|---|---|---|
| `datasets_export.csv` | — | all | opened | [Name], [Owner], [CreationMode], [Scenario], [Tags], [DQScore] +3 more | Consolidated Datasets.xlsx › Datasets (1.1) |
| `Consolidated Datasets.xlsx` | Datasets | all | saved | — | Consolidated Datasets.xlsx › Datasets (1.4) |
| `Output` | — | — | files named \*.xlsx listed | [FullPath], [FileName], [CreationTime], [LastWriteTime] | Dataset audit.xlsx › Issues (2.1) |
| `template.xlsx` | Datasets | all | listed in [Source] | [Name], [Owner], [CreationMode], [Scenario], [Tags], [DQScore] +3 more | Dataset audit.xlsx › Issues (2.6) |
| `Dataset audit.xlsx` | Issues | all | saved | — | Dataset audit.xlsx › Issues (2.27) |
| `old_rules.csv` | — | all | opened | — | Steps that feed no output (3.1) |

## 5. Detail

### How to read the pandas

- **Rule and pandas**, under each step, holds the step's exact rule (`when … → …` lines are read top down; the first true condition gives the value) and the same step in pandas, to paste into the steps notebook. Its first line gives the step number, the Designer tool and its ID, and whether the generated Python converts it exactly.
- In the pandas, `df` is the table flowing through the step; `s2_7` is step 2.7's output and `s2_7_true` its rows that pass; `src_…` is a file as loaded in the notebook's Load cell, and `FOLDER` is the workflow's folder. The steps notebook is laid out by Designer's branches; find a step here by its number (the index below lists them all).
- **A1, A2…** in a status are Alteryx behaviours the Python assumes; they are listed in what to check.

The pandas uses two helpers, because Alteryx compares text ignoring case and treats null and the empty string separately:

    import functools, glob, operator, os, subprocess
    import numpy as np
    import pandas as pd
    
    pd.options.mode.copy_on_write = True   # df = s2_6, then changing df, leaves s2_6 as it was
    
    def empty(s):
        """Alteryx IsEmpty: null or the empty string."""
        return s.isna() | (s == "")
    
    def eq(a, b):
        """Alteryx "=": text is compared ignoring case; null equals null."""
        fold = lambda x: x.str.casefold() if isinstance(x, pd.Series) and x.dtype == object else (
            x.casefold() if isinstance(x, str) else x)
        null = lambda x: x.isna() if isinstance(x, pd.Series) else x is None
        return (fold(a) == fold(b)) | (null(a) & null(b))

## Before relying on this

- **Not converted to Python** (1 step): the generated code stops there with an error until the step is written by hand, or until the test notebook gives it a stand-in.
  - RegEx, 1 step (2.18): tool kind RegEx has no converter
- **Alteryx behaviours the Python assumes** (steps refer to them as A1, A2…; settle them once with the probe workflow):
  - **A1** — Cleanse: the check-box numbers are mapped to options from memory of the standard macro; confirm against Cleanse.yxmc
  - **A2** — Directory: file times come from the operating system
  - **A3** — DynamicInput: as Input; 'read a list of data sources' (whole path, file name, prefix or suffix)
  - **A4** — Input: column types depend on Alteryx's file driver; the types recorded in the workflow are applied when present
  - **A5** — Join: row order of the three outputs and null = null on keys are not documented
  - **A6** — Output: cell formats written by Alteryx are not reproduced; writes only with Context(write=True)
  - **A7** — Sort: position of nulls and the order of upper/lower case are not documented
  - **A8** — Summarize: output order, Sum of no values and Concat of empty values are not documented
  - **A9** — Union: the type chosen when inputs disagree, and the order of the inputs, are not documented

### Columns created or changed in container 1 (10)

Each column this container creates or changes: the type Alteryx gives it, the steps that set it, how, and the later steps (anywhere in the workflow) that use a column of that name.

| Column | Type | Set at | How | Used later by |
|---|---|---|---|---|
| **Name** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 2.12 |
| **Owner** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 2.12, 2.15 |
| **CreationMode** | V_String | 1.1 | read from the file | 2.7 |
| **Scenario** | V_String | 1.1 | read from the file | 2.10 |
| **Tags** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 2.13, 2.14 |
| **DQScore** | Double | 1.1 | read from the file | 2.16, 2.17 |
| **Virtualizable** | V_String | 1.1 | read from the file | 2.19 |
| **SizeGB** | Double | 1.1 | read from the file | 2.19, 2.21 |
| **Access** | V_String | 1.1 | read from the file | 2.22, 2.23 |
| **Load Date** | Date | 1.3 | DateTimeToday() | not read by a later step |

### Columns created or changed in container 2 (18)

Each column this container creates or changes: the type Alteryx gives it, the steps that set it, how, and the later steps (anywhere in the workflow) that use a column of that name.

| Column | Type | Set at | How | Used later by |
|---|---|---|---|---|
| **FullPath** |  | 2.1 | file details | 2.5 |
| **FileName** |  | 2.1 | file details | 2.2 |
| **CreationTime** |  | 2.1 | file details | 2.3 |
| **LastWriteTime** |  | 2.1 | file details | not read by a later step |
| **Source** | V_WString | 2.5 | [FullPath] + "\|\|\|Datasets" | 2.6 |
| **Name** | V_String | 2.6, 2.12 | read from the files / all [Name] joined with ", " | 2.12 |
| **Owner** | V_String | 2.6, a typed table ([Owner], [Team]; 2 rows) | read from the files / typed in the workflow | 2.12, 2.15 |
| **CreationMode** | V_String | 2.6 | read from the files | 2.7 |
| **Scenario** | V_String | 2.6 | read from the files | 2.10 |
| **Tags** | V_String | 2.6 | read from the files | 2.13, 2.14 |
| **DQScore** | Double | 2.6 | read from the files | 2.16, 2.17 |
| **Virtualizable** | V_String | 2.6 | read from the files | 2.19 |
| **SizeGB** | Double | 2.6 | read from the files | 2.19, 2.21 |
| **Access** | V_String | 2.6 | read from the files | 2.22, 2.23 |
| **Issue** | V_WString | 2.8, 2.11, 2.14, 2.17, 2.20, 2.23 | differs by step (6 rules; see the steps) | 2.12 |
| **Datasets** |  | 2.12 | number of rows | not read by a later step |
| **Team** |  | a typed table ([Owner], [Team]; 2 rows) | typed in the workflow | not read by a later step |
| **Audit Date** | Date | 2.26 | DateTimeToday() | not read by a later step |

### Columns created or changed in container 3 (1)

Each column this container creates or changes: the type Alteryx gives it, the steps that set it, how, and the later steps (anywhere in the workflow) that use a column of that name.

| Column | Type | Set at | How | Used later by |
|---|---|---|---|---|
| **Old** | V_WString | 3.2 | 1 | not read by a later step |

### Every step by number (33)

| Step | Tool | Told in | Tool ID |
|---|---|---|---|
| 1.1 | Input Data | Consolidated Datasets.xlsx › Datasets | #1 |
| 1.2 | Data Cleansing | Consolidated Datasets.xlsx › Datasets | #2 |
| 1.3 | Formula | Consolidated Datasets.xlsx › Datasets | #3 |
| 1.4 | Output Data | Consolidated Datasets.xlsx › Datasets | #4 |
| 2.1 | Directory | Dataset audit.xlsx › Issues | #30 |
| 2.2 | Filter | Dataset audit.xlsx › Issues | #31 |
| 2.3 | Sort | Dataset audit.xlsx › Issues | #32 |
| 2.4 | Sample | Dataset audit.xlsx › Issues | #33 |
| 2.5 | Formula | Dataset audit.xlsx › Issues | #34 |
| 2.6 | Dynamic Input | Dataset audit.xlsx › Issues | #36 |
| 2.7 | Filter | Dataset audit.xlsx › Issues | #41 |
| 2.8 | Formula | Dataset audit.xlsx › Issues | #51 |
| 2.9 | Select | Dataset audit.xlsx › Issues | #61 |
| 2.10 | Filter | Dataset audit.xlsx › Issues | #42 |
| 2.11 | Formula | Dataset audit.xlsx › Issues | #52 |
| 2.12 | Summarize | Dataset audit.xlsx › Issues | #62 |
| 2.13 | Filter | Dataset audit.xlsx › Issues | #43 |
| 2.14 | Formula | Dataset audit.xlsx › Issues | #53 |
| 2.15 | Join | Dataset audit.xlsx › Issues | #63 |
| 2.16 | Filter | Dataset audit.xlsx › Issues | #44 |
| 2.17 | Formula | Dataset audit.xlsx › Issues | #54 |
| 2.18 | RegEx | Dataset audit.xlsx › Issues | #64 |
| 2.19 | Filter | Dataset audit.xlsx › Issues | #45 |
| 2.20 | Formula | Dataset audit.xlsx › Issues | #55 |
| 2.21 | Sort | Dataset audit.xlsx › Issues | #65 |
| 2.22 | Filter | Dataset audit.xlsx › Issues | #46 |
| 2.23 | Formula | Dataset audit.xlsx › Issues | #56 |
| 2.24 | Select | Dataset audit.xlsx › Issues | #66 |
| 2.25 | Union | Dataset audit.xlsx › Issues | #90 |
| 2.26 | Formula | Dataset audit.xlsx › Issues | #91 |
| 2.27 | Output Data | Dataset audit.xlsx › Issues | #92 |
| 3.1 | Input Data | Steps that feed no output | #100 |
| 3.2 | Formula | Steps that feed no output | #101 |
