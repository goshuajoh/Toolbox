"""SQL databases with SQLAlchemy (+ pyodbc for SQL Server, Azure SQL, Fabric Warehouse / SQL endpoint):
read, execute, write, upsert, safe IN lists, incremental loads with a watermark.
Values always go in params (:name), never into the SQL text with f-strings."""
import json
import os
import re
import struct
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from .robust import BusinessError, RobotError, SystemError_


def _odbc_value(v):
    v = str(v)
    return "{" + v.replace("}", "}}") + "}" if re.search(r"[;{}=]|^\s|\s$", v) else v


def odbc_conn_str(server, database, driver="ODBC Driver 18 for SQL Server", trusted=True, user=None, password=None,
                  encrypt=True, trust_cert=False, authentication=None, extra=None):
    """pyodbc connection string. trusted=True = your Windows login. authentication='ActiveDirectoryIntegrated'
    or 'ActiveDirectoryInteractive' for Entra ID (Azure SQL / Fabric); authentication='token' leaves login out
    for make_engine(url, token=...)."""
    parts = {"DRIVER": "{" + driver + "}", "SERVER": _odbc_value(server), "DATABASE": _odbc_value(database),
             "Encrypt": "yes" if encrypt else "no", "TrustServerCertificate": "yes" if trust_cert else "no"}
    if authentication == "token":
        pass                                          # the access token is the login
    elif authentication:
        parts["Authentication"] = authentication
        if user:
            parts["UID"] = _odbc_value(user)
    elif trusted:
        parts["Trusted_Connection"] = "yes"
    elif user:
        parts["UID"] = _odbc_value(user)
        if password is not None:
            parts["PWD"] = _odbc_value(password)
    parts.update({k: _odbc_value(v) for k, v in (extra or {}).items()})
    return ";".join(f"{k}={v}" for k, v in parts.items()) + ";"


def mssql_url(server, database, **odbc_kw):
    """SQLAlchemy URL for SQL Server via pyodbc (odbc_kw as odbc_conn_str)."""
    from sqlalchemy.engine import URL
    return URL.create("mssql+pyodbc", query={"odbc_connect": odbc_conn_str(server, database, **odbc_kw)})


def fabric_sql_url(endpoint, database, authentication="ActiveDirectoryInteractive", user=None):
    """Fabric Warehouse / Lakehouse SQL analytics endpoint (copy the 'SQL connection string' host from Fabric).
    The Lakehouse endpoint is read-only; write through a Warehouse or Delta (lake.py)."""
    return mssql_url(f"{endpoint},1433", database, authentication=authentication, user=user, trusted=False)


def access_token_attrs(token):
    """pyodbc attrs_before for an Entra ID access token (SQL_COPT_SS_ACCESS_TOKEN)."""
    raw = token.encode("utf-16-le")
    return {1256: struct.pack(f"<I{len(raw)}s", len(raw), raw)}


def make_engine(url, fast_executemany=True, token=None, **kw):
    """SQLAlchemy engine. url: 'sqlite:///x.db', 'postgresql://...' or mssql_url(...). SQL Server gets
    fast_executemany (bulk inserts much faster). token = string or callable (e.g. TokenCache.get) giving an
    Entra ID token for https://database.windows.net/.default; refreshed on every new connection."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.engine import make_url
    u = make_url(url) if isinstance(url, str) else url
    kw.setdefault("pool_pre_ping", True)
    if u.get_backend_name() == "mssql":
        kw.setdefault("fast_executemany", fast_executemany)
    if token is not None and u.get_backend_name() != "mssql":
        raise ValueError("token= is for SQL Server / Azure SQL / Fabric (mssql+pyodbc) URLs")
    if token is not None and re.search(r"(?i)trusted_connection|authentication=|\buid=|\bpwd=",
                                       str(u.query.get("odbc_connect", ""))):
        raise ValueError("with token= build the URL with authentication='token' (no other login settings)")
    engine = create_engine(u, **kw)
    if token is not None:
        @event.listens_for(engine, "do_connect")
        def _token(dialect, conn_rec, cargs, cparams):
            cparams["attrs_before"] = access_token_attrs(token() if callable(token) else token)
    return engine


_AUTH = re.compile(r"login failed|28000|password|authenticat|access denied|permission denied|not authori", re.I)
_DRIVER = re.compile(r"IM002|data source name not found|can't open lib|driver", re.I)
_TRANSIENT = re.compile(r"timeout|timed out|database is locked|deadlock|connection|communication link|network|"
                        r"tcp provider|could not connect|server has gone away|unavailable|08001|08S01|40001|\b1205\b",
                        re.I)


def _db_error(e, what):
    """Classify a database error: constraint -> BusinessError; login / driver problems -> RobotError (retrying
    can lock the login); connection / lock / deadlock -> SystemError_ (retry); anything else (bad SQL, missing
    table) -> RobotError for the developer."""
    from sqlalchemy import exc
    if isinstance(e, pd.errors.DatabaseError) and e.__cause__ is not None:   # pandas 3 wraps SQLAlchemy errors
        e = e.__cause__
    msg = str(getattr(e, "orig", None) or e)[:300]
    if isinstance(e, exc.IntegrityError):
        return BusinessError(f"{what}: constraint violated ({msg})", code="DB_INTEGRITY")
    if _AUTH.search(msg):
        return RobotError(f"{what}: database login refused ({msg})", code="AUTH")
    if _DRIVER.search(msg):
        return RobotError(f"{what}: ODBC driver problem ({msg})", code="DB_DRIVER")
    if isinstance(e, exc.TimeoutError) or (isinstance(e, (exc.OperationalError, exc.InterfaceError))
                                           and _TRANSIENT.search(msg)):
        return SystemError_(f"{what}: database unavailable ({msg})", code="DB_CONNECTION")
    return RobotError(f"{what}: {msg}", code="DB_ERROR")


def _plain(v):
    """numpy / pandas scalars -> plain Python for drivers (SQLite stores numpy ints as BLOBs, pyodbc rejects them)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def _params(params):
    if params is None:
        return {}
    if isinstance(params, dict):
        return {k: _plain(v) for k, v in params.items()}
    return [{k: _plain(v) for k, v in p.items()} for p in params]


@contextmanager
def _db_errors(what):
    from sqlalchemy import exc
    try:
        yield
    except (exc.SQLAlchemyError, pd.errors.DatabaseError) as e:
        raise _db_error(e, what) from e


def sql_read(engine, sql, params=None, dtype=None, parse_dates=None):
    """SELECT -> DataFrame: sql_read(e, "select * from trades where desk = :desk", {"desk": "FX"})."""
    from sqlalchemy import text
    with _db_errors("sql_read"), engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=_params(params), dtype=dtype, parse_dates=parse_dates)


def sql_read_chunks(engine, sql, params=None, chunksize=50_000):
    """Large SELECT as a generator of DataFrames."""
    from sqlalchemy import text
    with _db_errors("sql_read_chunks"), engine.connect() as conn:
        yield from pd.read_sql(text(sql), conn, params=_params(params), chunksize=chunksize)


def sql_exec(engine, sql, params=None, dry_run=False):
    """INSERT/UPDATE/DELETE/DDL in one transaction (commit, or roll back on error). params: dict, or a list of
    dicts to run once per row. Returns rows affected (driver-dependent for lists)."""
    from sqlalchemy import text
    if dry_run:
        print(f"[dry-run] would execute: {sql.strip()[:120]}")
        return 0
    if isinstance(params, (list, tuple)) and not params:
        return 0                                       # nothing to run (an empty list would run it once)
    with _db_errors("sql_exec"), engine.begin() as conn:
        return conn.execute(text(sql), _params(params)).rowcount


def sql_write(df, table, engine, schema=None, if_exists="append", chunksize=1000, dtype=None, dry_run=False):
    """DataFrame -> table (if_exists: append | replace | fail) in one transaction. Returns rows written."""
    if dry_run:
        print(f"[dry-run] would write {len(df)} rows to {table}")
        return 0
    with _db_errors(f"sql_write {table}"), engine.begin() as conn:
        df.to_sql(table, conn, schema=schema, if_exists=if_exists, index=False, chunksize=chunksize, dtype=dtype)
    return len(df)


def sql_upsert(df, table, keys, engine, schema=None, dry_run=False):
    """Update rows that match on keys, insert the rest, in one transaction. A staging table copies the target's
    column types (unicode text, datetime2 kept) and is dropped again, so it needs CREATE TABLE rights.
    SQL Server -> MERGE WITH (HOLDLOCK); SQLite / PostgreSQL -> ON CONFLICT (needs a unique index on keys)."""
    from sqlalchemy import text
    keys = [keys] if isinstance(keys, str) else list(keys)
    cols = list(df.columns)
    if any(k not in cols for k in keys):
        raise ValueError(f"keys {keys} must be columns of the data")
    if df.duplicated(keys).any():
        raise BusinessError(f"duplicate keys in upsert data for {table}", code="DUPLICATE_KEYS")
    if df[keys].isna().any().any():
        raise BusinessError(f"blank key values in upsert data for {table}", code="BLANK_KEYS")
    if dry_run:
        print(f"[dry-run] would upsert {len(df)} rows into {table} on {keys}")
        return 0
    staging = f"stg_{table}_{uuid.uuid4().hex[:8]}"
    with _db_errors(f"sql_upsert {table}"), engine.begin() as conn:
        q = conn.dialect.identifier_preparer.quote
        pre = f"{q(schema)}." if schema else ""
        full, stg = pre + q(table), pre + q(staging)
        c, upd = ", ".join(q(x) for x in cols), [x for x in cols if x not in keys]
        if conn.dialect.name == "mssql":   # UNION ALL stops SELECT INTO copying an IDENTITY property
            conn.execute(text(f"SELECT TOP 0 {c} INTO {stg} FROM {full} UNION ALL SELECT TOP 0 {c} FROM {full}"))
        else:
            conn.execute(text(f"CREATE TABLE {stg} AS SELECT {c} FROM {full} WHERE 1 = 0"))
        df.to_sql(staging, conn, schema=schema, index=False, if_exists="append")
        if conn.dialect.name == "mssql":
            sql = (f"MERGE INTO {full} WITH (HOLDLOCK) AS t USING {stg} AS s ON "
                   + " AND ".join(f"t.{q(k)} = s.{q(k)}" for k in keys))
            if upd:
                sql += " WHEN MATCHED THEN UPDATE SET " + ", ".join(f"t.{q(x)} = s.{q(x)}" for x in upd)
            sql += f" WHEN NOT MATCHED THEN INSERT ({c}) VALUES (" + ", ".join(f"s.{q(x)}" for x in cols) + ");"
        elif conn.dialect.name in ("sqlite", "postgresql"):
            action = "DO UPDATE SET " + ", ".join(f"{q(x)} = excluded.{q(x)}" for x in upd) if upd else "DO NOTHING"
            sql = (f"INSERT INTO {full} ({c}) SELECT {c} FROM {stg} WHERE true "
                   f"ON CONFLICT ({', '.join(q(k) for k in keys)}) {action}")
        else:
            raise ValueError(f"sql_upsert does not support {conn.dialect.name}")
        conn.execute(text(sql))
        conn.execute(text(f"DROP TABLE {stg}"))
    return len(df)


def in_clause(name, values):
    """Safe IN list: in_clause('id', [1, 2]) -> (':id_0, :id_1', {'id_0': 1, 'id_1': 2}); then
    sql_read(e, f"select * from t where id in ({frag})", params)."""
    values = list(dict.fromkeys(_plain(v) for v in values))
    if not values:
        raise ValueError("IN list is empty")
    if len(values) > 2000:
        raise ValueError("over 2000 values (SQL Server allows 2100 parameters): load them into a table and join")
    params = {f"{name}_{i}": v for i, v in enumerate(values)}
    return ", ".join(f":{k}" for k in params), params


def sql_tables(engine, schema=None):
    """Table names in a database/schema."""
    from sqlalchemy import inspect
    return inspect(engine).get_table_names(schema=schema)


def sql_columns(engine, table, schema=None):
    """Columns of a table -> DataFrame(name, type, nullable)."""
    from sqlalchemy import inspect
    cols = inspect(engine).get_columns(table, schema=schema)
    return pd.DataFrame([{"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable")} for c in cols])


# ---------- incremental loads ----------

def watermark_get(path, key, default=None):
    """Last value loaded (timestamp / id) for an incremental load, from a small JSON file."""
    p = Path(path)
    return (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}).get(key, default)


def watermark_set(path, key, value):
    """Save the new watermark — call it only after the load fully succeeded. Atomic write."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if isinstance(value, np.number):
        value = value.item()
    elif value is not None and not isinstance(value, (bool, int, float, str)):
        value = str(value)                                   # dates / timestamps
    data[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


def read_since(engine, table, column, since=None, schema=None, columns="*"):
    """Rows where column > since (None = all), ordered by column -> (df, new_watermark as plain int/str/date).
    Date-time text watermarks are sent as datetimes (SQL Server DATETIME rejects 6-digit fractions as text)."""
    q = engine.dialect.identifier_preparer.quote
    if columns != "*" and column not in columns:
        columns = list(columns) + [column]
    cols = columns if columns == "*" else ", ".join(q(c) for c in columns)
    sql = f"SELECT {cols} FROM {(q(schema) + '.') if schema else ''}{q(table)}"
    params = {}
    if since is not None:
        if isinstance(since, str) and engine.dialect.name != "sqlite" and re.match(r"\d{4}-\d{2}-\d{2}", since):
            since = pd.Timestamp(since).to_pydatetime()
        sql += f" WHERE {q(column)} > :since"
        params["since"] = since
    df = sql_read(engine, sql + f" ORDER BY {q(column)}", params)
    return df, (_plain(df[column].max()) if len(df) else since)
