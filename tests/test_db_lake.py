import struct

import pandas as pd
import pytest

from toolbank import *


def test_connection_strings():
    s = odbc_conn_str("sqlprod01\\IB", "Ops")
    assert s == ("DRIVER={ODBC Driver 18 for SQL Server};SERVER=sqlprod01\\IB;DATABASE=Ops;Encrypt=yes;"
                 "TrustServerCertificate=no;Trusted_Connection=yes;")
    s = odbc_conn_str("srv", "db", trusted=False, user="u", password="p;w}d")
    assert "UID=u;PWD={p;w}}d};" in s
    s = odbc_conn_str("x.datawarehouse.fabric.microsoft.com,1433", "wh", authentication="ActiveDirectoryInteractive",
                      user="me@bank.test")
    assert "Authentication=ActiveDirectoryInteractive;UID=me@bank.test;" in s and "Trusted_Connection" not in s
    attrs = access_token_attrs("abc")
    assert list(attrs) == [1256] and struct.unpack("<I", attrs[1256][:4])[0] == 6
    pytest.importorskip("sqlalchemy")
    u = fabric_sql_url("x.datawarehouse.fabric.microsoft.com", "wh")
    assert u.drivername == "mssql+pyodbc" and "x.datawarehouse.fabric.microsoft.com,1433" in u.query["odbc_connect"]
    with pytest.raises(ValueError):
        make_engine("sqlite://", token="t")


def test_in_clause_and_watermarks(tmp_path):
    frag, params = in_clause("id", [3, 1, 3])
    assert frag == ":id_0, :id_1" and params == {"id_0": 3, "id_1": 1}
    with pytest.raises(ValueError):
        in_clause("id", [])
    with pytest.raises(ValueError):
        in_clause("id", range(2001))
    wm = tmp_path / "wm.json"
    assert watermark_get(wm, "trades") is None
    import numpy as np
    watermark_set(wm, "trades", np.int64(42))
    watermark_set(wm, "ts", pd.Timestamp("2026-03-06 10:00"))
    assert watermark_get(wm, "trades") == 42 and watermark_get(wm, "ts") == "2026-03-06 10:00:00"


def test_sqlite_read_write_upsert(tmp_path):
    pytest.importorskip("sqlalchemy")
    e = make_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    sql_exec(e, "CREATE TABLE trades (trade_id TEXT PRIMARY KEY, desk TEXT, amount REAL, seq INTEGER)")
    df = pd.DataFrame({"trade_id": ["T1", "T2", "T3"], "desk": ["FX", "RATES", "FX"], "amount": [100.0, 200.5, 50.0],
                       "seq": [1, 2, 3]})
    assert sql_write(df, "trades", e) == 3
    got = sql_read(e, "select * from trades where desk = :desk order by trade_id", {"desk": "FX"})
    assert got["trade_id"].tolist() == ["T1", "T3"]
    frag, params = in_clause("t", ["T1", "T2"])
    assert len(sql_read(e, f"select * from trades where trade_id in ({frag})", params)) == 2
    assert sql_exec(e, "update trades set amount = :a where trade_id = :t", {"a": 1.0, "t": "T1"}) == 1
    assert sql_exec(e, "update trades set desk = :d where trade_id = :t",
                    [{"d": "EM", "t": "T2"}, {"d": "EM", "t": "T3"}]) in (2, -1)
    assert sql_exec(e, "delete from trades", dry_run=True) == 0
    up = pd.DataFrame({"trade_id": ["T1", "T4"], "desk": ["FX", "FX"], "amount": [999.0, 4.0], "seq": [1, 4]})
    assert sql_upsert(up, "trades", "trade_id", e) == 2
    all_rows = sql_read(e, "select * from trades order by trade_id")
    assert all_rows["amount"].tolist() == [999.0, 200.5, 50.0, 4.0] and all_rows["desk"].tolist()[1:3] == ["EM", "EM"]
    assert [t for t in sql_tables(e) if t.startswith("stg_")] == []                     # staging dropped
    with pytest.raises(BusinessError):
        sql_upsert(pd.concat([up, up]), "trades", "trade_id", e)
    with pytest.raises(BusinessError) as err:
        sql_write(df.head(1), "trades", e)                                               # duplicate primary key
    assert err.value.code == "DB_INTEGRITY"
    with pytest.raises(RobotError) as err:
        sql_read(e, "select * from no_such_table")
    assert err.value.code == "DB_ERROR" and not isinstance(err.value, SystemError_)
    cols = sql_columns(e, "trades")
    assert cols["name"].tolist() == ["trade_id", "desk", "amount", "seq"]
    chunks = list(sql_read_chunks(e, "select * from trades order by seq", chunksize=3))
    assert [len(c) for c in chunks] == [3, 1]
    new, mark = read_since(e, "trades", "seq", since=2)
    assert new["trade_id"].tolist() == ["T3", "T4"] and mark == 4
    none, mark2 = read_since(e, "trades", "seq", since=4, columns=["trade_id"])
    assert none.empty and mark2 == 4 and list(none.columns) == ["trade_id", "seq"]   # watermark column added
    import numpy as np
    again, mark3 = read_since(e, "trades", "seq", since=np.int64(3))                  # numpy watermark works
    assert again["trade_id"].tolist() == ["T4"] and type(mark3) is int
    assert len(sql_read(e, "select * from trades where seq in (" + in_clause("s", np.array([1, 4]))[0] + ")",
                        in_clause("s", np.array([1, 4]))[1])) == 2
    assert sql_exec(e, "update trades set desk = :d where trade_id = :t", []) == 0
    e.dispose()


@pytest.fixture
def delta():
    pytest.importorskip("deltalake")
    pytest.importorskip("pyarrow")


def test_delta_round_trip_merge_history(tmp_path, delta):
    path = tmp_path / "Tables" / "trades"
    df = pd.DataFrame({"TradeId": ["T1", "T2"], "desk": ["FX", "RATES"], "amount": [1.5, 2.0]})
    assert not delta_exists(path)
    assert write_delta(df, path) == 2 and delta_exists(path)
    write_delta(pd.DataFrame({"TradeId": ["T3"], "desk": ["FX"], "amount": [3.0]}), path)
    back = read_delta(path).sort_values("TradeId").reset_index(drop=True)
    assert back["TradeId"].tolist() == ["T1", "T2", "T3"]
    assert len(read_delta(path, version=0)) == 2
    assert read_delta(path, columns=["TradeId"], filters=[("desk", "=", "FX")])["TradeId"].sort_values().tolist() == \
        ["T1", "T3"]
    m = delta_merge(pd.DataFrame({"TradeId": ["T1", "T9"], "desk": ["FX", "EM"], "amount": [10.0, 9.0]}), path,
                    "TradeId")
    assert m["num_target_rows_updated"] == 1 and m["num_target_rows_inserted"] == 1
    cur = read_delta(path).set_index("TradeId")["amount"].to_dict()
    assert cur == {"T1": 10.0, "T2": 2.0, "T3": 3.0, "T9": 9.0}
    delta_merge(pd.DataFrame({"TradeId": ["T1"], "desk": ["FX"], "amount": [11.0]}), path, ["TradeId"],
                delete_missing=True)
    assert read_delta(path)["TradeId"].tolist() == ["T1"]
    write_delta(pd.DataFrame({"TradeId": ["T5"], "desk": ["FX"], "amount": [5.0], "book": ["B1"]}), path,
                schema_mode="merge")
    assert "book" in read_delta(path).columns
    write_delta(df, path, mode="overwrite", schema_mode="overwrite")
    assert sorted(read_delta(path)["TradeId"]) == ["T1", "T2"]
    hist = delta_history(path)
    assert len(hist) == 6 and {"version", "operation"} <= set(hist.columns)
    assert isinstance(delta_vacuum(path, retention_hours=168), list)
    assert write_delta(df, path, dry_run=True) == 0


def test_delta_partitions_and_onelake_uri(tmp_path, delta):
    path = tmp_path / "part"
    df = pd.DataFrame({"desk": ["FX", "FX", "EM"], "v": [1, 2, 3]})
    write_delta(df, path, partition_by=["desk"])
    assert sorted(p.name for p in path.iterdir() if p.is_dir() and p.name.startswith("desk=")) == ["desk=EM", "desk=FX"]
    assert read_delta(path, filters=[("desk", "=", "EM")])["v"].tolist() == [3]
    assert onelake_uri("Ops", "Sales", "Tables/trades") == \
        "abfss://Ops@onelake.dfs.fabric.microsoft.com/Sales.Lakehouse/Tables/trades"
    g = "1a2b3c4d-0000-1111-2222-333344445555"
    assert onelake_uri(g, g, "Files/in/") == f"abfss://{g}@onelake.dfs.fabric.microsoft.com/{g}/Files/in"
    assert onelake_uri("Ops", "WH.Warehouse") == "abfss://Ops@onelake.dfs.fabric.microsoft.com/WH.Warehouse"


def test_review_fixes_db_strings_and_errors():
    s = odbc_conn_str("srv", "db", trusted=False)
    assert "UID" not in s and "PWD" not in s
    tok = odbc_conn_str("x.database.windows.net", "db", authentication="token")
    assert "Trusted_Connection" not in tok and "Authentication" not in tok and "UID" not in tok
    pytest.importorskip("sqlalchemy")
    with pytest.raises(ValueError):
        make_engine(mssql_url("srv", "db"), token="t")                  # Trusted_Connection + token
    from sqlalchemy import exc
    from toolbank.db import _db_error
    login = _db_error(exc.InterfaceError("x", {}, Exception("[28000] Login failed for user 'bot'")), "read")
    assert type(login) is RobotError and login.code == "AUTH"
    driver = _db_error(exc.InterfaceError("x", {}, Exception("[IM002] Data source name not found")), "read")
    assert type(driver) is RobotError and driver.code == "DB_DRIVER"
    link = _db_error(exc.OperationalError("x", {}, Exception("[08S01] Communication link failure")), "read")
    assert isinstance(link, SystemError_)
    missing = _db_error(exc.OperationalError("x", {}, Exception("no such table: t")), "read")
    assert type(missing) is RobotError and missing.code == "DB_ERROR"


def test_delta_merge_guards(tmp_path, delta):
    path = tmp_path / "t"
    write_delta(pd.DataFrame({"k": ["A"], "v": [1]}), path)
    with pytest.raises(BusinessError):
        delta_merge(pd.DataFrame({"k": ["B", "B"], "v": [1, 2]}), path, "k")
    with pytest.raises(BusinessError):
        delta_merge(pd.DataFrame({"k": [None], "v": [1]}), path, "k")
    with pytest.raises(ValueError):
        delta_merge(pd.DataFrame({"k": [], "v": []}), path, "k", delete_missing=True)
    assert read_delta(path)["k"].tolist() == ["A"]
