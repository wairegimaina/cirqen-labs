# Cirqen support

How hospitals get help, and what Cirqen Labs commits to. This is the
Support Schedule the service terms (`docs/legal/SAAS_TERMS.md`) refer to.

## How to report a problem

1. Users go to their HOD or site IT first. Many questions are answered in
   `docs/manuals/FAQ.md`.
2. The HOD or site IT contacts Cirqen Labs:
   - Email: [support@cirqenlabs.example]
   - Phone / WhatsApp: [+254 ...] (severity 1 and 2: phone, always)
3. Say: hospital, what happened, when, who is affected, what was being done,
   any error message (a photo of the screen is fine). **Never send patient
   information, passwords or the contents of `cirqen.env`.**

## Hours

Monday to Friday 08:00–17:00 EAT, except Kenyan public holidays. Severity 1
is handled outside these hours by the on-call engineer.

## Severity and response times

| Severity | Meaning | Examples | First response | Target fix or workaround |
|---|---|---|---|---|
| 1 Critical | Cirqen unusable at a site, data lost or exposed, or wrong certificates being issued | Server down; nobody can sign in; suspected breach; certificate numbers or results wrong | **1 hour, any time** | 8 hours |
| 2 High | A main function broken for many users, no workaround | Work orders cannot be approved; calibrations cannot be submitted; sync stuck for over 24 h | **4 business hours** | 2 business days |
| 3 Normal | A function broken with a workaround, or one user affected | A report fails; one user cannot sign their work | 1 business day | Next release |
| 4 Low | Question, how-to, or suggestion | "How do I...", feature requests | 2 business days | Considered for the roadmap |

"First response" means a person at Cirqen Labs has read it, set the
severity, and replied with next steps. The headline **4 business-hour SLA**
is the first-response time for severity 2; severity 1 is faster, at any hour.

A suspected data breach is always severity 1 and follows the breach
procedure in `docs/legal/DATA_PROTECTION.md` (hospital told within 24 hours).

## On-call rota

| | |
|---|---|
| Who | One engineer on call per week, Monday 08:00 to Monday 08:00 EAT |
| Rota | Kept in the team calendar; at least two engineers trained before the pilot, so nobody is on call two weeks running |
| Alerts | HQ error monitoring (Sentry), `/api/sync/ops` checks, and the sync-backlog alert page the on-call engineer's phone |
| Hand-over | Monday: open incidents, anything unusual in `/api/sync/ops`, pending releases |
| Escalation | On-call engineer → lead engineer (after 1 hour on severity 1) → director (breach, or severity 1 over 4 hours) |

## What each side looks after

| Cirqen Labs | Hospital |
|---|---|
| The software, HQ, updates, certificate numbering | The server or PCs, network, power, UPS |
| Diagnosing faults, releasing fixes | The second backup copy location |
| Security of HQ | Users' accounts, deactivating leavers |
| Helping with restores | Running the quarterly restore drill (`docs/OPERATIONS.md`) |

## After an incident

For every severity 1 and 2 incident Cirqen Labs writes a short report within
5 business days: what happened, impact, cause, what was fixed, what will stop
it recurring. The hospital gets a copy.
