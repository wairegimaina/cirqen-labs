# Ansur in Perform Calibration: the plan

| | |
|---|---|
| Document | 12, supersedes the connector options in 10 for this site |
| For | Whoever builds it, the calibration-centre lead, the hospital's biomed and IT staff |
| Site | One hospital. Fluke Ansur 3.1.4 already in use; no MET/CAL |
| Set-up | Cirqen and Ansur on the **same Windows desktop** |
| Date | 27 September 2026 |

## 0. The decision on one page

**Best method: Cirqen starts Ansur, and Ansur hands the result back through a watched folder.**

1. On the **Perform Calibration** page the technician picks the equipment and schedule as usual.
2. They press **Start with Ansur**.
3. Cirqen writes an Ansur *work order* (a JobOrder XML file). It carries the device details, the right Ansur template, the Cirqen job number and a results folder. Cirqen then launches Ansur with that file.
4. Ansur opens with the device already filled in and read-only. The technician runs the test on the analyser exactly as they do today.
5. Ansur saves its test record (`.mtr`, an XML file) into the results folder.
6. Cirqen sees the file and checks that it belongs to the job. It then does **its own maths** (error, uncertainty, guard-banded verdict), and asks Ansur for its detailed PDF.
7. The session goes into the normal queue: a different reviewer approves it, HQ gives the number, and Cirqen issues the frozen, QR-coded certificate. The Ansur PDF is attached as an annex.

**Why this method:**

- **Documented features only.** It uses nothing but what Fluke documents: the work-order file, the command line (`/f` makes the PDF) and the XML record. There are no hacks and no undocumented database.
- **No MET/CAL and no extra Fluke licence.** Ansur already drives the analysers; Cirqen never talks to the instruments.
- **Nothing new for the technician.** They keep the Ansur screens they know and gain an automatic certificate, so there is no typing twice and no mismatched serials.
- **Cirqen's maths and approval apply.** Ansur results get the same maths, approval, numbering and audit trail as manual calibrations, so one validation pack covers both.

**Not chosen, and why:**

| Option | Verdict |
|---|---|
| Technician imports `.mtr` files by hand (upload button) | **Kept as the fallback** (same parser). Used if launching Ansur is blocked on a PC. |
| Cirqen drives the analysers directly | No. It means rewriting Fluke's instrument plug-ins and re-validating them. |
| MET/CAL | Not needed. It is Fluke's calibration-lab product; Ansur alone does what this site needs. |
| Read Ansur's SQL Server | No. It holds only users and e-signatures; results live in files. |

## 1. How the two work together

```
 Perform Calibration page            Ansur (same PC)             Analyser
 ------------------------            ---------------             --------
 pick equipment + schedule
 press "Start with Ansur"
   -> AnsurJob created (Prepared)
   -> job file written to
      C:\CirqenAnsur\jobs\CQ-000123.xml
   -> Ansur.exe launched      --->  opens template,
      (status: Sent)                DUT fields filled,
                                    read-only
                                    technician runs test  <-->  measures
                                    saves record to
      watcher sees the file   <---  C:\CirqenAnsur\results\
      (status: Result received)       CIRQEN-CQ-000123.mtr
   -> checks + maths (Checked)
   -> Ansur.exe /f record /h  --->  writes detailed PDF
   -> CalibrationSession +
      readings saved
      (status: Awaiting approval)
 reviewer approves -> HQ number -> frozen certificate + Ansur annex
```

The **Cirqen job number** is the thread that ties everything together. It is in the file names, in a custom DUT field inside Ansur, and on the `AnsurJob` record. A result is accepted only if its job number *and* serial match an open job.

## 2. The Perform Calibration page

The manual **Start calibration** button stays exactly as it is. A second button sits beside it:

**Start with Ansur** (shown only when the Ansur connection is set up in Settings and the procedure is mapped to an Ansur template).

Before the button is enabled, the technician must have done three things. These are the same checks as a manual calibration, so both paths are equally strict:

- Selected the equipment and schedule. The page already suggests the open schedule; one open schedule per equipment is enforced.
- Entered the environment (temperature, humidity).
- Chosen the analyser(s) from the **standards register**. A standard past its due date is refused (R5), as it is today.

**When pressed**, Cirqen:

1. Creates an `AnsurJob` (one open job per equipment; a second press reopens the same job rather than making a duplicate).
2. Writes the job file.
3. Launches Ansur.
4. Replaces the form with a **status panel** that updates every 2 seconds, using the same polling pattern as the certificate-number view:

| Status | Meaning | Technician can |
|---|---|---|
| Prepared | Job file written | Cancel |
| Sent | Ansur launched with the job | Re-open Ansur, Cancel |
| Running | Ansur is open with this job (optional; see section 12) | Re-open Ansur |
| Result received | Record file found and stable | Wait |
| Checked | Record matched, maths done | Open the preview |
| Awaiting approval | Session created, in the reviewer's queue | Go to the session |
| Rejected record | Wrong serial, aborted test or unreadable file, with the reason shown | Re-run in Ansur, or Cancel |

**Edge cases the page handles:**

- **Technician closes Ansur without saving.** The job stays at *Sent*, and **Re-open Ansur** launches it again with the same file.
- **Test aborted, or steps not performed.** The record is refused with the reason, and the technician re-runs.
- **Page closed or PC restarted.** The job survives. Re-opening Perform Calibration for that equipment shows the panel again.
- **Result arrives while the page is closed.** The session is still created. The technician gets a notification, like other calibration events.

## 3. The work order Cirqen writes

This follows the JobOrder layout in the Ansur manual. The template and field names come from the site's templates, which we collect in Phase 0.

```
<?xml version="1.0" encoding="utf-8"?>
<METRONFile Type="JobOrder" Version="1">
  <JobOrder>
    <Language>English</Language>
    <OutputDir>C:\CirqenAnsur\results</OutputDir>
    <Setup Template="C:\CirqenAnsur\templates\Defibrillator.mtt"
           ResultFile="CIRQEN-CQ-000123.mtr" ReadOnly="True">
      <DUT>
        <Item Name="Serial No" Key="True">DF-44102</Item>
        <Item Name="Manufacturer">Zoll</Item>
        <Item Name="Model">R Series</Item>
        <Item Name="Location">ICU</Item>
        <Item Name="Cirqen Job">CQ-000123</Item>
      </DUT>
      <ServiceEvents>PM</ServiceEvents>
      <Standard AlphaName="IEC 62353" />
    </Setup>
  </JobOrder>
</METRONFile>
```

| Cirqen field | Ansur element |
|---|---|
| Equipment serial number | DUT `Serial No` (key field) |
| Manufacturer, model, department | DUT `Manufacturer`, `Model`, `Location` |
| Job number | Custom DUT field `Cirqen Job` (added to the site's templates once) |
| Procedure | `.mtt` template, via the **template map** in Settings |
| Schedule type (PPM or after repair) | `ServiceEvents` |
| Safety standard of the procedure | `Standard` |
| Where results go | `OutputDir` and `ResultFile` |

`ReadOnly="True"` stops the technician from changing the serial number inside Ansur, which is the commonest source of mismatched records.

## 4. Launching Ansur

- **Finding Ansur.** Settings auto-detects it: first the Windows registry, then `C:\Program Files\Fluke\Ansur` and `C:\Program Files (x86)\Fluke\Ansur`. The path can be overridden, and a **Test connection** button checks it.
- **Who runs it.** Cirqen's desktop app starts Ansur as the **logged-in Windows user**, not as a service. A Windows service cannot open a window on the technician's desktop, and Ansur needs the user's session for its instrument drivers and e-signature.
- **The command line.** Ansur's manual says third-party programs start it with the job file as a parameter. It does not print the exact switch. Phase 0 confirms it on the real PC (for example `Ansur.exe "C:\CirqenAnsur\jobs\CQ-000123.xml"`).
- **If Ansur is already open.** We confirm in Phase 0 whether a second launch hands the job to the open window or starts a second copy. If neither works, Cirqen tells the technician to close Ansur first.
- **Fallback if the job-file launch is not accepted.** Cirqen opens Ansur plainly and shows the job number. The technician opens the mapped template and types the job number into the `Cirqen Job` field. The watcher still matches on job number plus serial, so the only cost is typing.

## 5. Picking up the result

A watcher runs in the desktop app's background worker, the same process that already runs sync. It never touches a file while Ansur is still writing it.

1. **Wait for a stable file.** The size must stay unchanged for 2 seconds and the file must open exclusively.
2. **Parse the XML.** Root `METRONFile Type="Record"`, then `Setup/DUT`, `Standard`, `TestInstruments`, the test steps and their status.
3. **Run the checks:**

| Check | If it fails |
|---|---|
| `Cirqen Job` matches an open `AnsurJob` | Moved to `quarantine\` with the reason: "no matching job" |
| `Serial No` equals the job's equipment serial | Refused: "serial mismatch" |
| Overall status is not *Aborted*, and no required step is *Not performed* | Refused: "test incomplete" |
| Template matches the one the job asked for | Refused: "wrong template" |
| Every analyser in `TestInstruments` is in the standards register and in date | Refused: "analyser not registered / overdue" |
| File readable, not encrypted by *Restrict access* | Refused: "record locked by Ansur" |

4. **Make the PDF.** Cirqen runs `Ansur.exe /f <record> /h`, which silently writes Ansur's detailed PDF next to the record.
5. **Store both files.** Cirqen copies the `.mtr` and PDF into its own storage and records a SHA-256 of each, the same fingerprinting used for issued certificates (R7). The original moves to `archive\`. **Cirqen never edits an Ansur record.**
6. **Create the session** (section 6), then set the job to *Awaiting approval*.

## 6. Turning Ansur results into Cirqen readings

Each Ansur step that has a **measured value and a limit** becomes a `CalibrationReading`. Steps that are only Pass/Fail (visual inspection, alarms) go to the job-card checklist, not into the maths.

### 6.1 Limit types

| Ansur limit | Cirqen treats it as | Verdict |
|---|---|---|
| Dynamic X + Y, or X + X*Y% (for example 360 J ±10%) | Set value X, tolerance ±T | Two-sided guard band (as today) |
| Absolute high only (for example leakage at most 500 µA) | Upper limit L | One-sided: PASS if x <= L - U, FAIL if x >= L + U |
| Absolute low only (for example insulation at least 2 M ohm) | Lower limit L | One-sided: PASS if x >= L + U, FAIL if x <= L - U |
| Absolute high and low | Set value = midpoint, T = half-range | Two-sided |
| Relative | Converted to absolute using the reference value stored in the record | As above |

Between the PASS and FAIL lines the verdict is **INDETERMINATE**, as it is for manual calibrations.

### 6.2 Uncertainty for a single reading

Ansur records one measurement per test point, so there is no Type A spread. The budget comes from the analyser:

| Component | Source | Distribution |
|---|---|---|
| Analyser accuracy | The analyser's specification at that range: ± (% of reading + floor) | Rectangular, half-width / $\sqrt{3}$ |
| Resolution | Display step of the analyser | Rectangular, step / $\sqrt{12}$ |
| Analyser calibration | Expanded uncertainty on the analyser's own certificate / its k | Normal |

Combined as the root sum of squares, with k = 2. The analyser's accuracy (± % of reading and ± fixed part) and resolution are entered per procedure parameter on the Ansur connection page, from Fluke's datasheet, and reviewed by the calibration-centre lead; its certificate uncertainty is the parameter's reference uncertainty, as for manual work. (Built this way rather than as a separate specification library: each parameter already names its analyser as the reference standard.)

**Worked example: defibrillator energy, two-sided.** The figures are illustrative; use the analyser's datasheet.

Set 360 J, tolerance ±10% = ±36 J. Ansur measured **352.4 J**. Analyser spec ±(1% of reading + 0.1 J), resolution 0.1 J, analyser certificate 1.0 J at k = 2.

| Step | Working | Result |
|---|---|---|
| Accuracy half-width | 0.01 × 352.4 + 0.1 | 3.624 J |
| u spec | 3.624 / $\sqrt{3}$ | 2.092317 J |
| u res | 0.1 / $\sqrt{12}$ | 0.028868 J |
| u cal | 1.0 / 2 | 0.500000 J |
| Combined u c | root sum of squares | 2.151424 J |
| Expanded U | 2 × 2.151424 | 4.302848 J |
| Error (set - reading, section 9 of the validation pack) | 360 - 352.4 | 7.600000 J |
| TUR | 36 / 4.302848 | 8.37 |
| Acceptance limit | 36 - 4.302848 | 31.697152 J |
| Verdict | 7.6 <= 31.697152 | **PASS** |

**Worked example: earth leakage, one-sided.** Limit at most 500 µA. Measured **112 µA**. Spec ±(1% + 1 µA), resolution 1 µA, certificate 2 µA at k = 2. The uncertainty works out as U = 3.213389 µA. PASS needs x <= 500 - 3.213389 = 496.786611 µA; since 112 <= 496.786611, the verdict is **PASS**.

Both examples become pinned tests, like `CalSoft/test_validation_example.py`.

### 6.3 Ansur's own verdict

Ansur's Pass/Fail for every step is stored next to Cirqen's verdict. **The certificate shows Cirqen's guard-banded verdict**, the same rule as manual calibrations. Ansur's status appears in the annex.

If the two disagree (typically Ansur *Pass* where Cirqen says *INDETERMINATE* because the reading sits close to the limit), the reviewer sees a highlighted row and must acknowledge it before approving.

### 6.4 The minimum-readings rule

Manual calibrations need at least 3 readings per point (R4). An Ansur session follows an **"instrument rule"** instead: one reading per point, as the Ansur template defines. The source is recorded on the session (`source = ansur`) and printed on the certificate. The validation pack gains a requirement for this so an auditor sees it was a deliberate decision.

## 7. Approval and the certificate

The flow is unchanged: `pending` -> reviewer -> `approved_pending_certificate` -> HQ number -> frozen PDF.

- **Performer** is the Cirqen user who pressed *Start with Ansur*. The Ansur operator name from the record is also shown. If the two differ, the reviewer sees a flag.
- **Reviewer rule** stays: never the performer (R5a).
- **The certificate** carries Cirqen's readings table, uncertainty and verdict, plus the line *"Measured with Fluke Ansur 3.1.4, template Defibrillator.mtt, analyser <model> <serial> (cal. due <date>)"*.
- **The Ansur detailed PDF** is appended as an annex inside the frozen issued copy, so the one SHA-256 fingerprint covers both. The `.mtr` hash is stored in the audit trail.
- **Certificate-number speed** is the same fast path as manual sessions (LISTEN/NOTIFY), so the number arrives in seconds.

## 8. What changes in Cirqen

| Area | Change | Where |
|---|---|---|
| Models | `CalibrationSession.source` (manual / ansur); `CalibrationParameter.limit_type` (two-sided / upper / lower) | `CalSoft/models.py` + migration |
| Models | `AnsurJob` (equipment, schedule, job number, status, job/record/PDF paths, hashes, error, timestamps), with at most one open job per equipment | new, `CalSoft/models.py` |
| Models | `AnsurTemplateMap` (procedure -> `.mtt`); on `CalibrationParameter`: `ansur_step`, analyser accuracy (% of reading, fixed part) and resolution | new |
| Maths | One-sided `guarded_decision`; single-reading uncertainty budget | `CalSoft/utils.py` (re-validation) |
| Connector | `jobfile.py` (writer), `launcher.py`, `watcher.py`, `parser.py` (`.mtr` -> plain data), `importer.py` (data -> session + readings) | new package `CalSoft/ansur/` |
| Views | `start_ansur` (POST), `ansur_job_status` (GET, JSON), `ansur_import` (manual fallback upload), Ansur connection settings | `CalSoft/view_modules/ansur.py`, `CalSoft/urls.py` |
| Page | *Start with Ansur* button and status panel | `templates/Calibrition/calibration.html`, `static/calibrition/ansur_start.js` |
| Review | Disagreement highlight; Ansur annex link | pending-sessions partial |
| PDF | Source line; append annex into the issued copy | `CalSoft/pdf_generators/` (re-validation) |
| Background | Watcher loop in the desktop worker | desktop app worker |
| Sync | New tables added to the sync table list; the `.mtr` and PDF travel with the issued certificate | `sync/`, config |
| Tests | Parser against real sample records; job-file writer; the two worked examples; import happy path and each refusal; the page end-to-end with a fake `Ansur.exe` | `CalSoft/ansur/tests/`, `e2e/` |
| Validation | New R17–R20 (below); pack re-issued; release marked **re-validation required** | `docs/validation/ISO17025_VALIDATION.md` |

**New validation requirements:**

- **R17.** A record is imported only when job number, serial, template and analysers all match, and it is not aborted or incomplete.
- **R18.** One-sided and single-reading verdicts, pinned by the two worked examples.
- **R19.** The imported record and Ansur PDF are stored unchanged with SHA-256.
- **R20.** An Ansur session cannot be approved by its performer, and disagreements must be acknowledged.

## 9. Settings: Ansur connection

For the HOD or an admin only.

- **Ansur program path.** Auto-detected, with a *Test connection* button (runs Ansur's version check or opens and closes it).
- **Folders.** `jobs`, `results`, `archive`, `quarantine`, `templates`. The default is `C:\CirqenAnsur\...` on the **local disk**, not a network share.
- **Template map.** Procedure -> `.mtt` file, with the Ansur standard and service event. A procedure with no mapping does not show the Ansur button.
- **Ansur steps.** Per parameter of each linked procedure: the Ansur step name, how it is judged, and the analyser's accuracy and resolution.
- **Behaviour.** Delete job files after import (yes/no); keep the archive for N years (default: the record retention in the validation pack).

## 10. Phases: the way to follow

Do them **in order**. Phase 0 is not optional: every later phase is built and tested against the real files it collects.

| Phase | Work | Exit when | Weeks |
|---|---|---|---|
| **0. Samples** | On the hospital PC: collect 10–20 `.mtr` records with their Ansur PDFs (defib, ECG simulator, electrical safety, infusion, NIBP), the `.mtt` templates, and the analyser datasheets and certificates. Confirm the section 12 items. | Every section 12 question answered in writing | 1 |
| **1. Maths** | `limit_type`, one-sided verdict, single-reading budget, analyser accuracy per parameter; pinned tests for the two worked examples | Tests green; the lead agrees with the numbers | 1–2 |
| **2. Parser and import** | `parser.py` + `importer.py`; manual **Import Ansur record** upload (the fallback) working end-to-end to an approved certificate | Every Phase 0 sample imports correctly, or is refused for the right reason | 2 |
| **3. Start with Ansur** | Job-file writer, launcher, watcher, status panel, settings page, fake-Ansur e2e test | A technician runs a real test from the button with no typing | 2–3 |
| **4. Extras** | Pass/Fail steps into the job-card checklist; PPM auto-close; trend of Ansur results per device | Agreed with the calibration-centre lead | 1–2 |
| **5. Pilot** | 2 weeks **silent**: technicians use Ansur as normal, Cirqen imports alongside, and we compare. Then 2 weeks **live** on one bench | Zero mismatches in the silent run; validation pack signed | 4 |

**Total: about 8–11 weeks of build, plus the pilot.** Phase 2 alone already saves most of the typing, so it can go live early while Phase 3 is built.

## 11. Making it work well

**On the PC:**

- **One PC, one Windows user per technician.** Cirqen runs in the same user session as Ansur. Windows 10 or 11 with at least 8 GB RAM; Ansur needs .NET 3.5 enabled.
- **Local folders only.** Keep `C:\CirqenAnsur` on the local disk. Network shares cause half-written files and lock errors.
- **Antivirus.** Add `C:\CirqenAnsur` to the antivirus scan exclusions, or scanning will hold files open and delay pick-up.
- **Clock.** Windows time on (it usually is), so Ansur's record time and Cirqen's session time agree.
- **Restrict access off.** Leave Ansur's *Restrict access* option off for records; otherwise Cirqen cannot read them. If the hospital requires it, we need Fluke's guidance first.
- **Backups.** Include `C:\CirqenAnsur\archive`, although Cirqen also keeps its own copy of every imported record.

**In Ansur:**

- **Custom field.** Add the `Cirqen Job` custom DUT field to each template once, then treat templates as controlled documents. A changed template is a new version in the template map, not an overwrite.
- **Serial numbers.** Keep them identical in Ansur and Cirqen; the job file enforces this from now on.

**In the workflow:**

- **Start every Ansur test from Cirqen.** Keep the import upload for exceptions, not as the normal path.
- **Train the reviewers.** They need to know what an INDETERMINATE-versus-Ansur-Pass disagreement means before the pilot.
- **Order of work.** Do not switch off manual calibration. Some devices have no Ansur template, and the manual path stays the reference.

## 12. Confirm on the real PC (Phase 0 checklist)

- [ ] Exact command line for starting Ansur with a job file, and that `ReadOnly="True"` locks the DUT fields.
- [ ] What happens when Ansur is already open and a second job is launched.
- [ ] Whether Ansur honours `OutputDir` and `ResultFile` (record lands where and as named).
- [ ] Where the measurements sit in the record (`PlugInData` layout per plug-in: value, unit, limits, status). This is the one part the manual does not document.
- [ ] `Ansur.exe /f <record> /h` produces the PDF with no window.
- [ ] Whether a record saved with an e-signature is still readable (not encrypted).
- [ ] Whether a custom DUT field can be added to the site's templates without Fluke.
- [ ] Analyser models, serials and certificates, and whether they are all in the Cirqen standards register.
- [ ] Ansur version on the PC (3.1.4) and Windows version.

## 13. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The job-file switch is different or not supported in 3.1.4 | Button cannot pre-fill Ansur | Section 4 fallback (typed job number); the import path still gives the certificate |
| `PlugInData` differs per plug-in | Parser misses values | One small adapter per plug-in, built from the Phase 0 samples; unknown layouts are refused rather than guessed |
| Ansur upgrade changes the record format | Imports refused | The parser checks the `Version` attribute; the refusal is visible; samples are re-collected before upgrading |
| Analyser specs entered wrongly | Wrong uncertainty | Specs reviewed and signed by the lead; pinned examples; TUR warning below 4:1 |
| Technician edits the serial in Ansur | Wrong device on the certificate | `ReadOnly="True"` plus the serial check refuses the record |
| Record encrypted by Restrict access | Cannot read it | Keep the option off; if required, ask Fluke first |
| Ansur and Cirqen verdicts disagree | Confusion at review | Both shown; acknowledgement required; the rule is written in the validation pack |

## 14. What we need from the hospital

1. Remote or on-site access to the Ansur PC for Phase 0 (about half a day), with a biomed technician present.
2. The sample records, templates and analyser documents listed in Phase 0.
3. Permission to add the `Cirqen Job` field to the Ansur templates.
4. The calibration-centre lead's time to review the maths in Phase 1 and to sign the validation pack after the pilot.
5. IT: a local folder on the PC, an antivirus exclusion for it, and confirmation that Cirqen may start programs as the logged-in user.
