# Changelog

Changes that hospitals will notice, newest first. Releases marked
**re-validation required** change something in section 7 of
`docs/validation/ISO17025_VALIDATION.md`: repeat the worked example before
using the new version for certificates.

## Unreleased (planned 1.6.0): re-validation required

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
- In-charges can approve work orders from a phone, signing with a finger.
- Checklists per equipment type appear on work orders.
- New Assets menu: maintenance KPIs (uptime, MTTR, MTBF, PPM compliance),
  a replacement-risk score for each machine, contracts and warranties,
  stock alerts with reorder levels and suppliers, and printable QR labels
  that open each machine's page.
- Daily reminders (in-app, email, SMS where set up) for PPM and calibration
  due, standards and contracts expiring, low stock and work orders waiting
  for approval; a monthly PDF report emailed to each HOD.
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
- HQ must be updated first and `public.assets_supplier` and
  `public.assets_servicecontract` added to its `TABLES`
  (`public.CalSoft_calibrationschedule` removed).

## 1.5.5 (2026-09-24)
- "Job cards" are now called "Work Orders"; work orders have Remarks.
- All times shown in East Africa Time.
- Linux desktop build as an AppImage with its own PostgreSQL.
- Sync: a fresh install can start up even when HQ has extra columns.

## 1.5.2 (2026-09-21)
- Updates no longer loop after a failed update.
- Version numbers aligned everywhere; the build fails if they drift.
