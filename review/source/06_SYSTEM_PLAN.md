# Equiper, The Plan From Here

Repositories: `cirqen-labs` and `wairegimaina/hq_server` | Written: 18 September 2026
Supersedes the sequencing in `04_PLAN_TO_9_OF_10.pdf`, which covered CalSoft alone

---

## 1. Where the system stands

534 Django tests and 61 sync tests pass. Four areas have been worked on, and
they are at different stages.

| Area | State | Score / coverage |
|---|---|---|
| **CalSoft**, calibration and certification | Phases 1, 2, 3 and most of 5 done | **8.6 / 10**, 188 tests |
| **calSchedules**, planning and grouping | Auto-scheduling repaired, regrouping added | 126 tests |
| **HOD dashboard**, the head's first screen | Rebuilt around calibration coverage | 14 tests, from none |
| **audit_log**, the audit trail | Both trails now surfaced | 17 tests, from none |
| **HQ server**, sync and certificate authority | One of six items done (B1) | 41 tests, unaudited in parts |

Two things are worth saying plainly about that table.

**The gains came from wiring, not from new mathematics.** Almost every fix was
a correct component that nothing called, or two components that disagreed:
guard banding written and never invoked, a drift engine whose consumer read
keys it never returned, two definitions of group membership, two certificate
allocators, an audit trail written from five places and displayed nowhere. The
system's problem has not been capability. It has been that capable parts were
not connected to each other.

**Three apps had no tests at all before this work**, and two of them
(`dashboard`, `audit_log`) were changed during it. Writing the tests alongside
the changes is the only reason those changes can be trusted; `reporthub`,
`machineReports` and `parts_tools` still have none and remain the least safe
places in the codebase to change.

---

## 2. What is left, by consequence

Ordered by what goes wrong if it is not done, not by effort.

### Tier 1, a certificate can still be doubted

| # | Item | Where | Effort |
|---|---|---|---|
| 1.1 | **HQ refuses to null an allocated certificate number** (B4) | HQ | 1 d |
| 1.2 | **Confirm the H-6 race empirically** | HQ | 0.5 d |
| 1.3 | **Make the last-write-wins guard able to engage** (B3) | HQ + sync | 3-5 d |
| 1.4 | **Void and reissue instead of reassigning** (B2) | HQ + sync | 3-5 d |
| 1.5 | **Sign the certificate; make verification real** (P4) | HQ + CalSoft | 1-2 w |

1.1 is still the only item that closes an *active* data-loss path. Everything
else in this tier prevents a future one.

1.5 is what the certificate currently promises and does not do. The QR note and
footer no longer overstate it, so the document is honest, but an issued
certificate is still not tamper-evident, and that is the single largest gap
against Fluke and Beamex.

### Tier 2, two decisions are gating four items

Neither is a technical question, and both change what stored data means.

| # | Decision | What it unblocks |
|---|---|---|
| 2.1 | **Sign convention.** Error of indication (`mean - reference`) or correction (`reference - mean`)? | P1.4, plus a data migration and the on-screen preview, which currently computes the opposite sign to the certificate |
| 2.2 | **Minimum readings.** Reject below `num_readings`, warn and certify, or cap `k` and disclose the truncation? | Enforcement, plus the `k = 12.706` case that two readings now produce |

On 2.2: capping `k` understates the interval, which is the dishonest direction
so it was not chosen unilaterally. The recommendation is to reject below the
parameter's declared `num_readings`, but that changes what the field may
submit, so it is yours.

### Tier 3, completing the metrology

| # | Item | Why it is not Tier 1 |
|---|---|---|
| 3.1 | **Sensitivity coefficients** (P5.2) | The budget cannot represent an influence quantity acting indirectly, a real limit, but no current parameter needs one |
| 3.2 | **Type B components as a table** (P5.3) | Follows from 3.1; do them in one migration |
| 3.3 | **Store the three-valued verdict** (P5.4) | The certificate reports PASS/FAIL/INDETERMINATE; the database still stores two. Needs a decision of its own: what does INDETERMINATE do to a session's overall pass? |
| 3.4 | **Round once, at presentation** (P3.7) | Four sequential roundings; immaterial at mmHg, not at millivolts |

3.1 and 3.2 share a migration with 1.3's `source_updated_at`. Doing all three
in one pass is less risky than three passes over the same synced tables.

### Tier 4, the parts nobody has looked at

| Area | Lines | Tests | Risk |
|---|---|---|---|
| `reporthub` | 2,726 | **0** | Generates reports leadership acts on |
| `machineReports` | 3,606 | **0** | Now linked from every workshop card |
| `parts_tools` | 3,038 | **0** | Stock and tooling |
| HQ `og_server_core`, Redis layers, `soft_delete_handler` | ~6,000 | partial | Sync correctness |

`machineReports` moved up this list during this work: the HOD dashboard now
links into it from every workshop card, so more people will reach code that
nothing verifies.

---

## 3. The order to do it in

```
NOW        1.1 null guard (1d)          2.1 + 2.2 decisions
             closes active loss           they gate four items

WEEK 1-2   1.2 confirm H-6 (0.5d)       P1.4 sign convention
           1.3 LWW guard live (3-5d)      once 2.1 is settled

WEEK 3-4   1.4 void and reissue         3.4 round once
           3.1 + 3.2 + 1.3 migration in one pass

WEEK 5-7   1.5 sign the certificate
             needs 1.1, 1.3, 1.4 first

ONGOING    Tier 4: a test file per module before the next change to it
```

**The one sequencing rule that matters:** 1.5 comes after 1.1-1.4. Signing a
certificate whose number might later be reallocated, or whose record a stale
upload can overwrite, produces a signature that attests to nothing. The
integrity of the number has to be settled before it is worth signing.

---

## 4. How to work on this system

Four practices earned their place during this work. They are written down
because each one caught something that tests alone did not.

**Render the thing and look at it.** The certificate printed two filled squares where an
emoji had no glyph in the PDF font, and the workshop card reported "5 locked" on a finished group,
technically true, operationally useless. Both passed every test. Neither
survived being looked at.

**Pin behaviour you intend to change.** Tests marked `PINS CURRENT BEHAVIOUR`
made four later changes visible instead of silent: the drift grading, the
decimal precision, the coverage factor and the approval path all broke a test
on purpose, and the diff showed exactly what the change meant.

**Fix the class, not the instance.** `test_no_broad_handler_discards_an_exception_silently`
walks every module's AST and fails if a broad handler swallows an error. That
prevents the next 26 instances, which fixing 26 handlers would not.

**Check the claim before repeating it.** Twice in this work a confident
statement was wrong: the HQ server does have tests, and a narrow
`except ValueError` is good design rather than a defect. Both were corrected by
looking rather than by remembering. The module's own 6,008-line handbook is
stale in exactly this way, superb, and wrong about the finding it calls most
important.

---

## 5. What "done" looks like

Not a score. Eight properties, of which **five now hold**:

1. **[done]** One verdict, the certificate, the record and the QR agree.
2. **[done]** A budget that reconciles, with `n` and the applied `k` on the page.
3. **[done]** Uncertainty that changes the answer, guard banding, three outcomes, TUR.
4. **[done]** Analyses that fail loudly rather than printing reassurance.
5. **[done]** Formulas that cannot silently change, one implementation, each pinned.
6. **[open]** A tamper-evident certificate, signed by the component that allocates it.
7. **[open]** Records that survive the trip, the LWW guard engaging on client uploads.
8. **[open]** A budget that can represent any influence quantity.

Items 6 and 7 are Tier 1. Item 8 is Tier 3. The three remaining properties are
the difference between a system that computes correctly and one that can prove
what it computed.

---

*Effort estimates assume one engineer familiar with the codebase. Items marked
HQ require access to the second repository and to its database for the
migration in 1.3.*
