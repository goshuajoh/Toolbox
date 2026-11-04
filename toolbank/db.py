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
    or 'ActiveDirectoryInteractive' for Entra ID (Azure SQL / Fabric)."""
    parts = {"DRIVER": "{" + driver + "}", "SERVER": _odbc_value(server), "DATABASE": _odbc_value(database),
             "Encrypt": "yes" if encrypt else "no", "TrustServerCertificate": "yes" if trust_cert else "no"}
    if authentication:
        parts["Authentication"] = authentication
        if user:
            parts["UID"] = _odbc_value(user)
    elif trusted:
        parts["Trusted_Connection"] = "yes"
    else:
        parts["UID"], parts["PWD"] = _odbc_value(user), _odbc_value(password)
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
    engine = create_engine(u, **kw)
    if token is not None:
        @event.listens_for(engine, "do_connect")
        def _token(dialect, conn_rec, cargs, cparams):
            cparams["attrs_before"] = access_token_attrs(token() if callable(token) else token)
    return engine


_TRANSIENT = re.compile(r"timeout|timed out|locked|deadlock|connection|communication link|network|tcp provider|"
                        r"could not connect|server has gone away|unavailable|login failed|08001|08S01|40001", re.I)


def _db_error(e, what):
    """Classify a database error: constraint -> BusinessError, connection/lock/deadlock -> SystemError_ (retry),
    anything else (bad SQL, missing table) -> RobotError for the developer."""
    from sqlalchemy import exc
    if isinstance(e, pd.errors.DatabaseError) and e.__cause__ is not None:   # pandas 3 wraps SQLAlchemy errors
        e = e.__cause__
    msg = str(getattr(e, "orig", None) or e)[:300]
    if isinstance(e, exc.IntegrityError):
        return BusinessError(f"{what}: constraint violated ({msg})", code="DB_INTEGRITY")
    if isinstance(e, (exc.TimeoutError, exc.InterfaceError)) or \
            (isinstance(e, exc.OperationalError) and _TRANSIENT.search(msg)):
        return SystemError_(f"{what}: database unavailable ({msg})", code="DB_CONNECTION")
    return RobotError(f"{what}: {msg}", code="DB_ERROR")


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
        return pd.read_sql(text(sql), conn, params=params or {}, dtype=dtype, parse_dates=parse_dates)


def sql_read_chunks(engine, sql, params=None, chunksize=50_000):
    """Large SELECT as a generator of DataFrames."""
    from sqlalchemy import text
    with _db_errors("sql_read_chunks"), engine.connect() as conn:
        yield from pd.read_sql(text(sql), conn, params=params or {}, chunksize=chunksize)


def sql_exec(engine, sql, params=None, dry_run=False):
    """INSERT/UPDATE/DELETE/DDL in one transaction (commit, or roll back on error). params: dict, or a list of
    dicts to run once per row. Returns rows affected (driver-dependent for lists)."""
    from sqlalchemy import text
    if dry_run:
        print(f"[dry-run] would execute: {sql.strip()[:120]}")
        return 0
    with _db_errors("sql_exec"), engine.begin() as conn:
        return conn.execute(text(sql), params or {}).rowcount


def sql_write(df, table, engine, schema=None, if_exists="append", chunksize=1000, dtype=None, dry_run=False):
    """DataFrame -> table (if_exists: append | replace | fail) in one transaction. Returns rows written."""
    if dry_run:
        print(f"[dry-run] would write {len(df)} rows to {table}")
        return 0
    with _db_errors(f"sql_write {table}"), engine.begin() as conn:
        df.to_sql(table, conn, schema=schema, if_exists=if_exists, index=False, chunksize=chunksize, dtype=dtype)
    return len(df)


def sql_upsert(df, table, keys, engine, schema=None, dry_run=False):
    """Update rows that match on keys, insert the rest, in one transaction (via a temporary staging table, so
    it needs CREATE TABLE rights). SQL Server -> MERGE; SQLite / PostgreSQL -> ON CONFLICT (unique index on keys)."""
    from sqlalchemy import text
    keys = [keys] if isinstance(keys, str) else list(keys)
    cols = list(df.columns)
    if any(k not in cols for k in keys):
        raise ValueError(f"keys {keys} must be columns of the data")
    if df.duplicated(keys).any():
        raise BusinessError(f"duplicate keys in upsert data for {table}", code="DUPLICATE_KEYS")
    if dry_run:
        print(f"[dry-run] would upsert {len(df)} rows into {table} on {keys}")
        return 0
    staging = f"stg_{table}_{uuid.uuid4().hex[:8]}"
    with _db_errors(f"sql_upsert {table}"), engine.begin() as conn:
        q = conn.dialect.identifier_preparer.quote
        pre = f"{q(schema)}." if schema else ""
        full, stg = pre + q(table), pre + q(staging)
        df.to_sql(staging, conn, schema=schema, index=False)
        c, upd = ", ".join(q(x) for x in cols), [x for x in cols if x not in keys]
        if conn.dialect.name == "mssql":
            sql = f"MERGE INTO {full} AS t USING {stg} AS s ON " + " AND ".join(f"t.{q(k)} = s.{q(k)}" for k in keys)
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
    values = list(dict.fromkeys(values))
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
    """Rows where column > since (None = all), ordered by column -> (df, new_watermark)."""
    q = engine.dialect.identifier_preparer.quote
    cols = columns if columns == "*" else ", ".join(q(c) for c in columns)
    sql = f"SELECT {cols} FROM {(q(schema) + '.') if schema else ''}{q(table)}"
    params = {}
    if since is not None:
        sql += f" WHERE {q(column)} > :since"
        params["since"] = since
    df = sql_read(engine, sql + f" ORDER BY {q(column)}", params)
    return df, (df[column].max() if len(df) else since)
