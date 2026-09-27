# Data protection (Kenya Data Protection Act, 2019): compliance pack

> **Draft.** Prepared from how the software actually handles data, as a
> starting point. It is not legal advice. Have a Kenyan advocate who
> practises data protection review it before any hospital signs, and before
> registering with the ODPC.

## 1. Roles

| Party | Role under the Act | Why |
|---|---|---|
| The hospital (county or private) | **Data controller** | Decides whose data goes in and why: its staff, its suppliers |
| Cirqen Labs | **Data processor** for the hospital's data held at HQ; **data controller** for its own customer and billing records | Hosts HQ, provides support, under the hospital's instructions |
| Hosting providers used for HQ (application host, managed PostgreSQL, error monitoring, email/SMS gateways) | Sub-processors | Listed in the data processing agreement, annex 2 |

## 2. What personal data Cirqen holds

Cirqen manages equipment, not patients. It holds **no patient data** and the
data processing agreement forbids entering any (for example, patient names
in a fault description).

| Data | Whose | Where it lives | Purpose |
|---|---|---|---|
| Name, username, email, phone number, role, workshop/department | Hospital staff users | Site database; copied to HQ by sync | Sign-in, assigning and approving work, notifications |
| Handwritten signature image | Staff who approve or sign certificates | Site database, HQ | Signing work orders and certificates |
| Password hash, two-factor secret | Staff users | Site database only | Sign-in security |
| Sign-in events, IP address, browser | Staff users | Site database (`users` security log, `audit_log`) | Security, audit trail required for ISO/IEC 17025 |
| Names on work orders, approvals, calibrations | Staff users | Site database, HQ | Record of who did what (legal and quality record) |
| Supplier contact name, phone, email | Supplier staff | Site database, HQ | Service contracts, reorders |
| Error reports | Staff (indirectly) | Error monitoring service, only if the site turns it on | Fixing faults. Sent with `send_default_pii=False`: no usernames, IPs or cookies |

No sensitive personal data (health, biometric for identification, etc.)
under section 2 of the Act is intended to be processed. A signature image is
used as a mark of approval, not to identify anyone biometrically. The
advocate should confirm this reading.

## 3. Checklist for going live

| # | Action | Who | Status |
|---|---|---|---|
| 1 | Register Cirqen Labs with the ODPC as a data **processor** (and controller for its own records); renew as required | Cirqen Labs | To do |
| 2 | Confirm each hospital is registered as a controller (most public hospitals already are) | Hospital | Per site |
| 3 | Sign the data processing agreement (`DPA_TEMPLATE.md`) with each hospital before its data reaches HQ | Both | Per site |
| 4 | Give staff the privacy notice (`PRIVACY_NOTICE.md`) at first sign-in (link from the sign-in page) | Cirqen Labs / hospital | To do |
| 5 | Data protection impact assessment (section 31) for HQ, since it combines many hospitals' staff data | Cirqen Labs | To do |
| 6 | Cross-border transfer: HQ's hosting is outside Kenya. Record the safeguards (section 48–50, and the 2021 General Regulations): provider data processing terms, encryption in transit and at rest, and the hospital's consent in the DPA. Consider Kenyan or African-region hosting if a county requires it | Cirqen Labs | To do: confirm the hosting regions |
| 7 | Retention schedule (section 4 below) agreed and set | Both | To do |
| 8 | Breach procedure (section 5 below) tested once before the pilot | Cirqen Labs | To do |
| 9 | Name a data protection contact at Cirqen Labs; put it in the privacy notice | Cirqen Labs | To do |

## 4. Retention

| Record | Keep for | How it ends |
|---|---|---|
| Calibration records, certificates, audit trail | Life of the equipment + 1 calibration interval, at least 5 years (confirm against the hospital's quality manual and records policy) | Archived with the equipment record |
| Work orders, PPM records | Life of the equipment + 2 years | As above |
| Staff user accounts | While employed; deactivated (not deleted) when they leave, because their name is on quality records | HOD deactivates the account |
| Sign-in security log | 12 months | Deleted automatically each night at the site (`CIRQEN_SECURITY_LOG_DAYS`) and daily at HQ (`SECURITY_LOG_DAYS`); default 365 days |
| Backups | Desktop 14 copies; server 30 days (`CIRQEN_BACKUP_KEEP`) | Rotated automatically |
| HQ copy after a contract ends | Returned as an export, then deleted within 90 days | Written confirmation to the hospital |

Staff who ask for erasure: their account is deactivated and contact details
removed; their name stays on signed quality records, which the Act permits
where retention is required by law or for legal claims (section 40). The
advocate should confirm.

## 5. Breach notification

The Act (section 43) requires the controller to notify the ODPC within **72
hours** of becoming aware of a breach, and the processor to tell the
controller **without delay**.

1. Anyone at Cirqen Labs who suspects a breach tells the on-call engineer
   and the data protection contact at once (see `SUPPORT.md`, severity 1).
2. Contain: revoke keys (`HQ_ADMIN_TOKEN`, API keys, per-site sync keys),
   rotate database credentials, block the source.
3. Within **24 hours**, Cirqen Labs tells each affected hospital in writing:
   what happened, what data, how many people, what has been done, a contact.
4. The hospital (controller) notifies the ODPC within 72 hours of learning of
   it, with Cirqen Labs' help; and tells affected staff where there is a real
   risk of harm.
5. Record every incident, notified or not, in the breach register, with the
   decision and reasons.

## 6. Security measures (for the DPA annex)

- Encryption in transit: HTTPS to HQ; server mode serves HTTPS only, with HSTS.
- Access: role-based scoping (people see only their workshop or department),
  unique accounts, lockout after repeated failed sign-ins, idle sign-out,
  two-factor sign-in (compulsory for HODs where the site sets it).
- Per-site API keys for sync; an admin token for HQ administration.
- Audit trail of changes and sign-ins.
- Daily backups with a readability check; off-disk copy on servers.
- Signed software updates only.
- Error reports carry no personal data by default.
