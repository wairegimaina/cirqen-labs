# Pilot plan (Phase 5)

One Level 5 or 6 hospital, 12 weeks, before selling more widely. The aim is
to prove, with real users and real equipment, that Cirqen is safe to depend
on, and to find what the tests did not.

## 1. Before the pilot starts (go/no-go 1)

All must be true. Owner in brackets.

| # | Item | Owner |
|---|---|---|
| 1 | HQ on a paid, always-on plan; `WEB_CONCURRENCY=2`, `POSTGRES_POOL_MAX=10`, `HQ_ADMIN_TOKEN` set; new asset tables in `TABLES` | Cirqen Labs |
| 2 | All credentials previously committed to git rotated (database, API keys, tokens) | Cirqen Labs |
| 3 | Error monitoring on (HQ and site), alerts reach the on-call phone | Cirqen Labs |
| 4 | ODPC registration done or applied for; DPA signed with the hospital | Cirqen Labs + hospital |
| 5 | Advocate has reviewed the DPA, privacy notice, EULA and service terms | Cirqen Labs |
| 6 | Hospital's quality manager has decided the error sign convention and the coverage-factor rule (validation pack, section 9) | Hospital |
| 7 | Validation pack signed on the installed copy (worked example matches) | Hospital calibration centre |
| 8 | Server installed (or desktops for a smaller site); second backup copy location set; first restore drill passed | Hospital IT + Cirqen Labs |
| 9 | Two engineers trained on support; on-call rota published | Cirqen Labs |
| 10 | Security-log retention prune in place (see `docs/legal/DATA_PROTECTION.md`) | Cirqen Labs |

## 2. Timeline

| Week | What |
|---|---|
| 0 | Install, import equipment and users, site details and logo; train the HOD and workshop leads (half day) |
| 1 | Train technologists (half day per workshop) and in-charges (one hour, on their phones) |
| 1–2 | **Parallel running**: paper and Cirqen side by side for work orders and PPM; calibration certificates still on the old system |
| 3 | Go/no-go 2 (below). If go, Cirqen becomes the record for work orders and PPM |
| 4–5 | Calibration: first certificates from Cirqen, checked against the old method for the first 20 |
| 6 | Go/no-go 3: Cirqen becomes the record for calibration |
| 6–12 | Normal use; weekly check-in with the HOD; one planned update using staged rollout |
| 8 | Restore drill with the hospital's IT; test of the breach procedure (tabletop) |
| 12 | Review and decision (section 5) |

## 3. Go/no-go criteria

**Go/no-go 2 (week 3), switching work orders and PPM to Cirqen**

- No severity 1 incident in weeks 1–2; any severity 2 fixed.
- Every work order raised on paper in weeks 1–2 is also in Cirqen and
  matches (spot check of 30).
- At least 80% of technologists and in-charges have signed in and completed
  one task.
- Backups taken every day and the readability check passing.

**Go/no-go 3 (week 6), switching calibration certificates to Cirqen**

- The first 20 Cirqen certificates agree with the old method (mean, error,
  uncertainty within rounding; verdict identical, or any difference is the
  guard-banded INDETERMINATE and explained).
- Every certificate's QR check at HQ shows the right number, device and
  date (all 20 checked).
- No duplicate certificate numbers; no certificate stuck "pending number"
  for more than 24 hours with the internet up.

**Stop at any time if**: a data breach; a wrong certificate reaches a
customer; data is lost that cannot be restored from backup. Revert to the
previous process, investigate, and re-run the relevant go/no-go.

## 4. What we measure

| Measure | Target by week 12 | Source |
|---|---|---|
| Weekly active users / accounts | ≥ 80% | Security log |
| Work orders raised in Cirqen / all work orders | ≥ 95% | HOD's count vs Cirqen |
| Median time from work order submitted to approved | Falls vs weeks 1–2 | KPIs page |
| PPM compliance | Visible and rising | KPIs page |
| Certificates issued with a correct number and QR check | 100% | HQ verify |
| Severity 1 incidents | 0 after week 3 | Support log |
| Severity 2 first response within 4 business hours | 100% | Support log |
| Sync backlog at any site | Under 1 hour, 95% of the time | `/api/sync/ops` |
| Page load (median) on the hospital network | Under 1 second | Spot checks |
| HOD and user satisfaction (1–5) | ≥ 4 | Week 6 and 12 survey |

## 5. End-of-pilot decision (week 12)

- **Proceed** to more hospitals if all section 4 targets are met, or any miss
  has a fix released and verified.
- **Extend** by 6 weeks if a target is missed with a fix in progress.
- **Stop and rework** if a stop condition occurred, or certificate accuracy
  or numbering failed.

Write up: the measures, incidents and their fixes, what users asked for, and
what changes before the next hospitals. Ask the hospital for a reference
if it goes well.

## 6. Things only Cirqen Labs' people can do

These cannot be done in the software:

- Register with the ODPC; engage the advocate; sign the DPA.
- Pick and sign up the pilot hospital; agree the pilot terms (free or
  discounted, in exchange for feedback and a reference).
- Hire or assign the second support engineer; run the training.
- Move HQ to an always-on plan and rotate the credentials.
