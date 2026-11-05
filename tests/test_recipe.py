import pandas as pd
import pytest

from toolbank import *

SPEC = """
name: fx breaks
inputs:
  rates: {fn: read_sheet, path: rates.xlsx}
steps:
  - {fn: read_sheet, path: trades.xlsx, required: [trade_date, Amount, ccy]}
  - {fn: cleanse, case: upper}
  - {fn: filter_rows, conditions: [[ccy, in, "USD,SGD"]], pick: 0, name: majors}
  - {fn: lookup, ref: $rates, key: ccy, cols: rate}
  - {fn: control_totals, amount_col: rate, as: totals}
  - {fn: safe_write_excel, path: out/breaks.xlsx, sheets: {Breaks: $df}, overwrite: true, as: report}
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({"Trade Date": ["06/03/2026", "07/03/2026", "08/03/2026"], "Amount": ["1,000", "2,000", "x"],
                  "ccy": ["usd", "SGD ", "EUR"]}).to_excel("trades.xlsx", index=False)
    pd.DataFrame({"ccy": ["USD", "SGD"], "rate": [1.35, 1.0]}).to_excel("rates.xlsx", index=False)
    (tmp_path / "r.yaml").write_text(SPEC)
    return tmp_path


def test_load_validate_run(files):
    r = load_recipe("r.yaml")
    assert r == load_recipe(SPEC) == load_recipe(r) and validate_recipe(r) == []
    out, trace = run_recipe(r, log=lambda m: None)
    assert out["ccy"].tolist() == ["USD", "SGD"] and out["rate"].tolist() == [1.35, 1.0]
    assert trace["step"].tolist() == ["read_sheet", "cleanse", "majors", "lookup", "control_totals", "safe_write_excel"]
    assert trace["rows_out"].tolist() == [3, 3, 2, 2, 2, 2] and (trace["status"] == "OK").all()
    assert (files / "out" / "breaks.xlsx").exists()


def test_generated_code_runs(files):
    r = load_recipe("r.yaml")
    code = recipe_to_code(r, path="gen.py")
    assert code == recipe_to_code(r) == (files / "gen.py").read_text(encoding="utf-8")   # deterministic
    assert "def run(df=None):" in code and "rates = read_sheet(path='rates.xlsx')" in code
    assert "df = filter_rows(df, ['ccy', 'in', 'USD,SGD'])[0]  # majors" in code
    assert "df = lookup(df, ref=rates, key='ccy', cols='rate')" in code
    assert "report = safe_write_excel(path='out/breaks.xlsx', sheets={'Breaks': df}, overwrite=True)" in code
    ns = {}
    exec(compile(code, "gen.py", "exec"), ns)
    assert ns["run"]().shape == (2, 4)


def test_validation_messages():
    bad = {"steps": [{"fn": "read_shet", "path": "x"}, {"fn": "lookup", "refkey": 1, "ref": "$rates"},
                     {"fn": "cleanse", "df": 1, "pick": "a", "as": "class"}, {"fn": "filter_rows"}, "oops"]}
    probs = validate_recipe(bad)
    assert probs == [
        "steps[0] (read_shet): unknown function 'read_shet'; did you mean ['read_sheet', 'read_all_sheets', 'read_since']?",
        "steps[1] (lookup): unknown parameter 'refkey'; did you mean 'ref_key'?",
        "steps[1] (lookup): parameter 'key' is required", "steps[1] (lookup): parameter 'cols' is required",
        "steps[1] (lookup): $rates is not an input or an earlier 'as:' name (known: ['df'])",
        "steps[2] (cleanse): 'df' is passed automatically (the current frame); do not set it",
        "steps[2] (cleanse): pick must be a whole number",
        "steps[2] (cleanse): 'as' must be a plain variable name, not 'class'",
        "steps[4]: needs fn: <toolbank function>"]
    assert validate_recipe({"steps": [{"fn": "cleanse"}]}) == [
        "steps[0] (cleanse): takes a frame as its first argument but it is the first step; give run_recipe a df "
        "or start with a reader"]
    assert validate_recipe({"steps": [{"fn": "cleanse"}]}, with_input=True) == []
    assert validate_recipe("nope") == ["recipe must be a mapping with 'steps'"]
    assert validate_recipe({"steps": []}) == ["steps must be a non-empty list"]
    assert validate_recipe({"inputs": {"df": {"fn": "read_sheet", "path": "x"}}, "steps": [{"fn": "read_sheet", "path": "y"}]})[0] \
        .startswith("inputs.df: name must be")
    with pytest.raises(ValueError, match="bad recipe: steps\\[0\\]"):
        load_recipe({"steps": [{"fn": "nope"}]})


def test_recipe_with_input_frame():
    r = {"steps": [{"fn": "cleanse", "case": "upper"}, {"fn": "filter_rows", "conditions": [["a", "==", "X"]], "pick": 0}]}
    out, trace = run_recipe(r, df=pd.DataFrame({"a": [" x ", "y"]}), log=lambda m: None)
    assert out["a"].tolist() == ["X"] and trace["rows_in"].tolist() == [2, 2]
    code = recipe_to_code(r)
    assert "def run(df):" in code and "out = run(read_any(sys.argv[1]))" in code
    compile(code, "gen.py", "exec")
    with pytest.raises(ValueError, match="start with a reader"):
        run_recipe(r)
    flow = recipe_to_flow({"steps": [{"fn": "cleanse"}, {"fn": "cleanse", "case": "lower"}]}, log=lambda m: None)
    flow.run(pd.DataFrame({"a": ["A"]}))
    assert flow.trace["step"].tolist() == ["cleanse", "cleanse_2"]
