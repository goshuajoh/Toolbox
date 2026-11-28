# How sales_report.yxmd works

Made by ayx2py from `sales_report.yxmd`, for someone who has never opened Alteryx Designer. 2 containers, 17 tools, 3 outputs.

**Python:** steps converted exactly 5, with a stated assumption 12, not converted 0. Before relying on it, read what to check in the Detail section.

- 1. What this workflow does
  - 1. Prepare orders
  - 2. Summarise
- 2. How each output is made
  - Container 1: duplicate_orders.csv, cancelled_by_region.csv (and 2 working tables)
  - Container 2: sales_summary.xlsx › Summary
- 3. Run order
- 4. Files, sheets and scripts
- 5. Detail: how to read the pandas, what to check, columns, every step by number

## 1. What this workflow does

### 1. Prepare orders

It opens 1 source, filters rows, totals rows, calculates columns, removes duplicates and saves 2 tables, in 10 steps.

- **Opens:** `orders.csv`
- **Saves:** `duplicate_orders.csv`; `cancelled_by_region.csv`
- **Used by:** container 2

### 2. Summarise

It matches tables, totals rows, calculates columns, stacks tables and saves 1 table, in 7 steps.

It takes rows from **orders.csv, split on Status, duplicates removed on OrderID** (container 1).

- **Opens:** —
- **Saves:** `sales_summary.xlsx` › Summary

## 2. How each output is made

One story per output: only the steps on its path, in the order they run, from the files it starts from to the saved sheet. Work that several outputs share is told once as a working table, and the stories start from it by name. Click **Rule and pandas** under a step for its exact rule and the same step in pandas.

### Container 1. Prepare orders

#### orders.csv, split on Status

A working table: its rows are used by **orders.csv, split on Status, duplicates removed on OrderID**, **cancelled_by_region.csv**. Told once here; those stories start from it.

**1. Input Data** · step 1.1 · tool #1  
Opens `orders.csv`.  
Columns: [OrderID] [OrderDate] [Region] [Product] [Qty] [UnitPrice] +1 more  

- **1.1** Input Data #1 · converted, assuming A3 · `t1`

      # columns: OrderID (V_String), OrderDate (V_String), Region (V_String), Product (V_String), Qty (V_String),
      # columns: UnitPrice (V_String), Status (V_String)
      # note: Read as: UTF-8.
      # pandas:
      src_orders = pd.read_csv(ORDERS_CSV, sep=',', encoding="utf-8-sig", dtype=str, keep_default_na=False).replace("", None)

**2. Data Cleansing** · step 1.2 · tool #2  
Cleans the columns [Region], [Product], [Status]: remove leading and trailing spaces.  
Looks at: [Region] [Product] [Status]  
Adds: [Region] [Product] [Status]  

- **1.2** Data Cleansing #2 · converted, assuming A1 · `t2`

      # note: Options read from the macro's numbered check boxes: confirm them in the tool's settings.
      # pandas:
      cols = ["Region", "Product", "Status"]
      df[cols] = df[cols].apply(lambda s: s.str.strip())

**3. Filter** · step 1.3 · tool #3  
Splits the rows: those where [Status] is not "Cancelled" pass, the others fail.  
Looks at: [Status]  
↳ **rows that pass** → **orders.csv, split on Status, duplicates removed on OrderID** · **rows that fail** → **cancelled_by_region.csv**  

- **1.3** Filter #3 · exact · `t3`

      m = ~eq(df["Status"], 'Cancelled')
      s1_3_true, s1_3_false = df[m], df[~m]

#### orders.csv, split on Status, duplicates removed on OrderID

A working table: its rows are used by **duplicate_orders.csv**, **sales_summary.xlsx › Summary** (container 2). Told once here; those stories start from it.

**1. Select** · step 1.4 · tool #4  
Sets the type of [Qty], [UnitPrice].  
Starts from: **orders.csv, split on Status** (rows that pass)  

- **1.4** Select #4 · exact · `t4`

      change type: [Qty] to Int32, [UnitPrice] to Double
      # pandas:
      df = df.astype({"Qty": Int64, "UnitPrice": float})

**2. DateTime** · step 1.5 · tool #5  
Adds column **Order Day** = [OrderDate] read as a date `dd/MM/yyyy`.  
Looks at: [OrderDate]  
Adds: [Order Day]  

- **1.5** DateTime #5 · converted, assuming A2 · `t5` · `df["Order Day"] = pd.to_datetime(df["OrderDate"], format="%d/%m/%Y", errors="coerce")`

**3. Formula** · step 1.6 · tool #6  
Sets 4 columns.  
Sets: **Month**: worked out from [Order Day] (rule in the details)  
**Gross** = [Qty] × [UnitPrice]  
**Discount** = 0.1 when [Gross] ≥ 10000; 0.05 when [Gross] ≥ 5000 and [Region] is one of "North", "East", otherwise 0  
**Net**: worked out from [Gross], [Discount] (rule in the details)  

- **1.6** Formula #6 · exact · `t6`

      Month = DateTimeFormat([Order Day], "%Y-%m")
      Gross = [Qty] × [UnitPrice]
      Discount =
          when [Gross] ≥ 10000                                       → 0.1
          when [Gross] ≥ 5000 and [Region] is one of "North", "East" → 0.05
          otherwise                                                  → 0
      Net = Round([Gross] × (1 - [Discount]), 0.01)
      # pandas:
      # df["Month"] = df.apply(x6_1, axis=1)  # generated code; no one-line form
      df["Gross"] = (df["Qty"] * df["UnitPrice"])
      df["Discount"] = np.select([(df["Gross"] >= 10000), ((df["Gross"] >= 5000) & df["Region"].isin(['North', 'East']))], [0.1, 0.05], default=0)
      df["Net"] = (((df["Gross"] * (1 - df["Discount"])) / 0.01).round() * 0.01)

**4. Unique** · step 1.7 · tool #7  
Removes duplicate rows by [OrderID] (keeps the first).  
Looks at: [OrderID]  
↳ **first row of each** → **sales_summary.xlsx › Summary** (container 2) · **duplicate rows** → **duplicate_orders.csv**  

- **1.7** Unique #7 · converted, assuming A9 · `t7`

      s1_7_unique = df.drop_duplicates(["OrderID"])
      s1_7_duplicates = df[df.duplicated(["OrderID"])]

#### duplicate_orders.csv

1 step, from orders.csv, split on Status, duplicates removed on OrderID.

**1. Output Data** · step 1.8 · tool #8  
Saves the table to `duplicate_orders.csv`.  
Starts from: **orders.csv, split on Status, duplicates removed on OrderID** (duplicate rows)  

- **1.8** Output Data #8 · converted, assuming A5 · `t8` · `s1_8 = df  # the rows this step saves; the notebook's last cell writes them to OUT`

#### cancelled_by_region.csv

2 steps, from orders.csv, split on Status.

**1. Summarize** · step 1.9 · tool #16  
Groups the rows by [Region] and calculates **Cancelled orders** (like a pivot table).  
Starts from: **orders.csv, split on Status** (rows that fail)  
Looks at: [Region] [OrderID]  
Adds: [Cancelled orders]  

- **1.9** Summarize #16 · converted, assuming A7 · `t16`

      Cancelled orders = number of rows
      # pandas:
      df = df.groupby(["Region"], sort=True, dropna=False).agg(**{"Cancelled orders": ("OrderID", "size")}).reset_index()

**2. Output Data** · step 1.10 · tool #17  
Saves the table to `cancelled_by_region.csv`.  

- **1.10** Output Data #17 · converted, assuming A5 · `t17` · `s1_10 = df  # the rows this step saves; the notebook's last cell writes them to OUT`

### Container 2. Summarise

#### sales_summary.xlsx › Summary

6 steps, from orders.csv, split on Status, duplicates removed on OrderID, a typed table ([Product], [Category]; 3 rows).

**1. Join** · step 2.1 · tool #10  
Matches its rows with a typed table ([Product], [Category]; 3 rows) on [Product].  
Starts from: **orders.csv, split on Status, duplicates removed on OrderID** (container 1) (first row of each)  
Looks at: [Product]  
↳ **matched rows** → step 3 · **rows only in the first input** → step 2 · **rows only in the second input** → dropped  

- **2.1** Join #10 · converted, assuming A4 · `t10`

      keys: left [Product] = right [Product]
      drop Right_Product
      # pandas:
      right = src_typed_product_category.rename(columns=lambda c: "Right_" + c if c in df.columns else c)  # as Alteryx: only clashing names get Right_
      m = df.merge(right, how="outer", left_on=["Product"], right_on=[c if c in right.columns else "Right_" + c for c in ["Product"]], indicator=True)
      m = m.drop(columns=["Right_Product"], errors="ignore")
      s2_1_join = m[m["_merge"] == "both"].drop(columns="_merge")
      s2_1_left = m[m["_merge"] == "left_only"][[c for c in df.columns if c in m.columns]]

**2. Formula** · step 2.2 · tool #11  
Sets 1 column.  
Takes: step 1 (rows only in the first input)  
Sets: **Category** = "Unknown"  

- **2.2** Formula #11 · exact · `t11`

      Category = "Unknown"
      # pandas:
      df["Category"] = 'Unknown'

**3. Union** · step 2.3 · tool #12  
Stacks the tables step 1 (matched rows), step 2 (columns matched by name).  

- **2.3** Union #12 · converted, assuming A8 · `t12` · `df = pd.concat([s2_1_join, s2_2], ignore_index=True)`

**4. Summarize** · step 2.4 · tool #13  
Groups the rows by [Region], [Month], [Category] and calculates **Net sales**, **Orders** (like a pivot table).  
Looks at: [Region] [Month] [Category] [Net] [OrderID]  
Adds: [Net sales] [Orders]  

- **2.4** Summarize #13 · converted, assuming A7 · `t13`

      Net sales = sum of [Net]
      Orders = number of different [OrderID]
      # pandas:
      df = df.groupby(["Region", "Month", "Category"], sort=True, dropna=False).agg(**{"Net sales": ("Net", "sum"), "Orders": ("OrderID", "nunique")}).reset_index()

**5. Sort** · step 2.5 · tool #14  
Sorts by [Region] A→Z, [Month] A→Z.  
Looks at: [Region] [Month]  

- **2.5** Sort #14 · converted, assuming A6 · `t14` · `df = df.sort_values(["Region", "Month"], ascending=[True, True], kind="stable", na_position="first")`

**6. Output Data** · step 2.6 · tool #15  
Saves the table to `sales_summary.xlsx › Summary` (replaces the sheet).  

- **2.6** Output Data #15 · converted, assuming A5 · `t15` · `s2_6 = df  # the rows this step saves; the notebook's last cell writes them to OUT`

## 3. Run order

Inside a story, steps run in the order shown. What must finish before what:

1. Container 2 runs after container 1: it takes rows from **orders.csv, split on Status, duplicates removed on OrderID**.

## 4. Files, sheets and scripts

| File | Sheet | Cells | Used | Columns | Where |
|---|---|---|---|---|---|
| `orders.csv` | — | all | opened | [OrderID], [OrderDate], [Region], [Product], [Qty], [UnitPrice] +1 more | orders.csv, split on Status (1.1) |
| `duplicate_orders.csv` | — | all | saved | — | duplicate_orders.csv (1.8) |
| `cancelled_by_region.csv` | — | all | saved | — | cancelled_by_region.csv (1.10) |
| `sales_summary.xlsx` | Summary | all | saved | — | sales_summary.xlsx › Summary (2.6) |

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

- **Alteryx behaviours the Python assumes** (steps refer to them as A1, A2…; settle them once with the probe workflow):
  - **A1** — Cleanse: the check-box numbers are mapped to options from memory of the standard macro; confirm against Cleanse.yxmc
  - **A2** — DateTime: English only; output type for a date-only format is assumed to be Date
  - **A3** — Input: column types depend on Alteryx's file driver; the types recorded in the workflow are applied when present
  - **A4** — Join: row order of the three outputs and null = null on keys are not documented
  - **A5** — Output: cell formats written by Alteryx are not reproduced; writes only with Context(write=True)
  - **A6** — Sort: position of nulls and the order of upper/lower case are not documented
  - **A7** — Summarize: output order, Sum of no values and Concat of empty values are not documented
  - **A8** — Union: the type chosen when inputs disagree, and the order of the inputs, are not documented
  - **A9** — Unique: output order is assumed to be the input order

### Columns created or changed in container 1 (13)

Each column this container creates or changes: the type Alteryx gives it, the steps that set it, how, and the later steps (anywhere in the workflow) that use a column of that name.

| Column | Type | Set at | How | Used later by |
|---|---|---|---|---|
| **OrderID** | V_String | 1.1 | read from the file | 1.7, 1.9, 2.4 |
| **OrderDate** | V_String | 1.1 | read from the file | 1.5 |
| **Region** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 1.6, 1.9, 2.4, 2.5 |
| **Product** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 2.1 |
| **Qty** | V_String, Int32 | 1.1, 1.4 | read from the file / type changed | 1.4, 1.6 |
| **UnitPrice** | V_String, Double | 1.1, 1.4 | read from the file / type changed | 1.4, 1.6 |
| **Status** | V_String | 1.1, 1.2 | read from the file / cleaned: remove leading and trailing spaces | 1.2, 1.3 |
| **Order Day** |  | 1.5 | [OrderDate] read as a date (dd/MM/yyyy) | 1.6 |
| **Month** | V_WString | 1.6 | DateTimeFormat([Order Day], "%Y-%m") | 2.4, 2.5 |
| **Gross** | Double | 1.6 | [Qty] × [UnitPrice] | not read by a later step |
| **Discount** | Double | 1.6 | (when [Gross] ≥ 10000 → 0.1; when [Gross] ≥ 5000 and [Region] is one of "North", "East" → 0.05; otherwise 0) | not read by a later step |
| **Net** | Double | 1.6 | Round([Gross] × (1 - [Discount]), 0.01) | 2.4 |
| **Cancelled orders** |  | 1.9 | number of rows | not read by a later step |

### Columns created or changed in container 2 (4)

Each column this container creates or changes: the type Alteryx gives it, the steps that set it, how, and the later steps (anywhere in the workflow) that use a column of that name.

| Column | Type | Set at | How | Used later by |
|---|---|---|---|---|
| **Product** |  | a typed table ([Product], [Category]; 3 rows) | typed in the workflow | 2.1 |
| **Category** | V_WString | a typed table ([Product], [Category]; 3 rows), 2.2 | typed in the workflow / "Unknown" | 2.4 |
| **Net sales** |  | 2.4 | sum of [Net] | not read by a later step |
| **Orders** |  | 2.4 | number of different [OrderID] | not read by a later step |

### Every step by number (16)

| Step | Tool | Told in | Tool ID |
|---|---|---|---|
| 1.1 | Input Data | orders.csv, split on Status | #1 |
| 1.2 | Data Cleansing | orders.csv, split on Status | #2 |
| 1.3 | Filter | orders.csv, split on Status | #3 |
| 1.4 | Select | orders.csv, split on Status, duplicates removed on OrderID | #4 |
| 1.5 | DateTime | orders.csv, split on Status, duplicates removed on OrderID | #5 |
| 1.6 | Formula | orders.csv, split on Status, duplicates removed on OrderID | #6 |
| 1.7 | Unique | orders.csv, split on Status, duplicates removed on OrderID | #7 |
| 1.8 | Output Data | duplicate_orders.csv | #8 |
| 1.9 | Summarize | cancelled_by_region.csv | #16 |
| 1.10 | Output Data | cancelled_by_region.csv | #17 |
| 2.1 | Join | sales_summary.xlsx › Summary | #10 |
| 2.2 | Formula | sales_summary.xlsx › Summary | #11 |
| 2.3 | Union | sales_summary.xlsx › Summary | #12 |
| 2.4 | Summarize | sales_summary.xlsx › Summary | #13 |
| 2.5 | Sort | sales_summary.xlsx › Summary | #14 |
| 2.6 | Output Data | sales_summary.xlsx › Summary | #15 |
