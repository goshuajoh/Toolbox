"""Microsoft Fabric / OneLake and Delta Lake tables (pip install deltalake pyarrow; azure-identity for OneLake).
A local folder works the same as abfss://, so code runs on your PC first, then against OneLake.
Tip: OneLake file explorer syncs Lakehouse Files to a local folder that read_any can read directly."""
import re

import pandas as pd

ONELAKE = "onelake.dfs.fabric.microsoft.com"
STORAGE_SCOPE = "https://storage.azure.com/.default"
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def onelake_uri(workspace, item, path="", item_type="Lakehouse"):
    """abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<item>.Lakehouse/<path>. Names or GUIDs (use GUIDs
    when names contain spaces). path: 'Tables/trades' (a Delta table) or 'Files/inbound'."""
    part = item if ("." in item or _GUID.match(item)) else f"{item}.{item_type}"
    return f"abfss://{workspace}@{ONELAKE}/{part}/{path.lstrip('/')}".rstrip("/")


def azure_token(scope=STORAGE_SCOPE, interactive=False):
    """Entra ID access token with azure-identity (Windows SSO / Azure CLI / VS Code sign-in; interactive=True
    opens a browser sign-in)."""
    from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
    cred = InteractiveBrowserCredential() if interactive else \
        DefaultAzureCredential(exclude_interactive_browser_credential=False)
    return cred.get_token(scope).token


def onelake_options(token=None):
    """storage_options for deltalake on OneLake."""
    return {"bearer_token": token or azure_token(), "use_fabric_endpoint": "true"}


def _opts(path, storage_options):
    if storage_options is None and ONELAKE in str(path):
        return onelake_options()
    return storage_options


def delta_exists(path, storage_options=None):
    """True if path holds a Delta table."""
    from deltalake import DeltaTable
    return DeltaTable.is_deltatable(str(path), storage_options=_opts(path, storage_options))


def read_delta(path, columns=None, version=None, filters=None, storage_options=None):
    """Delta table -> DataFrame. version= reads an older snapshot (audit / time travel);
    filters=[('desk', '=', 'FX')] reads only matching files."""
    from deltalake import DeltaTable
    dt = DeltaTable(str(path), version=version, storage_options=_opts(path, storage_options))
    return dt.to_pandas(columns=columns, filters=filters)


def write_delta(df, path, mode="append", partition_by=None, schema_mode=None, storage_options=None, dry_run=False):
    """DataFrame -> Delta table. mode: append | overwrite | error | ignore; schema_mode='merge' adds new columns."""
    from deltalake import write_deltalake
    if dry_run:
        print(f"[dry-run] would {mode} {len(df)} rows to {path}")
        return 0
    write_deltalake(str(path), df, mode=mode, partition_by=partition_by, schema_mode=schema_mode,
                    storage_options=_opts(path, storage_options))
    return len(df)


def delta_merge(df, path, keys, storage_options=None, delete_missing=False):
    """Upsert into a Delta table: update rows matching on keys, insert new ones (delete_missing=True also
    deletes target rows absent from df). Duplicate or blank keys are refused. Returns the merge metrics dict."""
    import pyarrow as pa
    from deltalake import DeltaTable
    from .robust import BusinessError
    keys = [keys] if isinstance(keys, str) else list(keys)
    if df.duplicated(keys).any():
        raise BusinessError(f"duplicate keys {keys} in merge data", code="DUPLICATE_KEYS")
    if df[keys].isna().any().any():
        raise BusinessError("blank key values in merge data (they never match, so rows multiply)", code="BLANK_KEYS")
    if delete_missing and df.empty:
        raise ValueError("delete_missing with an empty extract would empty the table: check the input")
    dt = DeltaTable(str(path), storage_options=_opts(path, storage_options))
    pred = " AND ".join(f't."{k}" = s."{k}"' for k in keys)
    m = dt.merge(source=pa.Table.from_pandas(df, preserve_index=False), predicate=pred, source_alias="s",
                 target_alias="t").when_matched_update_all().when_not_matched_insert_all()
    if delete_missing:
        m = m.when_not_matched_by_source_delete()
    return m.execute()


def delta_history(path, limit=None, storage_options=None):
    """Versions of a Delta table (who/when/what operation) -> DataFrame, newest first."""
    from deltalake import DeltaTable
    return pd.DataFrame(DeltaTable(str(path), storage_options=_opts(path, storage_options)).history(limit))


def delta_vacuum(path, retention_hours=168, storage_options=None, dry_run=True):
    """Files older than retention that vacuum would delete (dry_run=True) or deletes them."""
    from deltalake import DeltaTable
    dt = DeltaTable(str(path), storage_options=_opts(path, storage_options))
    return dt.vacuum(retention_hours=retention_hours, dry_run=dry_run, enforce_retention_duration=True)
