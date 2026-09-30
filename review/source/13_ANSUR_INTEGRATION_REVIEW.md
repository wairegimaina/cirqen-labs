# Fluke Ansur in Cirqen: how it works, and a rating

| | |
|---|---|
| Document | 13, a review of the integration built from plan 12 |
| For | The calibration-centre lead, whoever maintains CalSoft, biomed and IT at the site |
| Checked against | *Ansur Test Executive Users Manual*, FBC-0001 Rev. 6 (Ansur 3.1.3); the code on `main` at `e3b891e`; the HQ repository `hq_server` |
| Date | 28 September 2026 |
| **Rating** | **6.0 / 10 as reviewed. 7.1 / 10 with Ansur v2 (§8). About 8.0 after one real run at the site.** |

## 0. The verdict on one page

**The core is well built. The edges have not been proven.** When a record reaches Cirqen, the checks, the maths and the approval rules are careful and well tested. The weak points are at both ends. First, the file that starts Ansur does not match the layout in Fluke's manual. Second, until today, the Ansur fields never reached HQ, so other PCs could not see them.

**Strong:**

- **A bad record is never guessed at.** A record that does not match its job (serial, template, analyser, missing or extra results) is refused with every reason and moved to quarantine. Nothing is saved.
- **Cirqen does its own maths.** Ansur's Pass/Fail is kept beside Cirqen's verdict, not trusted in its place. Cirqen works out its own uncertainty and applies a guard band. Where the two verdicts differ, the reviewer must tick a box before approving, and the server enforces it.
- **Every record is fingerprinted.** Cirqen keeps its own copy of each record and PDF with a SHA-256 fingerprint. The review refuses to show a PDF that has changed since import.
- **The same approval path as manual work.** An Ansur session goes through the same approval, HQ numbering, audit trail and certificate as a manual one.

**Weak:**

- **The work-order file does not match the manual** (§4). Ansur will probably open without the device and template filled in. This is the first thing to fix.
- **It has never read a real record.** All 74 tests use records written by hand. The reader works by the *shape* of the data because the manual does not document how each plug-in stores its results. It is safe (unknown layouts are refused), but it is unproven.
- **Ansur details stay on the Ansur PC.** The job, the record copy and Ansur's PDF are not synced. A review or certificate done on another PC has no job number, no PDF link and no Annex A.
- **Every template must be set up twice.** Each Ansur template needs a matching Cirqen procedure with the same set values and limits, entered by hand.

## 1. What Ansur is, in Cirqen's terms

Ansur is Fluke Biomedical's test software. It drives the analysers (ESA615, ProSim and others) through plug-ins and saves the results. Its words map onto Cirqen's like this:

| Ansur | What it is | In Cirqen |
|---|---|---|
| Test template (`.mtt`) | The list of tests, their limits and which analyser runs them | Matches a **calibration procedure**. Linked one-to-one on the Ansur connection page |
| Procedure | Only the instruction text shown for one test step | No equivalent; not used |
| Test element / step | One test, e.g. "Leakage current" | Matches a **parameter**. Linked by the step name |
| Work order (JobOrder XML) | A file another program writes to start Ansur on one device | Written by Cirqen for each job |
| Test record (`.mtr`) | The results of one run, as XML | Read by Cirqen and turned into a calibration session |
| DUT info | The device details: serial, model, location | Filled in by Cirqen, plus a custom **Cirqen Job** field |
| Service event | Which tests of a template apply to this visit | Set per linked procedure (default `PM`) |

**Ansur has no procedure you select in Cirqen.** The technician always picks a *Cirqen* procedure. If that procedure is linked to a template, **Start with Ansur** appears, and Ansur opens the linked template by itself.

## 2. How it works, end to end

```
 CIRQEN (browser + Django, on the Ansur PC)            FLUKE ANSUR
 ------------------------------------------            -----------
 Perform Calibration
   pick equipment, schedule, linked procedure
   enter room conditions
   press "Start with Ansur" ------------------------+
                                                     |
 ansur/start/                                        |
   AnsurJob created (job number CQ-yymmdd-XXXX)      |
   work order written to C:\CirqenAnsur\jobs  ------+--> Ansur.exe "<work order>"
                                                            opens the template,
 page checks ansur/status/ every 2 s                        device filled in
   (each check also scans the results folder)               technician runs the test
                                                            record saved to
 Celery "scan-ansur-results" also scans  <-----------------  C:\CirqenAnsur\results
                                                             (CIRQEN-<job>.mtr)
 watcher: claim the file (move to .processing)
 parser : read DUT, analysers, steps
 importer: check against the job -- refused? --> quarantine + reason shown on the page
          |
          ok
 Ansur.exe /f record.mtr /h  ---------------------------> writes Ansur's PDF
 session created (source = ansur), readings, Cirqen's
   uncertainty and verdict, Ansur's Pass/Fail kept
 record + PDF archived, fingerprinted
          |
 Pending Approval: side-by-side verdicts, tick required where they differ
          |
 Approve -> HQ issues the certificate number -> certificate with
   "Measured with Fluke Ansur" note, "Checks recorded in Ansur", Annex A
```

**Step by step:**

1. **Set-up, once** (calibration centre or HOD, on the Ansur PC; *Calibration → Ansur connection*):
    - Find Ansur's program and create the work folders.
    - Copy the templates into `C:\CirqenAnsur\templates`.
    - Link each procedure to its template.
    - For each parameter, enter the Ansur step name, how it is judged (± tolerance, at most, at least), and the analyser's accuracy and resolution.
    - The page will not switch the connection on until every check is green.
2. **Start.** On Perform Calibration, the button shows only for linked, fully set-up procedures. Pressing it:
    - creates one open job per piece of equipment (enforced by the database);
    - writes the work order;
    - launches Ansur as the signed-in Windows user.
3. **Run.** The technician works in Ansur as they do today. The page shows the progress steps and says so if Ansur has been closed without saving.
4. **Import.** Records are picked up in two ways: by the background worker, and by every status check from the page. So an import still happens if the worker is down.
    - A file is taken only once it has stopped changing. It is claimed by moving it, so it is never read half-written or imported twice.
    - The importer checks: job number, serial, template, that the test completed, that every analyser is in the standards register with a current calibration, and exactly one result per set value with the same limit.
    - A failure refuses the whole record. The technician fixes the cause and saves again; the same job accepts a corrected record.
5. **Maths.** Each Ansur reading is one measurement, so Cirqen builds the uncertainty from the analyser's accuracy and resolution plus the reference standard, instead of from spread.
    - One-sided limits ("at most 500 µA") get a guard band on that side only.
    - Near a limit the verdict is INDETERMINATE, not a forced Pass.
6. **Review.** The approval screen shows the Ansur job, the operator, Ansur's verdict beside Cirqen's for every point, the Pass/Fail-only checks, and a link to Ansur's PDF.
    - Rows where the verdicts differ are highlighted.
    - Approving then needs a tick, and the server checks it too.
    - A failed Ansur check fails the calibration.
7. **Certificate.** The certificate says the readings were taken with Ansur and lists the checks recorded in Ansur. Ansur's detailed report is attached as **Annex A**.
8. **Fallbacks.** *Open Ansur again* re-opens the same job. *Import a record by hand* takes a saved `.mtr` file through the same checks.

## 3. Where each part lives

**Data** (`CalSoft/models.py`):

| Model / field | Holds | Synced to HQ |
|---|---|---|
| `AnsurSettings` | Program path, work folder, command-line forms, on/off | No (one per PC) |
| `AnsurTemplateMap` | Procedure ↔ template, Ansur standard, service event | No |
| `AnsurJob` | One work order: status, room conditions, record and PDF copies with SHA-256 | No |
| `CalibrationParameter` Ansur fields | Step name, limit type, analyser accuracy and resolution | Yes, from HQ migration `2026_add_ansur_calibration.sql` |
| `CalibrationSession` Ansur fields | `source`, operator, disagreements, checks, record fingerprint | Yes, as above |
| `CalibrationReading.ansur_status` | Ansur's own Pass/Fail per point | Yes, as above |

**Back end** (`CalSoft/ansur/`):

| File | Does |
|---|---|
| `setup.py` | Finds Ansur, creates and checks folders, lists templates, readiness checks |
| `jobfile.py` | Writes the work order |
| `launcher.py` | Starts Ansur; runs `/f … /h` for the PDF; checks whether Ansur is running |
| `watcher.py` | Claims and processes records; quarantine; archive pruning |
| `parser.py` | Reads a record into DUT, analysers, steps and checks |
| `importer.py` | Checks a record against its job and creates the session |
| `annex.py` | Attaches Ansur's PDF to the certificate as Annex A |

**Views and URLs** (`CalSoft/view_modules/ansur.py`, `CalSoft/urls.py`): `settings/ansur/`, `ansur/start/`, `ansur/status/`, `ansur/action/` (reopen or cancel), `ansur/upload/`, `ansur/session/<id>/pdf/`.

**Front end:**

- `templates/Calibrition/ansur_settings.html`: the connection page.
- `templates/Calibrition/calibration.html` with `static/calibrition/ansur_start.js` and `.css`: the button, progress panel and fallbacks.
- `sessions_pending_approval.html` and `.js`: side-by-side verdicts and the acknowledgement tick.
- Sidebar: *Calibration → Ansur connection* for calibration-centre staff; the HOD has their own link.

**Shared with manual calibration:** `finalise_session` (verdict, due date, schedule, history, audit), `utils.one_sided_decision` (guard-banded one-sided verdicts), the certificate sections, and approval in `pending_sessions.py`.

**Background jobs** (`Equiper/celery.py`): `scan-ansur-results` imports new records; `prune-ansur-archive` removes archived records past the retention.

**Tests:** `CalSoft/test_ansur_{maths,import,review,settings,start}.py` (74 tests, all passing), plus browser tests in `e2e/tests.py`.

## 4. Checked against Fluke's manual

| What Cirqen relies on | Manual says | Result |
|---|---|---|
| Start Ansur with a work order as the parameter | "The 3rd-party applications start Ansur with the work order file as a parameter" (4-37) | **Matches** |
| `<Setup>` position in the work order | A direct child of `<METRONFile>`, beside `<JobOrder>` (example, 4-37) | **Does not match.** Cirqen puts `<Setup>` inside `<JobOrder>`, so Ansur will probably ignore the device and template |
| Service event | `<ServiceEvents><Activity Type="" Event=""/></ServiceEvents>` (4-37) | **Does not match.** Cirqen writes the text `PM`, so the event is not pre-selected |
| Language | `<Language GUI="" Report="">` (4-37) | **Does not match.** Cirqen writes the text `English`; probably harmless |
| `OutputDir`, `ResultFile`, `ReadOnly="True"` | Documented (4-37, Table 4-8) | **Matches** |
| DUT items with `Key="True"` on the serial | Documented (4-37) | **Matches** |
| Custom DUT field *Cirqen Job* | Tools → Options → DUT tab (2-10) | **Matches.** Must be added on each PC |
| PDF with `/f record /h`, saved beside the `.mtr` | Appendix B | **Matches** |
| Record root `<METRONFile Type="Record">`, `Setup`, `TestInstruments` | Documented (4-37, 4-38) | **Matches** |
| Measurement layout inside `PlugInData` | "Ansur does not define the contents … This is done by each Plug-In" (4-38) | **Unknown.** Read by shape; unrecognised layouts are refused |
| Electronic signature on save (Ansur 3.0+) | Saving asks for a password; "the electronic signature is encrypted with the saved file" (3-8, 2-9) | **Unknown.** Test at the site; fallback is logging in with *Disable Electronic Signature* (2-8) |
| Restrict Access off | When on, files open only for the same licensed establishment (4-35) | **Documented** in Cirqen's guide |

The site runs Ansur 3.1.4 and the manual covers 3.1.3. The work-order and command-line sections have not changed since 2.9.7, so the comparison holds.

## 5. Found and fixed on 28 September

| Finding | Fix | Where |
|---|---|---|
| The docs told sites to apply HQ migration `2026_add_ansur_calibration.sql`, which did not exist. HQ silently dropped the Ansur columns on upload, so on any other PC an Ansur session looked manual and **the disagreement tick was skipped** | Wrote the migration. It adds 11 columns with defaults so older desktops can still upload, and runs on HQ's next boot. Tested twice inside a rolled-back transaction | `hq_server`, branch `ansur-calibration-columns` |
| HQ cached each table's JSON columns with no expiry. An upload during the boot migration would have broken `ansur_checks` until the next restart | The cache now expires after 5 minutes, like HQ's other column cache | `hq_server/dependency_parent_utils.py` |
| The Ansur connection page was HOD-only, but the calibration centre does the calibrations | Opened to calibration-centre staff, using the same check that decides who reviews sessions; link added to their Calibration menu; three tests | `cirqen-labs`, branch `ansur-calibration-centre-access` |
| The compiled app's database had eight unapplied migrations, four already applied by hand | Backed up; the four hand-applied ones recorded as applied after checking their tables, the rest applied | Local database, port 2216 |

Both branches are uncommitted and waiting for review.

## 6. Rating

Seven dimensions, weighted by how much each decides whether a certificate from an Ansur run can be trusted and produced without friction.

| Dimension | Weight | Score | Why |
|---|---|---|---|
| Import safety and record integrity | 20% | **9.0** | Checked before anything is written; refuses rather than guesses; quarantine with reasons; claim-by-move; SHA-256 copies |
| Metrology and verdict | 15% | **8.5** | Own uncertainty budget for single readings; one-sided guard bands; INDETERMINATE near limits; Ansur's verdict kept and compared |
| Matches Ansur's documented interface | 15% | **4.0** | Work-order layout wrong in three places; electronic signatures not handled or tested |
| Proven against real Ansur output | 15% | **2.0** | No real record has ever been read; every test record is hand-written |
| Fit with the rest of the system | 15% | **4.5** | HQ columns fixed today but not deployed; job, record and PDF stay on one PC; the session page shows nothing about Ansur |
| Set-up and daily use | 10% | **6.0** | Clear readiness checklist and good fallbacks; every template must be mirrored as a Cirqen procedure by hand |
| Tests and documentation | 10% | **7.0** | 74 tests and a site guide; but the guide named an HQ file that did not exist |
| **Overall** | | **6.0 / 10** | |

**How it compares:** the parts inside Cirqen would score about 8.5 on their own. The overall score is pulled down by the two ends: the handover to Ansur and the handover to HQ.

## 7. From 6.0 to 8.0

In order. Items 1 and 2 need nothing but code; item 3 needs one visit to the Ansur PC.

1. **Fix the work order** (`CalSoft/ansur/jobfile.py`). Move `<Setup>` beside `<JobOrder>`, write the service event as `<Activity Type="" Event="…"/>`, and use `<Language GUI="English" Report="English"/>`. Add a test that compares the file's structure with the manual's example. *Interface 4.0 → 7.0.*
2. **Deploy the HQ migration.** Merge the `hq_server` branch and restart HQ. Confirm with one Ansur session reviewed on a second PC. *System fit 4.5 → 6.0.*
3. **One real run at the site.** Save one record from each analyser in use, with electronic signature on and off, and add them as test fixtures. If a plug-in's layout is not recognised, write its small adapter. *Proven 2.0 → 7.0; interface → 8.0.*
4. **Show Ansur on the session page.** Add the source, operator, verdicts, checks and PDF link to `session_detail`, where *Open the session* leads. *System fit → 7.0.*
5. **Decide where Ansur sessions are reviewed.** Either sync `AnsurJob` and send the PDF through HQ, or keep review and issue on the Ansur PC and have the other PCs say so. *System fit → 7.5.*

**Worth doing next:** build the Cirqen procedure *from* the Ansur template. Templates are XML like records, so the step names and limits can be read instead of typed. *Set-up 6.0 → 8.0.*

**After items 1–5:** 20% × 9.0 + 15% × 8.5 + 15% × 8.0 + 15% × 7.0 + 15% × 7.5 + 10% × 6.0 + 10% × 7.5 = **7.8 / 10**. With template import as well (set-up 8.0): **8.0 / 10**.

## 8. Ansur v2: what changed, and the new rating

Built on 28 September 2026 on branch `ansur-v2` (cirqen-labs), with the HQ migration on `ansur-calibration-columns` (hq_server). All 95 Ansur tests and 433 calibration and scheduling tests pass.

| # | Change | Why |
|---|---|---|
| 1 | **Work order matches the manual.** `<Setup>` sits beside `<JobOrder>`; the service event is `<Activity Type="" Event=""/>`; `<Language GUI Report>`; DUT items carry `Ord` and `Caption`; ISO-8859-1 as in the manual's example | §4: Ansur would probably have opened empty. A test compares the file with the manual's example element by element, and fails on the old code |
| 2 | **Create a procedure from a template.** The Ansur connection page reads a `.mtt`'s test steps and limits and creates the procedure, parameters, set values and link | §0: every template had to be typed twice. Now only the analyser accuracy and certificate uncertainty are entered by hand |
| 3 | **Reference uncertainty on the Ansur page, and required.** A linked procedure is not offered until each parameter has one | A procedure built from a template cannot know it; a silent default would reach the certificate |
| 4 | **Database defaults** on the eight NOT NULL Ansur columns (CalSoft 0012), plus the same in the HQ migration | Older builds, and rows from HQ, failed to insert calibration sessions and readings |
| 5 | **The job number syncs** (`ansur_job_number` on the session). On another PC the review and session page show it, and say that Ansur's PDF is on the Ansur PC | §0: other PCs showed nothing |
| 6 | **Session page shows Ansur.** Job, operator, disagreements, checks, record fingerprint, PDF link and an Ansur column in the readings | *Open the session* led to a page with no Ansur details |
| 7 | **Protected records explained.** A record Ansur saved protected (Restrict Access, electronic signature) is refused with the exact Ansur settings to change | Ansur 3.x signs records on save; the old message only guessed at Restrict Access |
| 8 | **Calibration centre can run it.** The Ansur connection page and job actions (reopen, cancel, import) are open to calibration-centre staff, not only the HOD and the job's starter | They do the calibrations |
| 9 | **Numbers print as written.** Refusals said "3.6E+2"; they now say "360" | Found while testing change 2 |
| 10 | **HQ JSON-column cache expires** after 5 minutes | An upload during HQ's boot migration would have broken `ansur_checks` until a restart |

**Left as a decision, not changed:** the Ansur job, record copy and PDF still stay on the Ansur PC. Syncing them means sending files through HQ. v2 makes the gap visible instead: other PCs show the job number and say where the PDF is. The guide says to review and issue Ansur sessions on the Ansur PC.

### 8.1 Re-rating

| Dimension | Weight | Was | Now | Why it moved |
|---|---|---|---|---|
| Import safety and record integrity | 20% | 9.0 | **9.0** | Unchanged |
| Metrology and verdict | 15% | 8.5 | **8.5** | Unchanged; reference uncertainty can no longer be left at a default |
| Matches Ansur's documented interface | 15% | 4.0 | **7.0** | Work order checked against the manual's example; protected records handled. Not yet seen by a real Ansur |
| Proven against real Ansur output | 15% | 2.0 | **2.0** | Still no real record. Only a site run can move this |
| Fit with the rest of the system | 15% | 4.5 | **7.0** | HQ columns and defaults, job number synced, session page, cross-PC notes. PDF still local |
| Set-up and daily use | 10% | 6.0 | **8.0** | Procedures from templates; calibration centre runs it; clearer refusals |
| Tests and documentation | 10% | 7.0 | **8.0** | 95 Ansur tests including one against the manual; guide corrected against the manual |
| **Overall** | | **6.0** | **7.1 / 10** | |

### 8.2 The one step left to 8.0

**One real run at the site** (plan in §7, item 3). Save one record from each analyser in use, and one template, and add them as test fixtures. If a plug-in's layout is not recognised, write its small adapter. That moves *Proven* to 7.0 and *Interface* to 8.0: **about 8.0 / 10.** Beyond that, syncing the PDF through HQ would lift *System fit* to 8.5.

