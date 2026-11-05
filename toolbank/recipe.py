"""Recipes: a robot's data steps as a YAML / dict spec that is validated against toolbank's own function
signatures, run as a Flow (row counts and timings per step), or turned into a plain Python script.
The same spec always gives the same script: a systematic, deterministic way to build up robot code.

    name: fx_breaks
    inputs:                                   # loaded first; refer to them as $rates
      rates: {fn: read_sheet, path: rates.xlsx}
    steps:
      - {fn: read_sheet, path: in/trades.xlsx, required: [Trade Date, Amount, ccy]}
      - {fn: cleanse, case: upper}
      - {fn: filter_rows, conditions: [[ccy, in, "USD,SGD"]], pick: 0, label: majors}
      - {fn: lookup, ref: $rates, key: ccy, cols: rate}
      - {fn: safe_write_excel, path: out/breaks.xlsx, sheets: {Breaks: $df}, overwrite: true, as: report}

A step receives the current frame as its first argument when the function's first parameter is called `df`
(cleanse, lookup, filter_rows...); functions that take frames under other names (reconcile, join3: left / right)
get it explicitly as `left: $df`. `$df` names the current frame inside any parameter, `$name` an input or an
earlier `as:` result ('$$' starts a literal dollar: '$$1,000'). `pick: n` takes element n of a tuple result
(filter_rows, join3...), `as: name` keeps a result under a name instead of replacing the frame (writes, control
totals), `label:` names the step in the trace. Files are read with config.yaml_load, so 00123 and NO stay text."""
import datetime
import difflib
import importlib
import inspect
import json
import keyword
import math
import os
from pathlib import Path

import pandas as pd

from .config import yaml_load
from .flow import Flow
from .robust import RobotError

_RESERVED = ("fn", "label", "pick", "as")
_NOT_ALIAS = {"df", "pd", "sys", "out", "run"}
_FILE_SUFFIXES = (".yaml", ".yml", ".json")


def _toolbank():
    return importlib.import_module("toolbank")


def _fn(name):
    """toolbank function by name, or None (classes and private names do not count)."""
    obj = getattr(_toolbank(), str(name), None)
    return obj if inspect.isfunction(obj) and not str(name).startswith("_") else None


def _takes_frame(fn):
    params = list(inspect.signature(fn).parameters.values())
    return bool(params) and params[0].name == "df" and params[0].kind in (params[0].POSITIONAL_ONLY,
                                                                          params[0].POSITIONAL_OR_KEYWORD)


def _ref(value):
    """Name a $reference points to, or None ('$$x' is the literal text '$x', '$1,000' is text)."""
    if isinstance(value, str) and value.startswith("$") and not value.startswith("$$") and value[1:].isidentifier():
        return value[1:]
    return None


def _refs(value):
    """Every $name reference inside a parameter value (nested lists / dicts included)."""
    name = _ref(value)
    if name is not None:
        return [name]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _refs(v)]
    if isinstance(value, (list, tuple)):
        return [r for v in value for r in _refs(v)]
    return []


def _resolve(value, env):
    name = _ref(value)
    if name is not None:
        return env[name]
    if isinstance(value, str) and value.startswith("$$"):
        return value[1:]
    if isinstance(value, dict):
        return {k: _resolve(v, env) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_resolve(v, env) for v in value)
    return value


def _read(source):
    """Recipe data from a path (.yaml / .yml / .json), YAML text or a dict."""
    if isinstance(source, dict):
        return source
    text = str(source)
    looks_like_path = isinstance(source, Path) or ("\n" not in text and (
        Path(text).suffix.lower() in _FILE_SUFFIXES or os.sep in text or "/" in text))
    try:
        is_file = "\n" not in text and Path(text).is_file()
    except OSError:                                       # too long / odd characters for a file name
        is_file = False
    if is_file:
        p = Path(text)
        body = p.read_text(encoding="utf-8-sig")
        return json.loads(body) if p.suffix.lower() == ".json" else yaml_load(body)
    if looks_like_path:
        raise FileNotFoundError(f"recipe file not found: {text}")
    data = yaml_load(text)
    if isinstance(data, str):
        raise ValueError(f"recipe text is plain text {data[:60]!r}: give a .yaml path or YAML with 'steps:'")
    return data


def load_recipe(source):
    """Recipe from a .yaml / .json path, YAML text, or a dict -> dict. A missing file -> FileNotFoundError;
    a bad spec -> ValueError listing every problem found by validate_recipe, so it never half-runs."""
    recipe = _read(source)
    problems = validate_recipe(recipe)
    if problems:
        raise ValueError("bad recipe: " + "; ".join(problems))
    return recipe


def _alias_problem(alias, label):
    if not isinstance(alias, str) or not alias.isidentifier() or keyword.iskeyword(alias):
        return f"{label}: 'as' must be a plain variable name, not {alias!r}"
    if alias in _NOT_ALIAS:
        return f"{label}: {alias!r} is reserved ('df' is the current frame)"
    if alias in _toolbank().__all__:
        return f"{label}: {alias!r} is the name of a toolbank function; choose another name"
    return None


def _check_call(label, spec, known, has_frame, problems, inputs=False):
    """Problems with one function call spec (a step or an input); returns the function or None."""
    if not isinstance(spec, dict) or not spec.get("fn"):
        problems.append(f"{label}: needs fn: <toolbank function>")
        return None
    fn = _fn(spec["fn"])
    if fn is None:
        names = [n for n in _toolbank().__all__ if _fn(n)]
        near = difflib.get_close_matches(str(spec["fn"]), names, n=3, cutoff=0.6)
        problems.append(f"{label}: unknown function {spec['fn']!r}" + (f"; did you mean {near}?" if near else ""))
        return None
    params = list(inspect.signature(fn).parameters.values())
    var_kw = any(p.kind == p.VAR_KEYWORD for p in params)
    var_pos = next((p.name for p in params if p.kind == p.VAR_POSITIONAL), None)
    takes = _takes_frame(fn)
    frame = takes and has_frame
    names = [p.name for p in params if p.kind != p.VAR_KEYWORD]
    given = {}
    for k, v in spec.items():
        if k in _RESERVED:
            continue
        if not isinstance(k, str) or not k.isidentifier():
            problems.append(f"{label}: parameter key {k!r} is not a plain name (a YAML 1.1 loader turns on / yes "
                            "/ no into booleans: read the file with load_recipe, or quote the key: 'on': ccy)")
            continue
        given[k] = v
    for k in given:
        if k in names:
            continue
        near = difflib.get_close_matches(k, names, n=1, cutoff=0.75 if var_kw else 0.6)
        if not var_kw or near:
            problems.append(f"{label}: unknown parameter {k!r}" + (f"; did you mean {near[0]!r}?" if near else ""))
    for i, p in enumerate(params):
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.default is not p.empty or p.name in given:
            continue
        if i == 0 and frame:
            continue
        if i == 0 and takes and not inputs:
            continue                                      # validate_recipe explains a missing first frame
        hint = ""
        if i == 0 and takes and inputs:
            hint = " (an input gets no frame: give it as df: $other_input)"
        elif i == 0 and has_frame:
            hint = " (the current frame is $df)"
        problems.append(f"{label}: parameter {p.name!r} is required{hint}")
    if frame and params and params[0].name in given:
        problems.append(f"{label}: 'df' is passed automatically (the current frame); do not set it")
    if var_pos and var_pos in given and given[var_pos] is not None and not isinstance(given[var_pos], (list, tuple)):
        problems.append(f"{label}: {var_pos!r} must be a list with one entry per argument "
                        f"(e.g. [[ccy, in, 'USD,SGD']]), not {type(given[var_pos]).__name__}")
    for ref in dict.fromkeys(r for v in given.values() for r in _refs(v)):
        if ref not in known:
            problems.append(f"{label}: ${ref} is not an input or an earlier 'as:' name (known: {sorted(known)})")
    if "pick" in spec and (not isinstance(spec["pick"], int) or isinstance(spec["pick"], bool)):
        problems.append(f"{label}: pick must be a whole number")
    if "label" in spec and not isinstance(spec["label"], str):
        problems.append(f"{label}: label must be text")
    if inputs and ("as" in spec or "label" in spec):
        problems.append(f"{label}: 'as' and 'label' are for steps, not inputs")
    elif "as" in spec:
        bad = _alias_problem(spec["as"], label)
        if bad:
            problems.append(bad)
    return fn


def validate_recipe(recipe, with_input=False):
    """Problems with a recipe ([] = OK): unknown functions and parameters (with 'did you mean'), missing required
    parameters, unresolvable $references, keys YAML turned into booleans, bad pick / as / label, a frame step
    before any frame exists. with_input=True when run_recipe will be given a frame for the first step."""
    problems = []
    if not isinstance(recipe, dict):
        return ["recipe must be a mapping with 'steps'"]
    inputs = recipe.get("inputs") or {}
    if not isinstance(inputs, dict):
        problems.append("inputs must be a mapping of name -> {fn: ..., ...}")
        inputs = {}
    known = set()
    for alias, spec in inputs.items():
        bad = _alias_problem(alias, f"inputs.{alias}")
        if bad:
            problems.append(bad.replace("'as' must be", "name must be"))
        _check_call(f"inputs.{alias}", spec, known, has_frame=False, problems=problems, inputs=True)
        known.add(str(alias))
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps must be a non-empty list")
        return problems
    has_frame = with_input
    for i, spec in enumerate(steps):
        label = f"steps[{i}]" + (f" ({spec.get('fn')})" if isinstance(spec, dict) and spec.get("fn") else "")
        fn = _check_call(label, spec, known | ({"df"} if has_frame else set()), has_frame, problems)
        if fn is not None and not has_frame and _takes_frame(fn):
            problems.append(f"{label}: takes a frame as its first argument but no step produced one yet; give "
                            "run_recipe a df or start with a reader")
        alias = spec.get("as") if isinstance(spec, dict) else None
        if alias is None or alias == "df":
            has_frame = True                              # the step's result is the new frame
        elif not has_frame:
            problems.append(f"{label}: 'as' before any step produced a frame; load it under inputs: instead")
        elif isinstance(alias, str) and alias.isidentifier():
            known.add(alias)
    return problems


def _split_args(fn, params):
    """(positional values, keyword values) for a call. With a *args parameter, every parameter before it goes
    positionally (Python allows no keyword before *args) and the *args value is spread; an explicit df: $input
    is positional too."""
    names = list(inspect.signature(fn).parameters.values())
    var_pos = next((p.name for p in names if p.kind == p.VAR_POSITIONAL), None)
    kw, pos = dict(params), []
    if names and names[0].name == "df" and names[0].kind != names[0].KEYWORD_ONLY and "df" in kw:
        pos.append(kw.pop("df"))
    if var_pos:
        for p in names:
            if p.kind == p.VAR_POSITIONAL:
                break
            if p.name == "df" and (pos or "df" not in kw):
                continue                                  # the frame is the caller's first positional
            if p.name in kw:
                pos.append(kw.pop(p.name))
            elif p.default is not p.empty:
                pos.append(p.default)
            else:
                break                                     # a required one is missing: validation reports it
        pos += list(kw.pop(var_pos, None) or [])
    return pos, kw


def _invoke(fn, spec, env, df, frame, label):
    """Call one step or input with its parameters resolved; apply pick with clear errors."""
    params = {k: v for k, v in spec.items() if k not in _RESERVED}
    pos, kw = _split_args(fn, _resolve(params, env))
    if frame and df is None:
        raise RobotError(f"{label}: {fn.__name__} needs a frame but no step produced one yet", code="RECIPE")
    result = fn(df, *pos, **kw) if frame else fn(*pos, **kw)
    pick = spec.get("pick")
    if pick is not None:
        if not isinstance(result, (tuple, list)):
            raise RobotError(f"{label}: pick given but {fn.__name__} returned {type(result).__name__}, not a tuple",
                             code="RECIPE")
        if not -len(result) <= pick < len(result):
            raise RobotError(f"{label}: pick {pick} is out of range for {len(result)} results", code="RECIPE")
        result = result[pick]
    return result


def recipe_to_flow(recipe, inputs=None, name=None, keep=False, log=print):
    """Flow for a validated recipe. inputs = {alias: value} already loaded (run_recipe does this for you).
    Every step is one Flow step, so .trace shows rows in / out and timings; a step that does not return a
    DataFrame (and has no as:) stops the flow with RobotError RECIPE."""
    env = dict(inputs or {})
    flow = Flow(name or recipe.get("name") or "recipe", keep=keep, log=log)
    used = set()
    for spec in recipe["steps"]:
        fn, frame = _fn(spec["fn"]), _takes_frame(_fn(spec["fn"]))
        alias = spec.get("as") or "df"
        base = str(spec.get("label") or spec["fn"])
        label, n = base, 1
        while label in used:
            n += 1
            label = f"{base}_{n}"
        used.add(label)

        def run_step(df=None, fn=fn, frame=frame, spec=spec, alias=alias, label=label):
            result = _invoke(fn, spec, {**env, "df": df}, df, frame, label)
            if alias == "df":
                if not isinstance(result, pd.DataFrame):
                    raise RobotError(f"{label}: {fn.__name__} returned {type(result).__name__}, not a DataFrame; "
                                     "add as: <name> to keep it, or pick: n for a tuple", code="RECIPE")
                return result
            env[alias] = result
            return df
        flow.step(label, run_step)
    return flow


def run_recipe(recipe, df=None, keep=False, log=print):
    """Load the inputs, run the steps -> (frame, trace DataFrame). Validates first (ValueError lists every
    problem). df = a frame for the first step when the recipe does not start with a reader."""
    problems = validate_recipe(recipe, with_input=df is not None)
    if problems:
        raise ValueError("bad recipe: " + "; ".join(problems))
    inputs = {}
    for alias, spec in (recipe.get("inputs") or {}).items():
        inputs[alias] = _invoke(_fn(spec["fn"]), spec, inputs, None, False, f"inputs.{alias}")
    flow = recipe_to_flow(recipe, inputs, keep=keep, log=log)
    out = flow.run(df)
    return out, flow.trace


def _py(value, key=False):
    """Python source for a parameter value; $name references become plain names (not inside dict keys)."""
    name = None if key else _ref(value)
    if name is not None:
        return name
    if isinstance(value, str):
        return repr(value[1:] if value.startswith("$$") and not key else value)
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, float) and not math.isfinite(value):
        return f"float({str(value)!r})"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return f"pd.Timestamp({value.isoformat()!r})"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py(k, key=True)}: {_py(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_py(v) for v in value) + "]"
    if isinstance(value, tuple):
        return "(" + ", ".join(_py(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    return repr(value)


def _call(spec, frame):
    fn = _fn(spec["fn"])
    params = {k: v for k, v in spec.items() if k not in _RESERVED}
    pos, kw = _split_args(fn, params)
    args = (["df"] if frame else []) + [_py(v) for v in pos] + [f"{k}={_py(v)}" for k, v in kw.items()]
    text = f"{spec['fn']}({', '.join(args)})"
    return text + (f"[{spec['pick']}]" if spec.get("pick") is not None else "")


def recipe_to_code(recipe, path=None):
    """Plain Python script for a validated recipe (deterministic: same recipe, same text); saved to path if
    given. The script needs only `from toolbank import *`, so it runs and can be edited without the recipe.
    Dates from YAML become pd.Timestamp(...), so the script and run_recipe agree."""
    problems = validate_recipe(recipe)
    first_frame = False
    if problems:                                          # maybe valid with an input frame
        first_frame = not validate_recipe(recipe, with_input=True)
        if not first_frame:
            raise ValueError("bad recipe: " + "; ".join(problems))
    name = "_".join(str(recipe.get("name") or "recipe").split()) or "recipe"
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in name).strip("_") or "recipe"
    lines = [f'"""{name}: generated by toolbank.recipe. Plain Python: edit freely, or change the recipe and',
             'generate again."""', "import pandas as pd  # noqa: F401", "from toolbank import *  # noqa: F403",
             "", "", f"def run({'df' if first_frame else 'df=None'}):"]
    for alias, spec in (recipe.get("inputs") or {}).items():
        lines.append(f"    {alias} = {_call(spec, False)}")
    has_frame = first_frame
    for spec in recipe["steps"]:
        frame = _takes_frame(_fn(spec["fn"])) and has_frame
        target = spec.get("as") or "df"
        comment = f"  # {' '.join(str(spec['label']).split())}" if spec.get("label") else ""
        lines.append(f"    {target} = {_call(spec, frame)}{comment}")
        if target == "df":
            has_frame = True
    main = ["    import sys", "    out = run(read_any(sys.argv[1]))"] if first_frame else ["    out = run()"]
    lines += ["    return df", "", "", 'if __name__ == "__main__":', *main,
              '    print(f"{len(out):,} rows x {out.shape[1]} cols" if isinstance(out, pd.DataFrame) else "done")', ""]
    code = "\n".join(lines)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(code, encoding="utf-8")
    return code
