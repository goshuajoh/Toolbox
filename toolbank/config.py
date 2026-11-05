"""Config from YAML (default + environment overlay + env vars) and secrets from env / Windows Credential Manager.
YAML is read with yaml_load: no YAML 1.1 surprises (00123 and 01 stay text, NO / on / yes stay text)."""
import os
import re
from pathlib import Path


def _loader():
    """yaml.SafeLoader with YAML 1.2 core-schema numbers and booleans: only true / false are booleans
    (not yes / no / on / off, so 'country: NO' is Norway) and a leading zero is not octal ('00123' stays text,
    as account and branch codes must)."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass
    drop = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:bool")
    Loader.yaml_implicit_resolvers = {ch: [(t, r) for t, r in rs if t not in drop]
                                      for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}
    Loader.add_implicit_resolver("tag:yaml.org,2002:int",
                                 re.compile(r"^(?:[-+]?(?:0|[1-9][0-9_]*)|0x[0-9a-fA-F_]+|0o[0-7_]+)$"),
                                 list("-+0123456789"))
    Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                                 list("tTfF"))
    return Loader


def yaml_load(text):
    """YAML text -> Python, like yaml.safe_load but with YAML 1.2 numbers and booleans: '00123' and '01' stay
    text, NO / on / yes stay text, only true / false are booleans. Use it for every config and recipe file."""
    import yaml
    return yaml.load(text, Loader=_loader())


def _merge(base, over):
    """Deep merge: nested dicts are merged key by key, anything else in `over` replaces `base`."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def _typed(text):
    """Environment variable text -> the type YAML would give it, without YAML surprises: '3' -> 3, '0.5' -> 0.5,
    'true' / 'false' -> bool, 'null' -> None; '0123', 'NO', 'on' and anything else stay (trimmed) text."""
    s = text.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?(0|[1-9]\d*)", s):
        return int(s)
    if re.fullmatch(r"-?(0|[1-9]\d*)\.\d+([eE][-+]?\d+)?", s):
        return float(s)
    return s


def load_config(config_dir="config", env=None, env_prefix="BOT_"):
    """default.yaml, then <env>.yaml merged on top (nested sections key by key), then BOT_<KEY> environment
    variables on top: BOT_RETRIES=3 -> cfg['retries'] = 3 (typed like YAML), BOT_DB__HOST=x -> cfg['db']['host'].
    Returns dict (cfg['env'] = the environment name)."""
    env = env or os.environ.get(f"{env_prefix}ENV", "dev")
    cfg = {}
    for name in ("default.yaml", f"{env}.yaml"):
        p = Path(config_dir) / name
        if p.exists():
            loaded = yaml_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{p} must hold a mapping (key: value), not {type(loaded).__name__}")
            cfg = _merge(cfg, loaded)
    for k, v in os.environ.items():
        if k.startswith(env_prefix) and k != f"{env_prefix}ENV":
            path = [part.lower() for part in k[len(env_prefix):].split("__") if part]
            if not path:
                continue
            over = _typed(v)
            for part in reversed(path):
                over = {part: over}
            cfg = _merge(cfg, over)
    cfg["env"] = env
    return cfg


def require(cfg, *keys):
    """Raise a clear error if config keys are missing."""
    missing = [k for k in keys if cfg.get(k) in (None, "")]
    if missing:
        raise KeyError(f"missing config keys: {missing}")
    return cfg


def get_secret(name, service="robot"):
    """Secret from env var, else Windows Credential Manager / OS keyring (pip install keyring). Never hard-code."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        import keyring
        v = keyring.get_password(service, name)
    except ImportError:
        v = None
    if not v:
        raise KeyError(f"secret {name} not found in environment or keyring service '{service}'")
    return v
