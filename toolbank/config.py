"""Config from YAML (default + environment overlay + env vars) and secrets from env / Windows Credential Manager."""
import os
from pathlib import Path


def load_config(config_dir="config", env=None, env_prefix="BOT_"):
    """default.yaml, then <env>.yaml on top, then BOT_<KEY> environment variables on top. Returns dict."""
    import yaml
    env = env or os.environ.get(f"{env_prefix}ENV", "dev")
    cfg = {}
    for name in ("default.yaml", f"{env}.yaml"):
        p = Path(config_dir) / name
        if p.exists():
            cfg.update(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    for k, v in os.environ.items():
        if k.startswith(env_prefix) and k != f"{env_prefix}ENV":
            cfg[k[len(env_prefix):].lower()] = v
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
