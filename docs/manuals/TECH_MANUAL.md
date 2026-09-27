# Cirqen manual: technologists and engineers

## 1. First sign-in

Sign in with the one-time password from your HOD, choose your own password,
and draw your signature. You will be signed out after 30 minutes without
activity; your work in progress on a form is not saved, so save as you go.

## 2. Your dashboard

Shows your workshop's open work orders, PPM due this month, calibrations due,
and equipment out of service.

## 3. Work orders (repairs)

1. **Work Orders, Create Work Order**. Pick the equipment (or scan its QR
   label with a phone: the machine page has **Start work order**, which
   fills the equipment in for you).
2. Record the fault, what you did, parts used and times. Sign.
3. Submit. It goes to **Waiting Work Orders** until the department's
   in-charge approves it.
4. Once approved it moves to **Approved Work Orders**, where you can download
   the PDF. If declined, it is in **Declined Work Orders** with the reason;
   correct it and resubmit.

**Checklists**: some equipment types have a checklist that appears on the
work order. Complete every item.

## 4. PPM (planned maintenance)

**PPM Schedules** shows what is due each month for your workshop. Open an
item, record the checks, sign and submit. Overdue items show in red and
appear on the HOD's report.

## 5. Calibration (calibration centre only)

Before you start, check the reference standards you will use are in date
(**Calibration, Standards, View Standards**). Cirqen will not let you use an
overdue standard.

1. **Calibration, Perform Calibration**: pick the device.
2. Choose the procedure. Enter the environment (temperature, humidity,
   pressure), the device resolution, and the standards used.
3. Enter the readings at each set value. You need at least the number of
   readings the procedure asks for (never fewer than 3). Cirqen shows the
   mean, error, uncertainty and verdict as you type.
4. Submit. The session goes to **Pending Approval**.
5. Another calibration-centre reviewer or the HOD approves it. **You cannot
   approve a calibration you performed yourself.**
6. Cirqen HQ then gives it a certificate number. With no internet, the
   certificate waits for its number and is issued when the link returns.
7. **Certificates**: download the certificate. Once issued it never changes;
   every download is the same file. Each has a QR code anyone can scan to
   check it is genuine.

**Verdicts.** PASS: within tolerance even allowing for the uncertainty.
FAIL: outside it even allowing for the uncertainty. INDETERMINATE: too close
to the limit for this measurement to decide; repeat with better standards or
escalate to the HOD. A TUR below 4:1 is flagged on the certificate.

**Procedures and standards** (reviewers): create procedures (parameters, set
values, tolerances, number of readings) and add standards with their
certificate details, or import them from a spreadsheet.

## 6. Other tools

- **Machine Reports**: history, faults and downtime for any machine.
- **Parts & Tools**: request accessories and spares; track stock.
- **QR labels** (menu, Assets): print labels for equipment. Scanning a label
  opens the machine's page: status, next PPM and calibration, history.
- **Report Hub**: reports for your workshop.

## 7. Working offline

Cirqen keeps working without the internet. Everything is saved on the site
and sent to HQ when the link returns. Only certificate numbers wait for HQ.
