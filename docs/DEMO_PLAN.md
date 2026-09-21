# Demo plan — Equiper (Cirqen Labs) for 20 engineers

**Audience assumed:** 20 biomedical engineers and technologists (the people who
would use it). Appendix B adds a 20-minute technical segment if some of the room
are software engineers.
**Length:** 90 minutes (75 if you drop hands-on, step 11).
**Format:** one presenter machine on a projector, one *demo* HQ, the rest of the
room watching, then a short hands-on.

Everything below was taken from the code and docs in both repos
(`cirqen-labs` and `~/Desktop/hq_server`). Nothing here was run, so **do the
rehearsal in Part 1 before trusting any step.**

---

## Part 0 — Four things that can hurt you on the day

These come from reading the code, and each has a fix or a workaround in the plan.

1. **Do not let 20 laptops sync against your real HQ.** HQ's live-update stream
   (SSE) is capped at 24 clients (`SSE_MAX_CLIENTS`), and a rejected client
   blocks a server thread for 10 s. 20 engineers plus you plus any real site is
   right at that limit, and it has never been load tested. Either keep the
   room's machines offline / view-only, or point the whole room at a separate
   demo HQ.
2. **Two machines online at once can mint the same certificate number.** Your
   rule is "certificate numbers come from HQ only", but the *online* approval
   branch in `CalSoft/view_modules/pending_sessions.py` still allocates locally
   (`max+1`). Approve certificates on **one** machine only, and show the
   *offline* path for the HQ-issued number (step 8). Do not approve on two
   machines at the same time in front of the room.
3. **HQ sleeps.** On Render's free tier the first request after idle can take a
   minute (`PHASE4_OPS.md` 4.12). Warm it 10 minutes before you start:
   `curl https://<hq-host>/api/sync/health`.
4. **Secrets and defaults on screen.** `dist/Cirqen/` ships a `provisioning.json`
   with credentials; `.env` and `.env.build` hold real values; `QUICK_START.txt`
   prints the first-run login (`maina.wairegi` / `ChangeMe123!`).
   `SECURITY.md` also still lists the old credentials in plain text (the
   `git filter-repo` block). Do not open any of these on the projector, do not
   hand out the `dist/` archive, and change the default password before the demo.

---

## Part 1 — Preparation

### One week before

- [ ] Decide the demo machine: a laptop with the packaged build installed, **not**
      your dev checkout. The built `dist/Cirqen/` is dated 21 Sep, but the archive
      `dist/Cirqen_linux_v1.0.0.tar.gz` is old. Rebuild (`python build.py`) so
      what you show is 1.5.4 (`settings.APP_VERSION`). Build from a clean, merged
      `main` so the build is reproducible.
- [ ] Decide the demo HQ (see Part 0.1) and confirm the demo machine's
      `sync.api_url` points at it:
      `python -c "from config import resolve_endpoints as r; print(r('<data dir>'))"`
- [ ] Run the automated checks and read the result, not just the exit code:
      `python manage.py test --settings=Equiper.test_settings`, then `python -m pytest`
      (needs PostgreSQL binaries), then `ruff check .`. If any fail, know which
      before you stand up.
- [ ] Create the demo accounts (Users → `/login/manage/`, HOD only). Minimum:

      | Account | Role | Workshop / department | Used for |
      |---|---|---|---|
      | `hod.demo` | HOD | — | Dashboards, everything visible |
      | `tech.maint` | Tech | a *maintenance* workshop | Raises job cards |
      | `nic.ward` | NIC | one department | Approves job cards |
      | `tech.cal` | Tech | a *calibration_center* workshop | Performs calibrations |
      | `tech.cal2` | Tech | same calibration centre | Approves them (a different person) |

      A calibration must be approved by someone other than the technician who did
      it, and only calibration-centre Techs and HODs can approve
      (`can_review_calibrations`). Every new user is forced to set a password and
      upload/generate a signature on first login (`must_change_password`,
      `has_uploaded_signature`), so **log in once as each account beforehand**.
- [ ] Load realistic data. Import equipment from Excel (Inventory →
      import template) so the demo also shows the import. Then
      `python manage.py backup_db` and
      `python helper_scripts/seed_demo_data.py` for job cards with costs and
      downtime, spare parts, tools and PPM history. It adds records only and can be
      undone with `--undo`. `data/demo_seed_manifest.json` already exists, so check
      whether this machine was seeded before running it again.
- [ ] Set up calibration content: at least one procedure with parameters, set
      values and a reference standard (Calibration → Procedures, Standards). Use one
      that passes and one you can make fail (a reading outside tolerance).
- [ ] Publish a signed test package (`hq_server/build_package.py`) one version
      above the demo machine, for step 10. Rehearse apply **and** rollback on a
      spare machine or VM first.

### One day before

- [ ] Full rehearsal, start to finish, with a timer. Note every step that took
      longer than planned.
- [ ] Generate the fallback video so a dead network does not end the session:
      `python helper_scripts/capture_screens.py` then
      `python helper_scripts/build_showcase_video.py` (needs `ffmpeg`; output
      `data/Equiper_Showcase.mp4`, silent, captioned).
- [ ] Take a fresh `backup_db` of the finished demo state. This is your reset point:
      `restore_db <file>` returns you to it between runs.
- [ ] Pre-download the PDFs you will show (job card, PPM export, certificate,
      manufacturer performance) so a slow render cannot stall you.
- [ ] Set the projector display to a scale where the sidebar is legible from the
      back row (the app is a PySide6 window around a local Django site).

### One hour before

- [ ] Warm HQ (Part 0.3) and confirm the status indicator in the top bar shows online.
- [ ] `python helper_scripts/sync_status.py` — should exit 0 (no conflicts, no drift).
- [ ] Log in as each demo account once in separate browser profiles or note the
      passwords; close everything that is not needed; silence notifications.
- [ ] Have the offline switch ready (Wi-Fi off / cable out) and test it.

---

## Part 2 — Run of show (90 min)

Each step: **Show** (what to click), **Say** (the point for this audience),
**Watch for** (what can go wrong).

### 1. Frame the problem — 5 min
- **Say:** a hospital biomedical department runs on paper job cards, spreadsheets
  for PPM, and calibration certificates typed by hand. Equiper is one desktop app
  per site that covers inventory, job cards, PPM, calibration and ISO/IEC 17025
  certificates, parts and tools, and reports. It works with no internet and
  converges with HQ when it is back.
- Ask the room: "who still tracks PPM in Excel?" It sets up step 5.

### 2. Sign-in, roles and what each person sees — 8 min
- **Show:** log in as `hod.demo` → HOD dashboard (`/dashboard/hod-dashboard/`,
  every workshop). Log out, log in as `tech.maint` → sees only their workshop.
  Log in as `nic.ward` → only their department's records.
- **Then the trust point:** as the Tech, edit a URL to another workshop's job card
  or equipment ID. It returns "not found", not "forbidden". This is
  `core/scoping.py`, tested by `core/tests/test_cross_workshop_access.py`.
- **Say:** HOD sees everything, Tech sees their workshop, NIC sees their
  department. Signatures are stored per user and applied to documents.
- **Watch for:** first-login password and signature prompts if you skipped the
  pre-login.

### 3. Inventory — 10 min (`/Inventory/`)
- **Show:** the equipment list with departments; add one item (description →
  model → manufacturer); download the Excel import template and upload a small
  file; export to Excel and to PDF; transfer an item between departments.
- **Say:** this is the spine. Everything else (job cards, PPM, calibration,
  reports) hangs off an equipment record.
- **Watch for:** PDF export is asynchronous (`check-pdf-status`); start it, talk,
  then come back.

### 4. Job cards, end to end — 12 min (`/jobcard/`)
- **Show:** as `tech.maint`, create a job card for a repair: pick equipment,
  fault, work done, spare parts used (stock is checked live), submit. Status is
  *Waiting Approval*. Switch to `nic.ward` → **Waiting** list → approve it. It
  moves to **Approved**; open the PDF (`Download PDF`) with both signatures on it.
  Decline a second one to show **Declined**.
- **Then the link:** raise a job card against a *pending PPM schedule*, approve it,
  and show that the PPM schedule flips to completed on its own
  (`update_ppm_status_if_applicable`).
- **Say:** two-signature workflow, stock deducted from the parts store, no
  retyping.
- **Watch for:** approver must have a saved signature (`_has_saved_signature`).

### 5. Preventive maintenance (PPM) — 10 min (`/ppms/`)
- **Show:** dashboard, initialize the schedule, bulk-schedule unscheduled
  equipment, push one job to next month, mark one completed, run **Smart
  reorganize**, export the department PDF and Excel.
- **Say:** the schedule is generated from the inventory and rebalances itself.
  This is the module that replaces the spreadsheet.
- **Watch for:** initialize/reorganize run as background tasks; the page polls
  `task-status`. Have a pre-run result ready.

### 6. Calibration — 15 min (`/calibration/` and `/calSchedules/`)
The centrepiece.
- **Show:**
  1. **Definitions.** Procedures, parameters (unit, readings per point,
     tolerance), set values, and the reference **Standard** that gives
     traceability. State that tolerance lives on the procedure, so a technician
     cannot widen the limit their own work is judged against.
  2. **Perform.** As `tech.cal`, open *Perform calibration*, choose equipment and
     procedure, enter readings. The app computes mean, error, uncertainty and
     pass/fail per point as you type. Enter one out-of-tolerance reading on
     purpose and show it fail.
  3. **Review.** Log in as `tech.cal2`, open the pending-approval queue, review the
     readings, approve. (Only one machine, see Part 0.2.)
  4. **Certificate.** Open **Certificates**; show the PDF: number, results table,
     uncertainty, drift, signatures, QR code.
  5. **Scheduling.** Open `/calSchedules/`: on completion the next calibration is
     scheduled automatically, in the *planned* month, with the actual date kept
     separately.
- **Say:** raw readings are stored as entered; every derived number can be
  recomputed by an auditor; the certificate number is issued once, at approval.
- **Watch for:** if the site is online, the number may be assigned locally (Part
  0.2). Keep this to one machine. Step 8 shows the correct HQ-issued path.

### 7. Parts, tools and reports — 8 min
- **Show:** `/accessories/` (spare parts stock, tools, request → approve → accept
  flow, exports). Then `/machineReports/`: per-equipment history with cost and
  downtime, manufacturer performance PDF. Then `/reports/` (report hub, weekly
  reports) and the HOD dashboard again.
- **Say:** because job cards carry cost, downtime and parts, the reports fall out
  for free. This is where the seeded data pays off.

### 8. Works offline, syncs when back — 10 min
- **Show:**
  1. Point at the connection indicator (online). Turn the network **off**.
  2. Keep working: create a job card, then approve a calibration session. The
     message reads *"Approved offline. Certificate will be generated when
     connection is restored"* and the session waits in
     `approved_pending_certificate`.
  3. Turn the network **on**. Watch the sync status; the certificate number
     appears, issued by HQ.
  4. Open `/audit-log/` to show who did what and when.
  5. Open `/settings/hq-connection/` to show where a machine is pointed and the
     **Test** button. From a terminal: `python helper_scripts/sync_status.py`.
- **Say:** each site owns its data; HQ is the authority for certificate numbers and
  the place where sites converge. If two sites edit the same record, the losing
  version is kept in a conflicts table, not dropped.
- **Watch for:** this is the step most likely to be slow on a cold HQ. Rehearse
  the reconnect timing. If it drags, narrate over the video (Part 1).

### 9. Safety net: backups, updates, rollback — 7 min
- **Show:** `python manage.py backup_db --list` (daily 12:30 backup plus manual).
  Then **Updates** (`/updates/`, staff): check for updates → apply the staged
  package → live progress → new version shown. Then **Rollback** for that version.
- **Say:** before applying, the app backs up every file it changes and snapshots
  the local database; a failed download, signature, migration or health check
  restores both automatically.
- **Watch for:** do this on the spare machine or VM you rehearsed on, not the demo
  laptop, unless the rehearsal was clean. This is the step that changes the
  version number under you.

### 10. Honest limits — 3 min
- **Say, plainly:** a fleet of 500+ desktops is the target, and today HQ tops out
  well below that (single worker, direct database connections, SSE cap); it has
  been reviewed but never load tested. Certificate numbering is being made
  HQ-only. Credentials in the repo history are being rotated. Telling engineers
  this yourself is better than having them find it.

### 11. Hands-on — 10 min (optional)
- Split into 4 groups of 5, one demo machine per group, **offline or against the
  demo HQ** (Part 0.1). Each group:
  1. logs in as `tech.maint`, raises a job card;
  2. `nic.ward` approves it;
  3. finds it in **Approved** and downloads the PDF.
- Do **not** run calibration approval hands-on (Part 0.2).

### Close — 2 min
Ask three things and write the answers down: *What did you do on paper this week
that this replaces? What is missing? What would stop you using it?*

---

## Part 3 — Questions you will get

| Question | Answer, and where it lives |
|---|---|
| What if the internet is down for a week? | Work continues; changes queue and upload on reconnect. Runbook §2. |
| What if the laptop dies? | Daily backup at 12:30 plus manual; `restore_db`. Data also converges from HQ. Runbook §4. |
| Is it ISO/IEC 17025 compliant? | It is built toward it (raw data retained, independent approval, append-only audit log, traceable standards). Whether a certificate satisfies an assessor is for the accreditation body; do not claim compliance. See `review/07_CALSOFT_REFERENCE.pdf` §5–6 for what a certificate does and does not guarantee. |
| How does it compare with Fluke Ansur / Beamex? | `review/03_VS_FLUKE_AND_BEAMEX.pdf`. Self-rated 8.1/10 for CalSoft after the recent work. |
| Who can see whose data? | Role scoping, step 2. `core/scoping.py`. |
| Can it run on Windows? | The build supports Windows (`%LOCALAPPDATA%\Cirqen\data\`); this session only saw a Linux build. Confirm on a Windows build before saying yes. |
| How many sites can it support? | See step 10. |

## Part 4 — After the demo

- Reset: `restore_db` the pre-demo backup; `seed_demo_data.py --undo` if needed.
- Rotate anything that was on screen or on a shared machine, and finish the credential
  rotation in `SECURITY.md`.
- Send the feedback answers to whoever owns the plan (`review/06_SYSTEM_PLAN.pdf`).

---

## Appendix A — Time budget

| Step | Min | Running |
|---|---|---|
| 1 Frame | 5 | 5 |
| 2 Roles | 8 | 13 |
| 3 Inventory | 10 | 23 |
| 4 Job cards | 12 | 35 |
| 5 PPM | 10 | 45 |
| 6 Calibration | 15 | 60 |
| 7 Parts / reports | 8 | 68 |
| 8 Offline / sync | 10 | 78 |
| 9 Backups / updates | 7 | 85 |
| 10 Limits + close | 5 | 90 |
| 11 Hands-on | 10 | 100 (cut to fit 90) |

If you must fit 75 minutes, drop step 11 and shorten steps 3 and 7 to a
walk-through of pre-built pages.

## Appendix B — Technical segment for software engineers (20 min)

| Topic | Show |
|---|---|
| Shape | PySide6 shell (`main.py`, `bulider_tools/`) around Django (`Equiper/`), embedded PostgreSQL and Redis, sync agent in `sync/` |
| Sync engine | `sync/ARCHITECTURE.md`: timestamp poller, idempotent upload batches, LWW resolver, conflict quarantine, schema-drift guard |
| Two repos | `cirqen-labs` and `~/Desktop/hq_server` (sync + certificate authority). `cirqen-labs/hq_server/` is a *different* thing: the update server (Ed25519 signed packages) |
| Config | One place names HQ hosts: `HQ_ENDPOINT_DEFAULTS` in `config.py`; env → `config.json` → `provisioning.json` → default |
| Tests | `core/tests/test_route_smoke.py`, `test_cross_workshop_access.py`, `test_query_budget.py`; CI in `.github/workflows/ci.yml` |
| Known debt | 9-way mixin inheritance in the sync agent (documented, refactor deferred); the two certificate-numbering algorithms; HQ single worker |
