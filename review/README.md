# CalSoft Review

Six documents, two audiences (09 was added later). Reviewed 17 September 2026 against branch
`improvement-plan`; **revised 18 September after implementation**, so every
document now describes the code as it currently behaves.

**Where things stand: 4.6 / 10 → 8.1 / 10.** Of 44 plan items, 26 are done, 6
half-wired, 10 not started and 2 waiting on a decision. Start at
`04_PLAN_TO_9_OF_10.pdf` §0 for the item-by-item status.

## Read these

| # | Document | For | Pages |
|---|---|---|---|
| **01** | `01_CALIBRATION_MATHEMATICS.pdf` | Technicians, engineers, QA staff, auditors | 17 |
| **02** | `02_RATING_AND_COMPARISON.pdf` | Engineering leads | 9 |
| **03** | `03_VS_FLUKE_AND_BEAMEX.pdf` | Anyone comparing against commercial tools | 6 |
| **04** | `04_PLAN_TO_9_OF_10.pdf` | Whoever owns the work | 13 |
| **05** | `05_HQ_AND_SYNC_AUDIT.pdf` | Engineering leads, whoever owns sync | 6 |
| **06** | `06_SYSTEM_PLAN.pdf` | Whoever owns the work — **start here** | 4 |
| **07** | `07_CALSOFT_REFERENCE.pdf` | Anyone using or maintaining calibration | 5 |
| **08** | `08_CALSCHEDULES_REFERENCE.pdf` | Anyone planning calibration work | 4 |
| **09** | `09_HQ_CONNECTION_CONFIG.pdf` | Whoever owns sync, updates or installers | 28 |

**01 — The Mathematics of Calibration.** How every number on a certificate is
arrived at, built up from first principles. No programming content. Includes one
complete worked measurement from five raw readings to the printed line, and a
section on how to read a certificate critically.

**02 — Rating and comparison.** CalSoft scored 4.6/10 at baseline on eleven
weighted dimensions, against `calSchedules` at 8.4, `sync`+`updates` at 7.4 and
`ppms` at 5.0. §2a re-scores it at **8.1/10** after implementation and says
exactly where the remaining 1.6 sits.

**03 — Against Fluke and Beamex.** Capability matrix against Fluke Biomedical
Ansur, Fluke MET/CAL + MET/TEAM, and Beamex CMX. Six rows changed after
implementation: the decision-behaviour gap that defined this comparison is
closed, and what remains is records integrity rather than metrology. Vendor
claims are sourced to their own documentation (§8).

**04 — The plan.** Two tracks. **Track A** is five phases; **Track B** is six
platform-integrity items from 05, unscored on purpose. **§0 is the current
status**, §11 sequences both tracks, and §12 is what to do next. Start here if
you own the work.

**06 — The plan from here.** The whole system, not just CalSoft: where each
area stands, what is left in four tiers of consequence, the order to do it in,
and the two decisions currently gating four items. Supersedes 04's sequencing.

**05 — HQ server and sync audit.** The certificate authority and the conflict
resolution that decides which version of a record survives. Six findings, four
of them high severity, including a data-integrity guard that is documented,
tested, and unable to fire on the path it protects. Read this with 04.

**07 — CalSoft reference.** What the calibration module contains and how a
certificate is made, from capture through reduction, approval, issue and
rendering. §5 states what a certificate does and does not guarantee; §6 lists
the known limits.

**08 — calSchedules reference.** How work is planned, grouped and advanced:
groups, membership, month-end due dates, regrouping and the protections. **§7
lists what is not yet finished**, in full.

**09 — HQ connection configuration.** What happens when the HQ host changes, tested against the real config class: installed desktops stayed pinned to their first-run address and production ignored the URL environment variables. Rated 2.8/10. **Now fixed and re-rated 7.9/10** (§13, §15): one defaults block, four resolution layers, a migration for already-pinned machines, an *Administration → System → HQ Connection* page, an `hq_endpoint` command, and 60 tests. §14 then asks the harder question and rates the system **4.2/10 for 500 desktops** — three capacity walls that configuration work cannot lift, the first at about 25 desktops. §15.3 lists the defects a review pass found in that work. **§17–18 are the ones to read for moving the sync HQ.** The update server is a permanently fixed host, so it is the anchor that tells every desktop where the sync HQ went — **now built** (§18): a signed `/api/endpoints/` document, a desktop client that adopts only after signature, freshness, validation and a health probe, and automatic revert if the new address fails. Two scaling fixes cut idle fleet load 77% (167 → 39 req/s at 500 desktops). Ratings: moving HQ **9.0/10**, 500 desktops **6.8/10**. **§19 corrects an error in my own analysis**: the database wall was over-stated (≈1.5 steady-state connections at 500 desktops, not 1,500 — the Django `hq` alias is never queried), so the real limit is HQ's single worker, not the database. §20 says where each kind of change is made (moving HQ is one environment variable, not a release); §21 closes C-3 and C-4 — signing could not previously be switched on at all, and a rejected key had no handling anywhere. §22 does the last four items: the dashboard is no longer world-readable, SSE refuses instantly, and the certificate guard turns out to be **already** cross-process safe — so `--workers 1` is now documentation lag, not a code constraint (evidence in §22.2; deliberately not changed). Final: moving HQ **9.4/10**, 500 desktops **7.4/10**.

## Appendix

`A1_ENGINEERING_AUDIT.pdf` — the formula-level audit of `CalSoft`, with file and
line references and 18 findings. **§0 records which are closed** (ten of
eighteen). The engineering companion to 01; not for general readership.

## Sources

`source/` holds the Markdown each PDF was rendered from. To regenerate:

```
venv/bin/python helper_scripts/md_to_pdf.py review/source/01_CALIBRATION_MATHEMATICS.md review/01_CALIBRATION_MATHEMATICS.pdf
```

## Scope

The platform spans **two git repositories**, and `hq_server` names two unrelated
codebases:

| Path | Repository | Role |
|---|---|---|
| `cirqen-labs/` | `cirqen-labs` | Django field app — CalSoft, calSchedules, ppms, Inventory, sync client |
| `cirqen-labs/hq_server/` | `cirqen-labs` | Software **update** server (Ed25519 package signing) |
| `~/Desktop/hq_server/` | `wairegimaina/hq_server` | HQ **sync and certificate authority** — allocates certificate numbers |

Documents 02 and A1 score and audit the `cirqen-labs` modules. Document 05
audits the HQ certificate and record-integrity paths across both repositories;
the layers it did not reach are listed in its §7. HQ is deliberately left
**unscored** in 02 rather than guessed at.

**Design rule recorded during this review:** certificate numbers are allocated
on the HQ server only. 05 §3 records where the code departed from it; that
departure has since been fixed (item B1), so HQ is now the sole allocator.

Static analysis only. No application code was modified. Line references may
shift as the trees change; the mathematics in 01 does not.
