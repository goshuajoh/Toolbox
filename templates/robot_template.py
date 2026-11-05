r"""
Robot template: copy, rename, fill the three TODOs.
Run:  python my_robot.py --env dev [--dry-run] [--business-date 2026-03-06]
Exit: 0 ok · 2 business exceptions or bad input (send report to owner) · 3 system failure (re-run)
      4 config error · 5 robot error (needs a fix) · 6 already running (a second copy was started)
Schedule: schtasks_create("CoE\\my_robot", r'"C:\path\python.exe" "C:\bots\my_robot.py" --env prod', dry_run=False)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # so `import toolbank` works from templates/
import pandas as pd  # noqa: E402
from toolbank import *  # noqa: E402,F401,F403

ROBOT = "my_robot"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--business-date", default=None)
    ap.add_argument("--config-dir", default="config")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config_dir, args.env)
    except Exception as e:
        print("CONFIG ERROR:", e)
        return 4

    rd = run_dir(ROBOT, args.business_date, root=cfg.get("workdir", "work"))
    log = get_logger(rd / "run.log.jsonl")
    cp = Checkpoint(rd / "checkpoint.json")
    log.info("start", extra={"event": "start", "status": args.env})
    try:
        with single_instance(ROBOT, lock_dir=rd):      # scheduler fired twice -> second copy exits 6
            return run(args, cfg, rd, log, cp)
    except RobotError as e:
        if e.code != "ALREADY_RUNNING":
            raise
        log.warning(str(e), extra={"code": e.code})
        return 6


def run(args, cfg, rd, log, cp):
    try:
        # ---------- SETUP: read + validate inputs ----------
        with step("load", log.info):
            df = read_sheet(cfg.get("input_path", "data/input.xlsx"), header_row=1, required=["id"])   # TODO
            df = cleanse(df)

        # ---------- PROCESS: one item at a time ----------
        results = []

        def handle(row):
            # TODO: business rules -> raise BusinessError("why", code="SHORT_CODE")
            results.append({"id": row["id"], "status": "OK"})

        done, failures = process_all([r for _, r in df.iterrows()], handle, checkpoint=cp, key=lambda r: r["id"],
                                     log=log.warning)

        # ---------- CLEANUP: report ----------
        out = rd / f"{ROBOT}_report.xlsx"
        sheets = {"Summary": pd.DataFrame([{"metric": "rows", "value": len(df)},
                                           {"metric": "done", "value": len(done)},
                                           {"metric": "exceptions", "value": len(failures)}]),
                  "Results": pd.DataFrame(results), "Exceptions": failures}
        act(safe_write_excel, out, sheets, overwrite=True, dry_run=args.dry_run)   # TODO: add email/upload
        log.info("end", extra={"event": "end", "rows": len(df), "count": len(failures)})
        return 2 if len(failures) else 0
    except Exception as e:                       # classified: BusinessError 2, SystemError_ 3, anything else 5
        err, info = classify(e), error_info(e)
        log.error(str(err), extra={"code": err.code, "where": info["where"], "type": info["type"]}, exc_info=True)
        return 2 if isinstance(err, BusinessError) else 3 if err.retryable else 5


if __name__ == "__main__":
    sys.exit(main())
