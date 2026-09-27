# Cirqen manual: head of department (HOD)

## 1. First sign-in

1. Sign in with the username and one-time password you were given (on a
   desktop install it is in `first_login.txt` in the Cirqen data folder; on a
   server, IT gives it to you).
2. Choose a new password and draw your signature. No other page opens until
   this is done. Your signature goes on the work orders and certificates you
   approve.
3. Turn on two-factor sign-in: menu, **Two-factor sign-in**. Scan the code
   with an authenticator app (Google Authenticator, Microsoft Authenticator)
   and type the 6-digit number. Your site may make this compulsory for HODs.

## 2. Set up the site

1. **Site details** (menu, Administration): hospital name, address, contact,
   logo. These appear on certificates, work orders and reports.
2. **Manage workshop**: create each workshop and say what it is:
   - *maintenance* workshops repair and service equipment;
   - a *calibration centre* calibrates and issues certificates.
3. Create the departments (wards and units) and link each to the workshop
   that looks after it.
4. **Inventory**: add equipment one at a time, or import a spreadsheet. For
   warranty and life-cycle tracking fill in purchase date, warranty end,
   purchase cost and expected life.

## 3. Users

Menu, **Manage Users**.

- **Add one user**: name, email, phone, role (Tech or NIC), workshop (Tech)
  or department (NIC). Cirqen makes a username and temporary password and
  emails them to the person. At first sign-in they set their own password
  and signature. Check the email address carefully; if it cannot be
  delivered, reset the password and tell them the new one privately.
- **Import many users**: download the template, fill it in, upload. Cirqen
  shows a preview and lists any rows with problems before anything is
  saved. When you confirm, each person is emailed their username and
  temporary password.
- **Someone leaves**: deactivate the account. Do not delete it: their name
  stays on the records they signed, as quality rules require.
- **Forgotten password**: reset it from Manage Users; they get a new
  one-time password.
- **Lost authenticator** (two-factor): they can sign in with a recovery
  code. With none left, IT runs `reset_two_factor` (see OPERATIONS.md).

## 4. Oversight

- **Dashboard**: equipment status, open work orders, overdue PPM and
  calibration, across all workshops.
- **Maintenance KPIs** (menu, Assets): uptime, mean time to repair, mean
  time between failures, PPM compliance, and each machine's **risk score**
  with the reasons (age, repeat faults, overdue service, no warranty or
  contract). Use it to decide what to replace or put under contract.
- **Contracts & warranties**: service contracts with suppliers, and
  warranties about to expire. Cirqen warns daily from 60 days before a
  contract or warranty ends, and 30 days before a reference standard's
  calibration is due.
- **Stock alerts**: accessories and spares at or below their reorder level,
  with the supplier to order from.
- **Audit Log**: who changed what and when, including sign-ins.
- **Calibration, Pending Approval**: you can approve calibrations (but not
  ones you performed yourself).

## 5. Reports

- **Monthly report**: emailed to you on the 1st of each month (PPM
  compliance, calibration status, downtime, costs, top-risk machines).
- **Report Hub** and **Settings, Reports**: category and department reports
  as PDF. Large reports are built in the background; Cirqen tells you when
  the file is ready to download.

## 6. Notifications

Cirqen sends in-app notices and, where set up, email and SMS for: PPM and
calibration coming due or overdue, warranties and contracts expiring, stock
below reorder level, and work orders waiting for approval. Keep users'
email and phone numbers up to date for these to reach them.

## 7. System (IT or HOD with staff rights)

- **HQ Connection**: whether the site is linked to Cirqen HQ and when it last
  synced. The site keeps working offline; it catches up when the link
  returns.
- **Updates**: installed version and available updates. Updates are signed by
  Cirqen Labs; Cirqen refuses anything else.
- Backups run every day. See [../OPERATIONS.md](../OPERATIONS.md).
