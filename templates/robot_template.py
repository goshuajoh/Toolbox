r"""
Robot template: copy, rename, fill the three TODOs.
Run:  python my_robot.py --env dev [--dry-run] [--business-date 2026-03-06]
      --business-date is YYYY-MM-DD only, so a re-run always finds the same run folder and checkpoint.
      --dry-run sends nothing, writes my_robot_report_dryrun.xlsx and never marks the checkpoint, so the live run
      for the same date still does everything. config/<env>.yaml must exist (exit 4 otherwise).
Exit: 0 ok · 2 business exceptions or bad input (the owner works the report; if the input was rejected before the
      report exists, run.log.jsonl says why) · 3 system failure (re-run: the checkpoint resumes; read the code in
      run.log.jsonl first, see METHODOLOGY.md) · 4 config error · 5 robot error, including access and rules problems
      (route by the code in run.log.jsonl) · 6 already running (a second copy was started)
Schedule as the robot's service account, not your own (the access admin types its password into Task Scheduler):
      schtasks_create("CoE\\my_robot", r'"C:\path\python.exe" "C:\bots\my_robot.py" --env prod',
                      user=r"DOMAIN\svc_my_robot", interactive=False, dry_run=True)   # print, then run by the admin
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # so `import toolbank` works from templates/
import pandas as pd  # noqa: E402
from toolbank import *  # noqa: E402,F401,F403

ROBOT = "my_robot"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.environ.get("BOT_ENV", "dev"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--business-date", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    ap.add_argument("--config-dir", default="config")
    args = ap.parse_args(argv)

    try:
        if not (Path(args.config_dir) / f"{args.env}.yaml").exists():
            raise FileNotFoundError(f"{args.config_dir}/{args.env}.yaml not found (one config file per environment)")
        cfg = load_config(args.config_dir, args.env)
    except Exception as e:
        print("CONFIG ERROR:", e, file=sys.stderr)
        return 4

    try:                                               # an offline share must not end as a bare traceback (exit 1)
        bdate = args.business_date.isoformat() if args.business_date else None
        rd = run_dir(ROBOT, bdate, root=cfg.get("workdir", "work"))
        log = get_logger(rd / "run.log.jsonl")
        cp = Checkpoint(rd / "checkpoint.json")
    except OSError as e:
        print(f"RUN FOLDER ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    log.info("start", extra={"event": "start", "status": args.env, "dry_run": args.dry_run})
    try:
        with single_instance(ROBOT, lock_dir=rd):      # scheduler fired twice -> second copy exits 6
            return run(args, cfg, rd, log, None if args.dry_run else cp)
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
            # Screen steps: attempt(action, verify, log=log.warning, **RETRY_PROFILES["ui"]); sends go through
            # act(send, ..., dry_run=args.dry_run) and the idempotent_submit pattern. See patterns/ for tested blocks.
            results.append({"id": row["id"], "status": "OK"})

        done, failures = process_all([r for _, r in df.iterrows()], handle, checkpoint=cp, key=lambda r: r["id"],
                                     log=log.warning)

        # ---------- CLEANUP: report (a dry run writes it too, under its own name, for comparison) ----------
        out = rd / f"{ROBOT}_report{'_dryrun' if args.dry_run else ''}.xlsx"
        sheets = {"Summary": pd.DataFrame([{"metric": "rows", "value": len(df)},
                                           {"metric": "done", "value": len(done)},
                                           {"metric": "exceptions", "value": len(failures)},
                                           {"metric": "dry run", "value": str(args.dry_run)}]),
                  "Results": pd.DataFrame(results), "Exceptions": failures}
        safe_write_excel(out, sheets, overwrite=True)    # TODO: email / upload through act(..., dry_run=args.dry_run)
        log.info("end", extra={"event": "end", "rows": len(df), "count": len(failures)})
        return 3 if failures["retryable"].any() else 2 if len(failures) else 0   # retryable -> re-run resumes
    except Exception as e:                       # classified: BusinessError 2, SystemError_ 3, anything else 5
        err, info = classify(e), error_info(e)
        log.error(str(err), extra={"code": err.code, "where": info["where"], "type": info["type"]}, exc_info=True)
        return 2 if isinstance(err, BusinessError) else 3 if err.retryable else 5


if __name__ == "__main__":
    sys.exit(main())
