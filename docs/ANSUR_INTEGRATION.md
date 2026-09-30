# Fluke Ansur: setting up and using Start with Ansur

Cirqen and Ansur run on the same Windows PC. Cirqen writes a work order and
opens Ansur with the device filled in; the technician runs the test in
Ansur as usual; Cirqen picks up the record Ansur saves, checks it, computes
its own uncertainty and verdict, and the session goes for approval like a
manual one. The design and its reasons are in
`review/12_ANSUR_PERFORM_CALIBRATION_PLAN.pdf`.

## One-time set-up (calibration centre or HOD, on the Ansur PC)

Open **Ansur connection** (Calibration menu; for the HOD, the sidebar) and
work down the status list.

1. **Ansur program.** Press *Find Ansur*, or paste the path to Ansur's `.exe`.
2. **Work folders.** Keep `C:\CirqenAnsur` (local disk, not a share) and
   press *Create folders*. Add it to the antivirus exclusions and the backup.
3. **Templates.** Copy each Ansur template (`.mtt`) the site uses into
   `C:\CirqenAnsur\templates`.
4. **Procedures.** Two ways, under *Procedures run with Ansur*:
   - **Create a procedure from a template** (quickest). Pick the template,
     name the procedure and choose the analyser. Cirqen reads the template's
     test steps and limits and creates the procedure, its parameters and set
     values, already linked. Steps without a limit (checklists) stay Ansur's
     Pass/Fail checks. If the template's layout gives no limits, Cirqen says
     so; link it by hand instead.
   - **Link an existing procedure.** Pick the Cirqen procedure and its
     template. Its set values and tolerances must be the ones the template
     uses; sub-parameters are not supported.
5. **Finish each parameter.** For each linked procedure, check or fill in the
   Ansur step name (exactly as Ansur prints it), how it is judged (± tolerance,
   at most, or at least the set value), the analyser's accuracy (± % of
   reading and ± fixed part) and resolution from its datasheet, and the
   **reference uncertainty** (k=2) from the analyser's calibration
   certificate. A procedure is not offered until all of these are in.
6. **Analysers.** Every analyser Ansur lists in a record must be in the
   standards register with a current calibration.
7. Tick **Show Start with Ansur** and save. It will not switch on until every
   check is green.

In Ansur, once (Users Manual FBC-0001 Rev. 6):

- Add the device field **Cirqen Job**: *Tools > Options > Ansur
  Preferences > DUT*, double-click the last field and type the name (2-10).
  Cirqen fills it in through the work order and matches records by it.
- Turn **Restrict Access** off: *Tools > Options > General* (4-35).
  Protected records cannot be read.
- Sign in with **Disable Electronic Signature** ticked (2-8) until a record
  saved with a signature has been tried here. A record Cirqen cannot read is
  refused with the settings to change.

### Confirm once at the site

Ansur's manual does not print everything Cirqen relies on. On the real PC:

- Press *Start with Ansur* once. The work order follows the layout in the
  manual (4-37), and Ansur is started with it as the parameter, as the manual
  describes. If Ansur still opens without the device filled in, try
  `/r "{job}"` in **Start Ansur with** (Advanced; Appendix B).
- Save a test record and check it is imported. If it is refused with "No
  measured results were found", send a sample record to Cirqen Labs: that
  plug-in stores its results in a layout the reader does not know yet, and
  records are refused rather than guessed.
- Check Ansur's PDF is attached (Advanced: **Make Ansur's PDF with**,
  default `/f "{record}" /h`).

## Daily use (technician)

1. Perform Calibration: choose the equipment, schedule and a linked procedure,
   and enter the room conditions.
2. Press **Start with Ansur**. Ansur opens with the device filled in.
3. Run the test in Ansur and save it. The panel moves to *Waiting for
   approval* within a few seconds.
4. If the record is refused, the panel says why (for example a serial
   mismatch or an unregistered analyser). Fix it, run and save again: the same
   job accepts a corrected record. *Open Ansur again* re-opens the job;
   *Import a record by hand* takes a saved `.mtr` file instead.

## Review (calibration centre or HOD)

Ansur's Pass/Fail-only steps (visual inspection, alarms) are listed in the
review and printed on the certificate as "Checks recorded in Ansur"; a failed
check fails the calibration.

The review and the session page show the Ansur job, operator, Ansur's Pass/Fail beside Cirqen's
verdict for every point, and a link to Ansur's PDF. Where the two verdicts
differ (usually a reading close to a limit, which Cirqen calls
INDETERMINATE), the row is highlighted and approving needs a tick to
confirm it was reviewed. The certificate states the readings were taken
with Ansur and carries Ansur's report as Annex A.

The Ansur job, the record copy and Ansur's PDF stay on the Ansur PC. The
session, its readings, Ansur's verdicts, the checks and the job number sync,
so another PC shows everything except the PDF, and says where it is. Review
and issue Ansur sessions on the Ansur PC to get Annex A.

Calibration-centre staff can reopen, cancel or import for a colleague's job.

## Where things go

| Folder | Holds |
|---|---|
| `jobs` | Work orders; removed once imported (setting) |
| `results` | Where Ansur saves records; emptied as they are read |
| `archive\<year>` | Imported records and Ansur PDFs; kept for the years set |
| `quarantine` | Refused records, each with a `.reason.txt` |
| `templates` | The controlled Ansur templates |

Cirqen also keeps its own copy of every imported record and PDF, with
SHA-256 fingerprints, so the archive folder is not the only copy.

## Deploying this release

Deploy HQ with `migrations/2026_add_ansur_calibration.sql` before any site
updates; HQ applies it on boot. It adds the synced Ansur columns, with
defaults so older desktops can still upload. On the desktops, CalSoft 0012
gives the same columns database defaults. Sites need `pypdf` (in
`requirements.txt`). The release is marked re-validation required; section
5.1 of the validation pack is the worked example to repeat.
