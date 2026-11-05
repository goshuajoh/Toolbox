"""Config from YAML (default + environment overlay + env vars) and secrets from env / Windows Credential Manager."""
import os
import re
from pathlib import Path


def _merge(base, over):
    """Deep merge: nested dicts are merged key by key, anything else in `over` replaces `base`."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def _typed(text):
    """Environment variable text -> the type YAML would give it, without YAML surprises: '3' -> 3, '0.5' -> 0.5,
    'true' -> True, 'null' -> None; '0123' and anything else stay text (account numbers keep their zeros)."""
    s = text.strip()
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?(0|[1-9]\d*)", s):
        return int(s)
    if re.fullmatch(r"-?(0|[1-9]\d*)\.\d+([eE][-+]?\d+)?", s):
        return float(s)
    return text


def load_config(config_dir="config", env=None, env_prefix="BOT_"):
    """default.yaml, then <env>.yaml merged on top (nested sections key by key), then BOT_<KEY> environment
    variables on top: BOT_RETRIES=3 -> cfg['retries'] = 3 (typed like YAML), BOT_DB__HOST=x -> cfg['db']['host'].
    Returns dict (cfg['env'] = the environment name)."""
    import yaml
    env = env or os.environ.get(f"{env_prefix}ENV", "dev")
    cfg = {}
    for name in ("default.yaml", f"{env}.yaml"):
        p = Path(config_dir) / name
        if p.exists():
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{p} must hold a mapping (key: value), not {type(loaded).__name__}")
            cfg = _merge(cfg, loaded)
    for k, v in os.environ.items():
        if k.startswith(env_prefix) and k != f"{env_prefix}ENV":
            path = [part.lower() for part in k[len(env_prefix):].split("__") if part]
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
