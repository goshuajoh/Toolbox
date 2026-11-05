"""Recipes: a robot's data steps as a YAML / dict spec that is validated against toolbank's own function
signatures, run as a Flow (row counts and timings per step), or turned into a plain Python script.
The same spec always gives the same script: a systematic, deterministic way to build up robot code.

    name: fx_breaks
    inputs:                                   # loaded first; refer to them as $rates
      rates: {fn: read_sheet, path: rates.xlsx}
    steps:
      - {fn: read_sheet, path: in/trades.xlsx, required: [Trade Date, Amount, ccy]}
      - {fn: cleanse, case: upper}
      - {fn: filter_rows, conditions: [[ccy, in, "USD,SGD"]], pick: 0}
      - {fn: lookup, ref: $rates, key: ccy, cols: rate}
      - {fn: safe_write_excel, path: out/breaks.xlsx, sheets: {Breaks: $df}, overwrite: true, as: report}

A step receives the current frame as its first argument when the function's first parameter is called `df`
(every DataFrame-in / DataFrame-out function in toolbank). $df names the current frame inside any parameter,
pick: n takes element n of a tuple result (filter_rows, join3...), as: name keeps a result under a name instead
of replacing the frame (writes, control totals) and later steps can use $name."""
import difflib
import importlib
import inspect
import keyword
import re
from pathlib import Path

from .flow import Flow

_RESERVED = ("fn", "name", "pick", "as")


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


def _refs(value):
    """Every $name reference inside a parameter value (nested lists / dicts included)."""
    if isinstance(value, str):
        return [value[1:]] if value.startswith("$") and value[1:] else []
    if isinstance(value, dict):
        return [r for v in value.values() for r in _refs(v)]
    if isinstance(value, (list, tuple)):
        return [r for v in value for r in _refs(v)]
    return []


def _resolve(value, env):
    if isinstance(value, str) and value.startswith("$") and value[1:]:
        return env[value[1:]]
    if isinstance(value, dict):
        return {k: _resolve(v, env) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_resolve(v, env) for v in value)
    return value


def load_recipe(source):
    """Recipe from a .yaml / .json path, YAML text, or a dict -> dict. Raises ValueError listing every problem
    found by validate_recipe, so a bad spec never half-runs."""
    if isinstance(source, dict):
        recipe = source
    else:
        text = str(source)
        try:
            is_path = "\n" not in text and Path(text).is_file()
        except OSError:                                   # too long / odd characters for a file name
            is_path = False
        if is_path:
            text = Path(text).read_text(encoding="utf-8")
        import yaml
        recipe = yaml.safe_load(text)
    problems = validate_recipe(recipe)
    if problems:
        raise ValueError("bad recipe: " + "; ".join(problems))
    return recipe


def _check_call(label, spec, known, takes_frame_ok, problems):
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
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    var_kw = any(p.kind == p.VAR_KEYWORD for p in params)
    var_pos = next((p.name for p in params if p.kind == p.VAR_POSITIONAL), None)
    frame = _takes_frame(fn) and takes_frame_ok
    names = [p.name for p in params if p.kind not in (p.VAR_KEYWORD,)]
    given = {k: v for k, v in spec.items() if k not in _RESERVED}
    for k in given:
        if k not in names and not var_kw:
            near = difflib.get_close_matches(k, names, n=1, cutoff=0.6)
            problems.append(f"{label}: unknown parameter {k!r}" + (f"; did you mean {near[0]!r}?" if near else ""))
    for p in params[1 if frame or _takes_frame(fn) else 0:]:      # the caller explains a missing first frame
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.default is not p.empty or p.name in given:
            continue
        if p.name == var_pos:
            continue
        problems.append(f"{label}: parameter {p.name!r} is required")
    if frame and params and params[0].name in given:
        problems.append(f"{label}: 'df' is passed automatically (the current frame); do not set it")
    for ref in {r for v in given.values() for r in _refs(v)}:
        if ref not in known:
            problems.append(f"{label}: ${ref} is not an input or an earlier 'as:' name (known: {sorted(known)})")
    if "pick" in spec and not isinstance(spec["pick"], int):
        problems.append(f"{label}: pick must be a whole number")
    alias = spec.get("as")
    if alias is not None and (not isinstance(alias, str) or not alias.isidentifier() or keyword.iskeyword(alias)):
        problems.append(f"{label}: 'as' must be a plain variable name, not {alias!r}")
    return fn


def validate_recipe(recipe, with_input=False):
    """Problems with a recipe ([] = OK): unknown functions and parameters (with 'did you mean'), missing required
    parameters, unresolvable $references, bad pick / as. with_input=True when run_recipe will be given a frame
    for the first step."""
    problems = []
    if not isinstance(recipe, dict):
        return ["recipe must be a mapping with 'steps'"]
    inputs = recipe.get("inputs") or {}
    if not isinstance(inputs, dict):
        problems.append("inputs must be a mapping of name -> {fn: ..., ...}")
        inputs = {}
    known = set()
    for alias, spec in inputs.items():
        if not str(alias).isidentifier() or keyword.iskeyword(str(alias)) or alias == "df":
            problems.append(f"inputs.{alias}: name must be a plain variable name (not 'df')")
        _check_call(f"inputs.{alias}", spec, known, takes_frame_ok=False, problems=problems)
        known.add(str(alias))
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps must be a non-empty list")
        return problems
    for i, spec in enumerate(steps):
        label = f"steps[{i}]" + (f" ({spec.get('fn')})" if isinstance(spec, dict) and spec.get("fn") else "")
        has_frame = i > 0 or with_input
        fn = _check_call(label, spec, known | ({"df"} if has_frame else set()), has_frame, problems)
        if fn is not None and not has_frame and _takes_frame(fn):
            problems.append(f"{label}: takes a frame as its first argument but it is the first step; give "
                            "run_recipe a df or start with a reader")
        if isinstance(spec, dict) and spec.get("as") and spec["as"] != "df":
            known.add(spec["as"])
    return problems


def _split_args(fn, params):
    """(positional values, keyword values) for a call: a value for the *args parameter is spread positionally."""
    sig = inspect.signature(fn)
    var_pos = next((p.name for p in sig.parameters.values() if p.kind == p.VAR_POSITIONAL), None)
    pos = list(params[var_pos]) if var_pos and var_pos in params else []
    kw = {k: v for k, v in params.items() if k != var_pos}
    return pos, kw


def recipe_to_flow(recipe, inputs=None, name=None, keep=False, log=print):
    """Flow for a validated recipe. inputs = {alias: value} already loaded (run_recipe does this for you).
    Every step is one Flow step, so .trace shows rows in / out and timings."""
    env = dict(inputs or {})
    flow = Flow(name or recipe.get("name") or "recipe", keep=keep, log=log)
    used = set()
    for i, spec in enumerate(recipe["steps"]):
        fn, frame = _fn(spec["fn"]), _takes_frame(_fn(spec["fn"]))
        params = {k: v for k, v in spec.items() if k not in _RESERVED}
        pick, alias = spec.get("pick"), spec.get("as") or "df"
        label = spec.get("name") or spec["fn"]
        if label in used:
            label = f"{label}_{i + 1}"
        used.add(label)

        def run_step(df=None, fn=fn, frame=frame, params=params, pick=pick, alias=alias):
            pos, kw = _split_args(fn, _resolve(params, {**env, "df": df}))
            result = fn(df, *pos, **kw) if frame else fn(*pos, **kw)
            if pick is not None:
                result = result[pick]
            if alias == "df":
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
        fn = _fn(spec["fn"])
        pos, kw = _split_args(fn, _resolve({k: v for k, v in spec.items() if k not in _RESERVED}, inputs))
        inputs[alias] = fn(*pos, **kw)
    flow = recipe_to_flow(recipe, inputs, keep=keep, log=log)
    out = flow.run(df)
    return out, flow.trace


def _py(value):
    """Python source for a parameter value; $name references become plain names."""
    if isinstance(value, str) and value.startswith("$") and value[1:]:
        return value[1:]
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py(k)}: {_py(v)}" for k, v in value.items()) + "}"
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
    given. The script needs only `from toolbank import *`, so it runs and can be edited without the recipe."""
    problems = validate_recipe(recipe)
    first_frame = False
    if problems:                                          # maybe valid with an input frame
        first_frame = not validate_recipe(recipe, with_input=True)
        if not first_frame:
            raise ValueError("bad recipe: " + "; ".join(problems))
    name = re.sub(r"\W+", "_", str(recipe.get("name") or "recipe")).strip("_") or "recipe"
    lines = [f'"""{name}: generated by toolbank.recipe. Plain Python: edit freely, or change the recipe and',
             'generate again."""', "import pandas as pd  # noqa: F401", "from toolbank import *  # noqa: F403",
             "", "", f"def run({'df' if first_frame else 'df=None'}):"]
    for alias, spec in (recipe.get("inputs") or {}).items():
        lines.append(f"    {alias} = {_call(spec, False)}")
    for i, spec in enumerate(recipe["steps"]):
        frame = _takes_frame(_fn(spec["fn"])) and (i > 0 or first_frame)
        target = spec.get("as") or "df"
        comment = f"  # {spec['name']}" if spec.get("name") else ""
        lines.append(f"    {target} = {_call(spec, frame)}{comment}")
    main = ["    import sys", "    out = run(read_any(sys.argv[1]))"] if first_frame else ["    out = run()"]
    lines += ["    return df", "", "", 'if __name__ == "__main__":', *main,
              '    print(f"{len(out):,} rows x {out.shape[1]} cols")', ""]
    code = "\n".join(lines)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(code, encoding="utf-8")
    return code
