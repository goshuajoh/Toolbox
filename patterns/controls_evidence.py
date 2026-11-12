"""
title: "Prove the robot did what it said: control totals and evidence"
order: 80
when: >-
  Any robot that turns an input file into an output another team or system relies on (a payment file, an upload, a
  booking list): the run must show that no row was lost or changed on the way, and keep the proof.
tags: [controls]
needs: []
blocks: [BusinessError, RobotError, add_audit_columns, assert_totals_match, control_totals, file_sha256, get_logger, read_csv_smart, to_csv_strict, to_number, zip_files]
decisions:
  - "The control is input = output + rejected, on the 4 figures of control_totals: rows and distinct refs exactly, sum and absolute sum to the cent (both rounded to 2 dp, tol 0.005). Float noise passes, one lost cent fails. An amount that does not parse counts as a row but adds 0 to the sums, so process() must reject it (the demo does)."
  - "abs_sum next to sum: a debit and a credit both cut by 10 leave the sum equal. distinct_keys next to rows: a lost row plus a doubled one leave the count equal."
  - "Output and rejected totals come from the files read back from disk, not the frames in memory: the control covers what the next system receives, the writer's rounding included."
  - "A mismatch raises RobotError CONTROL_MISMATCH (exit 5, never retried). Not BusinessError: the input was accepted, so a lost row is the robot's fault, not the data's. Not SystemError_: a re-run would lose the same row."
  - "The output is written as <name>.pending and renamed only when the control passes. On a fail it stays .pending, so no downstream job picks it up, and it is kept as evidence."
  - "A released output is never overwritten: a re-run that finds it raises BusinessError ALREADY_RELEASED before any work (exit 2), and a person checks whether the target took the first file."
  - "add_audit_columns runs on the input as read: row_hash is the hash of the input row before any change. process() keeps it with run_id, processed_at and source on every output and rejected row, so each traces back to one input row."
  - "run_id = UTC time + 6 random hex characters (20261112T091500Z-a1b2c3), so two runs in the same second never share an id. It names the log, the manifest and the pack, and is on every output row."
governance:
  - "The manifest (JSON) holds run id, robot, version, the SHA-256 of the robot's code, user, host, start and end (UTC), each file's role (input, output, rejected), name, size and SHA-256, the totals and PASS or FAIL. It is written on a fail too."
  - "The version says what should have run; robot_sha256 (the file this code is in) proves what did. Compare it with the hash of the approved release."
  - "The evidence pack (manifest, output, rejected rows, log) holds no input file: the input's SHA-256 proves which file was used without a second copy of it. The output and rejected rows are customer data: keep the pack under the data retention rule, with the same access as the output."
  - "The error names the failed check (rows, sum, abs_sum or distinct_keys) and the lost refs. The totals stay in the manifest; amounts never reach the log."
  - "A FAIL goes to a person: they compare the .pending file with the input, the robot is fixed and run again. Nobody renames a .pending file by hand."
"""
import getpass
import json
import os
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from toolbank import (BusinessError, RobotError, add_audit_columns, assert_totals_match, control_totals, file_sha256,
                      get_logger, read_csv_smart, to_csv_strict, to_number, zip_files)

ROBOT, ROBOT_VERSION = "my_robot", "1.0.0"               # bump the version with every approved change


def utc_now():
    """Now in UTC as ISO text."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id():
    """UTC time plus 6 random hex characters: unique even for two runs in the same second."""
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def totals(df, amount, key):
    """control_totals of a frame whose amounts may still be text ('1,250.00', '(40.00)')."""
    return control_totals(df.assign(**{amount: to_number(df[amount])}), amount, key)


def write_manifest(path, run_id, status, files, figures, started):
    """Evidence manifest: who, where, when, which code, which files (SHA-256), which totals, PASS or FAIL."""
    m = {"run_id": run_id, "robot": ROBOT, "version": ROBOT_VERSION, "robot_sha256": file_sha256(__file__),
         "status": status, "user": getpass.getuser(), "host": socket.gethostname(), "started_utc": started,
         "finished_utc": utc_now(), "totals": figures,
         "files": [{"role": r, "name": p.name, "bytes": p.stat().st_size, "sha256": file_sha256(p)} for r, p in files]}
    path.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return path


def controlled_run(inp_path, process, out_path, run_folder, key="ref", amount="amount"):
    """Read, process, release the output only if input = output + rejected; manifest and evidence pack either way.
    process(df) -> (output, rejected), input columns kept. Returns the pack; RobotError CONTROL_MISMATCH on a fail."""
    inp_path, out_path, run_folder = Path(inp_path), Path(out_path), Path(run_folder)
    if out_path.exists():
        raise BusinessError("output already released by an earlier run", code="ALREADY_RELEASED", file=out_path.name)
    run_id, started = new_run_id(), utc_now()
    log_path, rej_path = run_folder / f"{run_id}.log.jsonl", run_folder / f"{run_id}.rejected.csv"
    log = get_logger(log_path, run_id=run_id, name=f"{ROBOT}.evidence", console=False)   # not the run's own logger
    inp = add_audit_columns(read_csv_smart(inp_path, required=[key, amount], dtype=str), run_id, source=inp_path.name)
    out, rejected = process(inp)
    pending = out_path.with_name(out_path.name + ".pending")
    to_csv_strict(out, pending)
    to_csv_strict(rejected, rej_path)
    written, rej = read_csv_smart(pending, dtype=str), read_csv_smart(rej_path, dtype=str)   # the files, not the frames
    figures = {"input": totals(inp, amount, key), "output": totals(written, amount, key),
               "rejected": totals(rej, amount, key)}
    figures["output_plus_rejected"] = totals(pd.concat([written, rej]), amount, key)
    try:
        assert_totals_match(figures["input"], figures["output_plus_rejected"])
    except ValueError as e:
        failed = str(e).split(":")[0]                     # 'rows: 4 vs 3' -> 'rows'; the figures go to the manifest
    else:
        failed = None
        os.replace(pending, out_path)                     # released only now
    status, released = ("FAIL", pending) if failed else ("PASS", out_path)
    log.info("control", extra={"event": "control", "status": status, "rows": figures["input"]["rows"],
                               "count": figures["output"]["rows"]})
    manifest = write_manifest(run_folder / f"{run_id}.manifest.json", run_id, status,
                              [("input", inp_path), ("output", released), ("rejected", rej_path)], figures, started)
    pack = zip_files([manifest, released, rej_path, log_path], run_folder / f"{run_id}.evidence.zip")
    if failed:                                            # a lost row with a blank ref shows in the rows check only
        lost = sorted(set(inp[key].dropna()) - set(written[key].dropna()) - set(rej[key].dropna()))[:20]
        raise RobotError("control totals do not match: output not released", code="CONTROL_MISMATCH",
                         check=failed, lost_refs=lost)
    return pack


def demo(tmp):
    """4 payments in, 3 out and 1 rejected: the control passes and the output is released with its evidence.
    A step that drops a row, or rounds amounts, is stopped: the output stays .pending and the manifest says FAIL."""
    import zipfile
    src = tmp / "payments.csv"
    pd.DataFrame({"ref": ["P1", "P2", "P3", "P4"], "account": ["123456789012", "223456789012", "323456789012",
                  "423456789012"], "amount": ["1,250.00", "(40.00)", "99.99", "12.30"]}).to_csv(src, index=False)

    def process(df):
        df = df.assign(amount=to_number(df["amount"]))
        ok = df["amount"] > 0                                       # a payment must be positive
        return df[ok], df[~ok]

    pack = controlled_run(src, process, tmp / "upload.csv", tmp)
    m = json.loads(next(tmp.glob("*.manifest.json")).read_text())
    assert m["status"] == "PASS" and m["totals"]["input"] == m["totals"]["output_plus_rejected"]
    assert m["totals"]["rejected"]["rows"] == 1 and m["totals"]["input"]["sum"] == 1322.29
    run_id, files = m["run_id"], {f["role"]: f["sha256"] for f in m["files"]}
    assert files == {"input": file_sha256(src), "output": file_sha256(tmp / "upload.csv"),
                     "rejected": file_sha256(tmp / f"{run_id}.rejected.csv")}
    assert sorted(zipfile.ZipFile(pack).namelist()) == sorted(
        [f"{run_id}.manifest.json", "upload.csv", f"{run_id}.rejected.csv", f"{run_id}.log.jsonl"])
    upload = pd.read_csv(tmp / "upload.csv", dtype=str)
    assert upload["run_id"].eq(run_id).all() and upload["row_hash"].str.len().eq(16).all() and len(upload) == 3
    log_text = (tmp / f"{run_id}.log.jsonl").read_text()
    assert '"status": "PASS"' in log_text and not any(v in log_text for v in ("P1", "1250.0", "123456789012"))
    try:
        controlled_run(src, process, tmp / "upload.csv", tmp)
    except BusinessError as e:
        assert e.code == "ALREADY_RELEASED"
    else:
        raise AssertionError("expected ALREADY_RELEASED")

    bugs = {"rows": (lambda o: o.iloc[:-1], ["P4"]),                 # the last payment never reaches the output
            "sum": (lambda o: o.assign(amount=o["amount"].round(0)), [])}   # amounts rounded to whole units
    for check, (bug, lost) in bugs.items():
        run = tmp / check
        run.mkdir()
        try:
            controlled_run(src, lambda df, bug=bug: (bug(process(df)[0]), process(df)[1]), run / "upload.csv", run)
        except RobotError as e:
            assert e.code == "CONTROL_MISMATCH" and e.context == {"check": check, "lost_refs": lost}
        else:
            raise AssertionError(f"expected CONTROL_MISMATCH on {check}")
        assert not (run / "upload.csv").exists() and (run / "upload.csv.pending").exists()
        assert json.loads(next(run.glob("*.manifest.json")).read_text())["status"] == "FAIL"
