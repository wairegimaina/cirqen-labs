# Fluke Ansur: setting up and using Start with Ansur

Cirqen and Ansur run on the same Windows PC. Cirqen writes a work order and
opens Ansur with the device filled in; the technician runs the test in
Ansur as usual; Cirqen picks up the record Ansur saves, checks it, computes
its own uncertainty and verdict, and the session goes for approval like a
manual one. The design and its reasons are in
`review/12_ANSUR_PERFORM_CALIBRATION_PLAN.pdf`.

## One-time set-up (HOD, on the Ansur PC)

Open **Ansur connection** in the sidebar and work down the status list.

1. **Ansur program.** Press *Find Ansur*, or paste the path to Ansur's `.exe`.
2. **Work folders.** Keep `C:\CirqenAnsur` (local disk, not a share) and
   press *Create folders*. Add it to the antivirus exclusions and the backup.
3. **Templates.** Copy each Ansur template (`.mtt`) the site uses into
   `C:\CirqenAnsur\templates`. In Ansur, add a custom device field named
   **Cirqen Job** to each of them.
4. **Link procedures.** Under *Procedures run with Ansur*, pick the Cirqen
   procedure and its template. The procedure's set values and tolerances must
   be the ones the Ansur template uses; sub-parameters are not supported.
5. **Link parameters.** For each linked procedure, fill in per parameter:
   the Ansur step name exactly as Ansur prints it, how it is judged (± tolerance,
   at most, or at least the set value), and the analyser's accuracy
   (± % of reading and ± fixed part) and resolution from its datasheet. The
   analyser's certificate uncertainty is the parameter's reference
   uncertainty, and its serial the reference standard, as for manual work.
6. **Analysers.** Every analyser Ansur lists in a record must be in the
   standards register with a current calibration.
7. Tick **Show Start with Ansur** and save. It will not switch on until every
   check is green.

In Ansur, leave **Restrict access** off for test records, or Cirqen cannot
read them.

### Confirm once at the site

Ansur's manual does not print everything Cirqen relies on. On the real PC:

- Press *Start with Ansur* once. If Ansur opens without the device filled
  in, change **Start Ansur with** (Advanced) to the form Ansur accepts; the
  work order file is `{job}`.
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

The review shows the Ansur job, operator, Ansur's Pass/Fail beside Cirqen's
verdict for every point, and a link to Ansur's PDF. Where the two verdicts
differ (usually a reading close to a limit, which Cirqen calls
INDETERMINATE), the row is highlighted and approving needs a tick to
confirm it was reviewed. The certificate states the readings were taken
with Ansur and carries Ansur's report as Annex A.

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

Apply the HQ migration `2026_add_ansur_calibration.sql` before any site
updates: it adds the new synced columns. Sites need `pypdf` (in
`requirements.txt`). The release is marked re-validation required; section
5.1 of the validation pack is the worked example to repeat.
