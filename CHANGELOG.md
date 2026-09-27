# Changelog

Changes that hospitals will notice, newest first. Releases marked
**re-validation required** change something in section 7 of
`docs/validation/ISO17025_VALIDATION.md`: repeat the worked example before
using the new version for certificates.

## Unreleased: re-validation required

### Calibration
- A calibration cannot be saved with fewer readings than the procedure asks
  for (never fewer than 3); the message names the rows that are short.
- An issued certificate is stored exactly as issued, with a fingerprint;
  every later download is the same file, and a changed file is refused.
- The person who performed a calibration can no longer approve it; another
  reviewer or the HOD must (ISO/IEC 17025 clause 7.8.1.2).
- Certificate numbers always come from HQ, so two sites can never issue the
  same number. A clash made offline by an older version is repaired
  automatically and recorded in the audit trail.
- Certificate QR codes open HQ's public check page.
- Certificate numbers arrive in seconds instead of up to a minute or more:
  approving wakes the sync agent at once, HQ allocates in the same request
  and returns the number, and the approval page shows it without a reload.
- Certificate prefix is set per site.
- One calibration schedule list (the duplicate schedule table was retired).
- Bulk certificate download no longer uses a GPL-licensed library.

### Work orders and maintenance
- In-charges can approve work orders from a phone, signing with a finger
  (Work Orders, Phone approval).
- New Assets menu: maintenance KPIs (uptime, MTTR, MTBF, PPM compliance),
  service contracts (with the Inventory suppliers), stock alerts with
  reorder levels, and printable QR labels that open each machine's page
  (status, warranty, failure risk, history).
- Alerts (in-app, email, SMS where set up) for reference standards due,
  service contracts ending and low stock; a monthly PDF report emailed to
  each HOD. PPM, calibration and warranties are in the 1.6.0 daily digest.
- Large reports are built in the background.

### Security
- Sign-out after 30 minutes without activity (`CIRQEN_IDLE_MINUTES`).
- Two-factor sign-in with an authenticator app; can be made compulsory for
  HODs (`CIRQEN_REQUIRE_HOD_2FA=1`). `reset_two_factor` for lost devices.
- Five wrong passwords lock an account for 15 minutes.
- A new installation creates its own HOD account with a one-time password;
  there is no shared default account.
- Sites reach HQ only through its API; no HQ database credentials are stored
  at sites.

### Operations
- Hospital server mode: one install on a hospital server, used from browsers
  (`docs/SERVER_MODE.md`).
- Daily backups are checked for readability; servers keep 30 days and a
  second copy off the server's disk.
- Per-site branding: hospital name, address and logo on documents.
- Bulk user import from a spreadsheet.
- Sign-in security events are deleted after 365 days
  (`CIRQEN_SECURITY_LOG_DAYS`; 0 keeps them).

### Upgrade notes
- HQ must be updated first: it now creates the tables and columns sites
  used to create with `migrate --database=hq`
  (`migrations/2026_add_scheduling_warranty_checklists.sql`). Add
  `public.assets_servicecontract` to its `TABLES`, remove
  `public.CalSoft_calibrationschedule`.

## 1.6.0 (2026-09-25)
- Scheduling plans: each workshop plans its own PPM programme and the
  calibration centre plans calibration hospital-wide; schedule history is
  kept and no equipment is left unscheduled.
- Warranties and suppliers in Inventory; work-order checklists; daily
  email digests; failure-risk prediction in Machine Reports.
- One sidebar entry per module, with its pages as tabs; HODs see PPM and
  calibration schedules read-only.
- Sign in with username or email.

## 1.5.5 (2026-09-24)
- "Job cards" are now called "Work Orders"; work orders have Remarks.
- All times shown in East Africa Time.
- Linux desktop build as an AppImage with its own PostgreSQL.
- Sync: a fresh install can start up even when HQ has extra columns.

## 1.5.2 (2026-09-21)
- Updates no longer loop after a failed update.
- Version numbers aligned everywhere; the build fails if they drift.
