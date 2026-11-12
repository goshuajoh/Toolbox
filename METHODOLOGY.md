# Delivery method for governed automation

How to take a repetitive operations process from the first stakeholder call to a robot that runs in production,
passes audit, and fails safely. There are seven stages. Each stage ends with a **gate**: a named person signs a named
artefact before the next stage starts. The toolbox supplies the building blocks for every design decision, so robots
look and fail the same way.

This page is a method, not your bank's policy. Where your bank has its own standard (technology risk, change
management, data classification, end-user computing, outsourcing), that standard wins. Use the artefacts here as the
evidence it asks for.

## Principles

1. **Deterministic.** The same input gives the same output. Every decision the robot makes is a written rule with an
   owner. No hidden judgement in code.
2. **Fail loud, never silently wrong.** A value the robot cannot read becomes blank and an exception. It is never
   guessed.
3. **Every item ends in exactly one state:** done, exception with a reason, or retry later. Nothing disappears.
4. **Nothing irreversible happens twice or without four eyes.** Submits, payments and emails are checked before every
   retry. Money movements and customer-facing actions go through maker-checker.
5. **Least privilege.** The robot uses a service account with only the access the process needs. Secrets live in a
   vault, never in code or config.
6. **Evidence by default.** Every run keeps what came in, what went out, control totals, who approved, and which
   version and rules ran.
7. **The business owns the rules.** Thresholds, mappings and routing live in a rules sheet or config the process
   owner signs off. The robot applies them.
8. **There is always a manual fallback,** and the team has rehearsed it.

## The seven stages

| # | Stage | Goal | You produce | Gate: who signs | Toolbox |
|---|---|---|---|---|---|
| 1 | Intake and triage | Decide if this is worth automating, and how much | Intake record, suitability score | Your lead / automation CoE | |
| 2 | Discovery | Understand the process exactly as it runs today | Process definition (PDD), samples | Process owner | `describe_shape`, `synth` |
| 3 | Design | Decide how the robot does each step and how it fails | Solution design (SDD), exception table, controls | Peer review; risk / controls where required | patterns, `RETRY_PROFILES` |
| 4 | Build | Build from tested blocks, the same way every time | Robot code, config, rules sheet, unit tests | Code review | template, patterns, recipes |
| 5 | Test | Prove it on every edge case and in UAT | Test evidence, UAT sign-off | Process owner | `--dry-run`, `synth` |
| 6 | Deploy | Release through change control | Change record, runbook, access approvals | Change approver | `schtasks_create`, `single_instance` |
| 7 | Run and improve | Monitor, handle exceptions, keep it current, retire it cleanly | KPIs, incident log, periodic review | Process owner, yearly | exit codes, logs, evidence pack |

## 1. Intake and triage

Score the request before you book a long call. Score each line 0, 1 or 2.

| Question | 0 | 1 | 2 |
|---|---|---|---|
| Are the decisions rules, not judgement? | Mostly judgement | Some judgement | All rules |
| Are the inputs digital and structured? | Paper / free text | Mixed | Files, tables, systems |
| Are the systems and screens stable? | Change monthly | Change yearly | Stable or API |
| Volume | A few a month | Daily | Many per day, or peaks |
| Cost of an error today | Low | Medium | High (money, customer, regulatory) |
| Time it takes people today | < 2 h / month | 2–20 h / month | > 20 h / month |
| Exceptions | Most items | Some | Few |

- **10 or more:** automate end to end (unattended).
- **6 to 9:** automate the preparation and checks, keep a human for the decisions (attended, or maker-checker).
- **Under 6:** fix the process or the data first. A robot would copy the mess faster.

Rough benefit: hours saved per month = items per month × minutes per item ÷ 60 × (1 − exception rate). Put the
error-cost reduction next to it. In a bank it is often worth more than the hours.

## 2. Discovery: the stakeholder call

**Before the call** ask for: the procedure document if one exists, a screen recording of one normal item and one
awkward item, two or three masked sample inputs and outputs, the list of systems and who grants access. Run
`describe_shape(df)` on a sample to see column patterns without seeing values. That output is safe to share and to
paste into an AI tool.

**On the call** walk through a real item live, click by click. Ask "what do you do if…" at every step. Use these
questions:

- **Outcome.** What is the result, who uses it, and what happens if it is late or wrong?
- **Trigger and timing.** What starts the work? When? Which cut-offs, time zones and holiday calendars apply? What
  happens at month end?
- **Inputs.** Where does each input come from, in what format, at what time? How often is it late, incomplete or
  laid out differently? Do the headers change?
- **Steps and rules.** Which decisions are made, on what rule, written down where? Who owns each rule?
- **Systems and access.** Which applications, which versions? Is it Citrix? Is there MFA? Can a service account be
  used? Is there an API or an export instead of screens?
- **Outputs and handover.** What is produced, where is it stored, who is told?
- **Exceptions.** What are the five things that go wrong most often? What do you do for each one? How often does
  each happen?
- **Controls.** What do you check today: totals, reconciliations, a second pair of eyes, sign-offs? What would an
  auditor ask for?
- **Data.** Is there customer or personal data? What is its classification? Who may see it?
- **Volumes.** Items per day, the peak, the month-end spike, the longest single item.
- **Fallback.** If the robot is down, who does the work by hand, and how fast must it be done?
- **Change.** How often do screens, files or rules change, and who tells you?

**Listen for** these phrases. Each one changes the design:

| They say | It means | Design response |
|---|---|---|
| "We usually just…" | An unwritten rule | Write it down, give it an owner, put it in the rules sheet |
| "Sometimes the file is late" | Trigger and timeout | `wait_for_file` with a deadline, then hand over to a human |
| "We check it against…" | A control | `control_totals`, `reconcile` |
| "Only Jane knows how" | Key-person risk | Document the rule before you build |
| "We fix it by hand" | An exception path | BusinessError code plus an exceptions report |
| "It depends on the client" | A decision table | `first_match` with the table in Excel |
| "The screen sometimes freezes" | Retry and fallback | `attempt` with `RETRY_PROFILES['ui']` |

**Capture the process** in one table, one row per step:

| # | Actor | System | Action | Input | Output | Rule / decision | Exceptions | Control | Volume / time |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Ops | Outlook | Save the attachment from the custodian mail | Mail | .xlsx | Sender is custodian X | No mail by 09:00 | – | 1 / day |

**After the call** send the table and a one-page summary back within two days and ask the process owner to confirm
it. **Gate:** the process owner signs the process definition (PDD).

## 3. Design

Design each step, then design how each step fails. Write both into the solution design (SDD).

**Choose the route for each step,** best first:

1. Remove the step: does anyone use its output?
2. Data or API: an official API, a database view, or the JSON the portal page itself calls (`api_over_ui` pattern).
3. A file export the system already produces.
4. The screen: desktop (`pywinauto`), web (`Browser`), Citrix (`pyautogui` images). Use it only when nothing above
   exists.

**Map each step to a block.** Every row of the process table should name the toolbox function or pattern it uses. If
nothing fits, design a new block and add it to the toolbox, instead of hand-coding it in one robot.

**Exception design.** Every failure has a kind, an action and an audience:

| Failure | Kind | Robot does | Who is told | Block |
|---|---|---|---|---|
| Account not found, amount mismatch | Business (data is wrong) | Record it, skip the item, carry on | Process owner, in the exceptions report | `BusinessError`, `process_all` |
| File locked, screen slow, network blip | System (environment) | Retry per profile, then stop after 3 in a row | Support, by exit code 3 | `attempt`, `process_all` |
| Login refused, password expired | Access | Stop at once, never retry | Owner and support | `RobotError` AUTH, `login_once` |
| A bug (`TypeError`, `AttributeError`) | Bug | Stop at once | Developer, exit code 5 | `classify` |
| Input missing or broken | Input | Stop before processing | Process owner, exit code 2 | `safe_input` |
| Past the cut-off | Timing | Do not submit; hand over | Process owner | `assert_before_cutoff` |

**Retry policy.** Use the named profiles in `RETRY_PROFILES` and change them only in one place:

| Profile | Tries | Wait | Check up to | Why |
|---|---|---|---|---|
| `ui` | 3 | 2 s | 10 s | Screens lag and pop-ups steal focus; still fails within about a minute |
| `page_load` | 2 | 5 s | 60 s | Slow searches and reports; one reload is enough |
| `file_lock` | 5 | 10 s, doubling | – | Someone has the file open in Excel |
| `read` | 3 | 2 s, doubling | – | API GET, query, download: safe to repeat |
| `submit` | 1 | – | 30 s | Post, pay, send: more tries only with an `already_done` check |
| `login` | 1 | – | 30 s | A repeated wrong password locks the service account |

On top of the profiles: after a step gives up, the item loop stops after 3 system failures in a row, so a dead
system is not hammered for every item.

**Controls.**

- Input control totals (rows, sum, absolute sum, distinct keys) are recorded before processing and reconciled after.
- An output reconciliation runs against the source or the target system.
- Maker-checker applies to anything irreversible or customer-facing. The checker is a named person, never the robot's
  account.
- Segregation of duties: the developer does not approve production changes or hold production credentials.
- Every run keeps an evidence pack: inputs and outputs with hashes, totals, approvals, rule file version, log.

**Data protection.** Classify the data. Logs carry ids, never values (`get_logger` redacts). Screenshots stay in the
run folder under the retention rule. Test with synthetic data (`synth`). Share shapes, not values (`describe_shape`).

**Access.** Use a service account per robot, with least privilege. Credentials come from the vault (`get_secret`).
Plan MFA with the security team early, since it decides whether a step can be unattended.

**Run design.** Choose the trigger: schedule, file arrival, mail or webhook. Add a single-instance lock,
business-day and cut-off guards, a run folder per business date, and exit codes that monitoring understands. Choose
attended or unattended.

**Fallback.** The manual procedure stays current. The robot has a kill switch (a STOP file) and a dry-run mode.

**Solution design (SDD) outline:** purpose and scope, process (to-be) with the route per step, inputs and outputs,
rules and owners, exception table, retry policy, controls, data classification and protection, access and
credentials, schedule and triggers, evidence and retention, monitoring and exit codes, fallback and kill switch,
open risks. **Gate:** peer review, plus risk or controls review where your policy requires it.

## 4. Build

- Start from `templates/robot_template.py`. It gives you `--env`, `--dry-run`, `--business-date`, a lock, a run
  folder, JSON logs, a checkpoint and exit codes.
- Build steps from **patterns** (copy the block, keep its decisions). Build data flows from **recipes** (a YAML spec
  validated against the real functions, which can generate the script).
- Keep one config per environment. Secrets never go in config. Rules live in a sheet the business owns.
- Make every step safe to re-run: business keys, not row numbers; checkpoints; `already_done` before any repeat of a
  submit.
- Test with synthetic data first.

**Code review checklist:**

- Every failure path raises the right kind: `BusinessError`, `SystemError_`, or a stop.
- Nothing sends, pays or submits without dry-run support and a double-send guard.
- No secrets, no data in logs, no personal accounts.
- Control totals are reconciled in and out.
- Edge cases from the test checklist are covered by tests.
- The version and the rules file hash are recorded in the evidence.

**Gate:** code review by a second engineer.

## 5. Test

**Data edge cases** that every robot is tested against:

- Upper / lower / mixed case, leading and trailing spaces, non-breaking spaces, tabs.
- Blanks, `NA`, `-`, zero; duplicate keys; zero-padded ids (`00123` vs `123`).
- Number formats: `(1,000)`, `1,000-`, `1,000 DR`, `1.234,56`, currency symbols and codes.
- Date formats: day-first, ISO, Excel serials, `20260306`, `9999-12-31`, dates without a year.
- An empty file, missing or renamed columns, extra columns, a very large file, non-Latin names.

**Environment edge cases:**

- A late, missing or locked file; an application that is down, slow or shows a pop-up.
- An expired session or password; a network timeout after a submit.
- A run after the cut-off or on a holiday; a second copy started by the scheduler.
- A crash mid-run, then a re-run: it must resume, and nothing may be sent twice.

**Levels:**

1. **Unit tests** with synthetic data (`pytest`).
2. **System test** in the test environment, first with `--dry-run`, then live.
3. **UAT** on masked real samples. The process owner checks the outputs and the exceptions report.
4. **Parallel run:** the robot and the team process the same days, typically 5 to 10 business days. Compare the
   results until they match.

**Gate:** the process owner signs UAT, with the test evidence attached.

## 6. Deploy

The change record carries the robot version, the SDD, the test evidence, the runbook, access approvals, the schedule,
and a rollback plan (disable the schedule, run by hand). Hypercare runs for the first two weeks: the developer checks
every run.

**Runbook** (one page): what the robot does and when; inputs and where they come from; outputs and who receives them;
the exit code table and what to do for each code; how to re-run for a business date; how to stop it (the kill
switch); the manual fallback; contacts.

| Exit | Meaning | Action |
|---|---|---|
| 0 | OK | None |
| 2 | Business exceptions, or input rejected | Owner works the exceptions report; if no report, read `run.log.jsonl` |
| 3 | System failure | Re-run later; the checkpoint resumes |
| 4 | Config error | Support fixes the config |
| 5 | Robot error | Developer fixes; run by hand meanwhile |
| 6 | Already running | None, or check for a stuck run |

**Gate:** the change approver signs the change record.

## 7. Run, monitor, improve, retire

- **Monitor exit codes** and exception counts after every run. Repeated exit 3 means the environment needs a fix, not
  more retries.
- **KPIs per month:** items processed, straight-through rate, exceptions by code, run time, failed runs, hours
  saved.
- **Exceptions SLA:** the owner works the report the same day.
- **Change triggers:** a screen, file layout or rule change sends the robot back to stage 3. Rule changes in the
  rules sheet need the owner's sign-off and show up in the evidence through the file hash.
- **Periodic review,** at least yearly: confirm access, rules, owner, fallback and that the robot is still needed.
- **Retire cleanly:** remove the schedule, revoke the service account, archive the evidence under the retention rule,
  and update the procedure.

## Artefacts at a glance

| Artefact | Stage | Written by | Approved by | Toolbox help |
|---|---|---|---|---|
| Intake record and score | 1 | You | Lead / CoE | Section 1 table |
| Process definition (PDD) | 2 | You | Process owner | Capture table, `describe_shape` |
| Solution design (SDD) | 3 | You | Peer; risk / controls | Exception and retry tables, patterns |
| Rules sheet | 3–4 | Process owner | Process owner | `rules_from_excel`, `business_rules` pattern |
| Code, config, tests | 4 | You | Code reviewer | Template, patterns, recipes, `synth` |
| Test evidence, UAT sign-off | 5 | You, owner | Process owner | `--dry-run`, evidence pack |
| Change record, runbook | 6 | You | Change approver | Exit code table, `schtasks_create` |
| Run evidence | Every run | The robot | Kept per retention | `controls_evidence`, `get_logger` |
| Periodic review | 7 | You, owner | Process owner | KPIs from logs |

## From design decision to block

| You need to… | Use |
|---|---|
| Click, type or select on a screen, and survive lag and pop-ups | `ui_step` pattern, `attempt`, `RETRY_PROFILES['ui']` |
| Stop cleanly on any failure: close apps, keep evidence, notify | `shutdown_cleanup` pattern |
| Submit, pay or send exactly once | `idempotent_submit` pattern |
| Log in without locking the account | `login_once` pattern, `get_secret` |
| Work a list of items with exceptions and resume | `item_loop` pattern, `process_all`, `Checkpoint` |
| Accept an input file only when it is complete and correct | `safe_input` pattern, `read_any(required=…)` |
| Reconcile two sources | `reconcile_report` pattern, `reconcile` |
| Prove what the robot did | `controls_evidence` pattern, `control_totals`, `file_sha256` |
| Let a named human approve before anything is executed | `maker_checker` pattern, `propose`, `approved_actions` |
| Let operations own the rules | `business_rules` pattern, `rules_from_excel`, `first_match` |
| Run only on business days and before the cut-off | `cutoff_calendar` pattern |
| Tell people and hand over to a human | `notify_handover` pattern |
| Use the data behind a portal instead of clicking | `api_over_ui` pattern |
| Describe a data flow once and generate the code | `recipe` (`load_recipe`, `run_recipe`, `recipe_to_code`) |
