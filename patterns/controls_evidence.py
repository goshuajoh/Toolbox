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
  - "A dry run (dry_run=True: pass the template's --dry-run) runs every check but writes the output only into the run folder, as <run_id>.<name>.dryrun, and never releases it. The live run for the same date then still runs."
  - "An output is released once. Just before the rename the run writes <name>.released (its run id) into the run folder. A re-run that finds that record or the output raises BusinessError ALREADY_RELEASED before any work (exit 2), also after the downstream job has taken the file away, and a person checks whether the target took the first file."
  - "run_folder is the run's own dated folder (the template's rd), never the downstream folder: the record, log, rejected rows, dry-run output, manifest and pack go there, where no pickup job takes them."
  - "add_audit_columns runs on the input as read: row_hash is the hash of the input row before any change. process() keeps it with run_id, processed_at and source on every output and rejected row, so each traces back to one input row."
  - "run_id = UTC time + 6 random hex characters (20261112T091500Z-a1b2c3), so two runs in the same second never share an id. It names the log, the manifest and the pack, and is on every output row."
governance:
  - "The manifest (JSON) holds run id, robot, version, the SHA-256 of the robot's code, user, host, start and end (UTC), each file's role (input, output, rejected), name, size and SHA-256, the totals, PASS or FAIL, and whether it was a dry run. It is written on a fail too."
  - "The version says what should have run; robot_sha256 (the file this code is in) proves what did. Compare it with the hash of the approved release."
  - "The evidence pack (manifest, output, rejected rows, log) holds no input file: the input's SHA-256 proves which file was used without a second copy of it. The output and rejected rows are customer data: keep the pack under the data retention rule, with the same access as the output."
  - "The error names the failed check (rows, sum, abs_sum or distinct_keys) and the lost refs. The totals stay in the manifest; amounts never reach the log."
  - "A FAIL goes to a person: they compare the .pending file (.dryrun in a dry run) with the input, the robot is fixed and run again. Nobody renames a .pending file by hand."
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


def write_manifest(path, run_id, status, files, figures, started, dry_run=False):
    """Evidence manifest: who, where, when, which code, which files (SHA-256), which totals, PASS or FAIL, dry run."""
    m = {"run_id": run_id, "robot": ROBOT, "version": ROBOT_VERSION, "robot_sha256": file_sha256(__file__),
         "status": status, "dry_run": dry_run, "user": getpass.getuser(), "host": socket.gethostname(),
         "started_utc": started, "finished_utc": utc_now(), "totals": figures,
         "files": [{"role": r, "name": p.name, "bytes": p.stat().st_size, "sha256": file_sha256(p)} for r, p in files]}
    path.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return path


def controlled_run(inp_path, process, out_path, run_folder, key="ref", amount="amount", dry_run=False):
    """Read, process, release the output only if input = output + rejected; manifest and evidence pack either way.
    process(df) -> (output, rejected), input columns kept. Returns the pack; RobotError CONTROL_MISMATCH on a fail.
    run_folder: the run's dated folder (the template's rd). dry_run=True (the template's --dry-run): every check
    runs, the output stays in run_folder, nothing is released."""
    inp_path, out_path, run_folder = Path(inp_path), Path(out_path), Path(run_folder)
    released = run_folder / f"{out_path.name}.released"   # stays when the downstream job takes the output away
    if out_path.exists() or released.exists():
        raise BusinessError("output already released by an earlier run", code="ALREADY_RELEASED", file=out_path.name)
    run_id, started = new_run_id(), utc_now()
    log_path, rej_path = run_folder / f"{run_id}.log.jsonl", run_folder / f"{run_id}.rejected.csv"
    log = get_logger(log_path, run_id=run_id, name=f"{ROBOT}.evidence", console=False)   # not the run's own logger
    inp = add_audit_columns(read_csv_smart(inp_path, required=[key, amount], dtype=str), run_id, source=inp_path.name)
    out, rejected = process(inp)
    pending = (run_folder / f"{run_id}.{out_path.name}.dryrun" if dry_run    # a dry run's output is never released
               else out_path.with_name(out_path.name + ".pending"))
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
        if not dry_run:
            with open(released, "x", encoding="utf-8") as fh:   # "x": fails if it exists, so only one run releases
                fh.write(run_id)                          # written first: a crash before the rename still stops a re-run
            os.replace(pending, out_path)                 # released only now
    status = "FAIL" if failed else "PASS"
    output = pending if failed or dry_run else out_path
    log.info("control", extra={"event": "control", "status": status, "rows": figures["input"]["rows"],
                               "count": figures["output"]["rows"], "dry_run": dry_run})
    manifest = write_manifest(run_folder / f"{run_id}.manifest.json", run_id, status,
                              [("input", inp_path), ("output", output), ("rejected", rej_path)], figures, started,
                              dry_run)
    pack = zip_files([manifest, output, rej_path, log_path], run_folder / f"{run_id}.evidence.zip")
    if failed:                                            # a lost row with a blank ref shows in the rows check only
        lost = sorted(set(inp[key].dropna()) - set(written[key].dropna()) - set(rej[key].dropna()))[:20]
        raise RobotError("control totals do not match: output not released", code="CONTROL_MISMATCH",
                         check=failed, lost_refs=lost)
    return pack


# --- demo ---


def demo(tmp):
    """4 payments in, 3 out and 1 rejected: a dry run checks everything and releases nothing; the live run for the same
    date then passes and the output is released with its evidence. A re-run is refused, also after the downstream job
    took the file. A step that drops a row, or rounds amounts, is stopped: the output stays .pending, FAIL."""
    import zipfile
    src, outbox, rd = tmp / "payments.csv", tmp / "outbox", tmp / "run"          # rd: the run's dated folder
    outbox.mkdir()
    rd.mkdir()
    pd.DataFrame({"ref": ["P1", "P2", "P3", "P4"], "account": ["123456789012", "223456789012", "323456789012",
                  "423456789012"], "amount": ["1,250.00", "(40.00)", "99.99", "12.30"]}).to_csv(src, index=False)

    def process(df):
        df = df.assign(amount=to_number(df["amount"]))
        ok = df["amount"] > 0                                       # a payment must be positive
        return df[ok], df[~ok]

    def manifest(pack):
        return json.loads(pack.with_name(pack.name.replace(".evidence.zip", ".manifest.json")).read_text())

    dry_pack = controlled_run(src, process, outbox / "upload.csv", rd, dry_run=True)
    md = manifest(dry_pack)
    assert md["status"] == "PASS" and md["dry_run"] is True and not list(outbox.iterdir())   # nothing released
    assert f"{md['run_id']}.upload.csv.dryrun" in zipfile.ZipFile(dry_pack).namelist()
    assert not (rd / "upload.csv.released").exists()

    pack = controlled_run(src, process, outbox / "upload.csv", rd)                  # the live run still runs
    m = manifest(pack)
    assert m["status"] == "PASS" and m["dry_run"] is False and m["totals"]["input"] == m["totals"]["output_plus_rejected"]
    assert m["totals"]["rejected"]["rows"] == 1 and m["totals"]["input"]["sum"] == 1322.29
    run_id, files = m["run_id"], {f["role"]: f["sha256"] for f in m["files"]}
    assert files == {"input": file_sha256(src), "output": file_sha256(outbox / "upload.csv"),
                     "rejected": file_sha256(rd / f"{run_id}.rejected.csv")}
    assert sorted(zipfile.ZipFile(pack).namelist()) == sorted(
        [f"{run_id}.manifest.json", "upload.csv", f"{run_id}.rejected.csv", f"{run_id}.log.jsonl"])
    upload = pd.read_csv(outbox / "upload.csv", dtype=str)
    assert upload["run_id"].eq(run_id).all() and upload["row_hash"].str.len().eq(16).all() and len(upload) == 3
    log_text = (rd / f"{run_id}.log.jsonl").read_text()
    assert '"status": "PASS"' in log_text and not any(v in log_text for v in ("P1", "1250.0", "123456789012"))
    for taken in (False, True):                           # the output still there, then taken by the downstream job
        if taken:
            (outbox / "upload.csv").unlink()
        try:
            controlled_run(src, process, outbox / "upload.csv", rd)
        except BusinessError as e:
            assert e.code == "ALREADY_RELEASED"
        else:
            raise AssertionError("expected ALREADY_RELEASED")
    assert (rd / "upload.csv.released").read_text() == run_id and not list(outbox.iterdir())   # nothing sent twice

    bugs = {"rows": (lambda o: o.iloc[:-1], ["P4"]),                 # the last payment never reaches the output
            "sum": (lambda o: o.assign(amount=o["amount"].round(0)), [])}   # amounts rounded to whole units
    for check, (bug, lost) in bugs.items():
        run, out = tmp / check, outbox / f"{check}.csv"
        run.mkdir()
        try:
            controlled_run(src, lambda df, bug=bug: (bug(process(df)[0]), process(df)[1]), out, run)
        except RobotError as e:
            assert e.code == "CONTROL_MISMATCH" and e.context == {"check": check, "lost_refs": lost}
        else:
            raise AssertionError(f"expected CONTROL_MISMATCH on {check}")
        assert not out.exists() and out.with_name(out.name + ".pending").exists()
        assert not (run / f"{out.name}.released").exists()
        assert json.loads(next(run.glob("*.manifest.json")).read_text())["status"] == "FAIL"
