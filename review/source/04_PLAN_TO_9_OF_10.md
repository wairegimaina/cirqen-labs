# CalSoft and Platform, The Plan to Reach 9 / 10

Repository: `cirqen-labs` | Branch: `improvement-plan` | Revised: 18 September 2026
**Implementation status as of this revision is in section 0.**
Companion to `02_RATING_AND_COMPARISON.pdf` (the 4.6/10 baseline) and
`05_HQ_AND_SYNC_AUDIT.pdf` (the platform findings)

This plan has **two tracks**, because the review found two different kinds of
problem in two different repositories:

- **Track A, CalSoft (section 1-section 9).** Five phases that take the calibration module
  from 4.6/10 to 9.2/10 on the rubric in `02`. This is measurement correctness,
  testing, and the certificate as a document.
- **Track B, Platform integrity (section 10).** Six findings from `05` covering
  certificate-number authority and whether a calibration record survives the
  trip to HQ intact. Not scored, for the reason given in section 10.1.

Track A is work inside `cirqen-labs`. Track B spans that repository and the
separate HQ server at `~/Desktop/hq_server`. **section 11 sequences both together**
and section 12 is what to do in the first week.

---

---

## 0. Status

Implemented and verified against the full suite (408 Django tests, 61 pytest, all
passing). The score is re-derived in `02_RATING_AND_COMPARISON.pdf` section 2a.

**4.6 / 10 -> 8.1 / 10.** Of 44 items: **26 done, 6 half-wired, 10 not started,
2 waiting on a decision.**

| Phase | Done | Half-wired | Not started |
|---|---|---|---|
| **P1** certificate truth | 6 / 7 |, | 1.4 needs a decision |
| **P2** test vectors | **7 / 7** |, |, |
| **P3** one engine | 5 / 8 | 3.1, 3.5, 3.8 | 3.7 |
| **P4** signing | 0 / 7 |, | all 7 |
| **P5** metrology | 5 / 8 | 5.4 | 5.2, 5.3 |
| **Track B** | 1 / 6 |, | B2-B6 |

**Done since the plan was written**

| Item | What changed |
|---|---|
| P1.1-1.3, 1.5-1.7 | Certificate reads strict conformity; the 40% rate is triage only; Reference column, `n` and the applied `k` are printed; uncertainty formatting keeps two significant digits |
| P2.1-2.8 | 181 tests, 95 with no database, every formula pinned to a hand-computed vector |
| P3.1-3.4, 3.6 | One shared `compute_uncertainty_budget`; live preview and stored record identical; drift and linearity report real numbers instead of reassuring strings; Decimal precision set once |
| P5.1 | `k` derived from effective degrees of freedom (Welch-Satterthwaite), configured value as a floor |
| P5.5-5.7 | Decision rule printed; TUR printed per point with named warnings below 4:1; drift graded as a fraction of tolerance |
| B1 | Certificate numbers allocated by HQ only; local allocation deleted from all three paths |

**Half-wired**

| Item | What is missing |
|---|---|
| P3.1 / 3.8 | `ReportGenerator`, `CalibrationValidator` and `MetrologyUtils` remain with zero callers |
| P3.5 | 73 broad exception handlers remain in the module |
| P5.4 | The guarded verdict is computed and printed, but `passes_tolerance` in the database is still binary, see section 12 |

**Waiting on a decision, not on effort**

1. **P1.4, the sign convention.** Error of indication (`mean - reference`,
   positive means reads high) or correction (`reference - mean`)? Either is
   legitimate; the column must be labelled to match, the stored data migrated
and the on-screen preview changed to agree, it currently computes the
   opposite sign to the certificate.
2. **Minimum readings.** Deriving `k` surfaced this: with two readings there is
   one degree of freedom and `t` is 12.706, so a two-reading point now prints a
   coverage factor of 12.7. That is statistically correct and not a certifiable
   measurement. The module has four different minimums (2 in the calculation, 3
   in the validator, 3 in `PDFConfig`, `num_readings` on the parameter) and the
   loosest governs. Reject below `num_readings`, warn and certify, or cap `k`
   and disclose the truncation. Capping understates the interval, so it was not
   chosen unilaterally.

---

## 1. The target, and what it costs

CalSoft scores **4.6 / 10** on the eleven-dimension rubric in the rating
document. **Track A** reaches **9.2 / 10** in five phases. (Track B, section 10, is
deliberately unscored, the score measures the calibration module.)

The arithmetic is not a guess. Each phase raises named dimensions by a stated
amount, and the running total is computed from the same weights the baseline
used:

| After | Phase | Score |
|---|---|---|
|, | Baseline | **4.6 / 10** |
| P1 | The certificate states one verdict, and it reconciles | **5.9 / 10** |
| P2 | Every formula pinned by a test vector | **7.0 / 10** |
| P3 | One calculation engine; failures surface | **8.0 / 10** |
| P4 | The certificate is tamper-evident | **8.6 / 10** |
| P5 | The metrology is completed | **9.2 / 10** |

Two things about this table are worth noticing before reading further.

**The phases are ordered by consequence, not by score.** P1 is worth 1.3 points
and is non-negotiable, because until it lands the system can certify a
non-conforming device as PASSED. P5 is worth 0.6 and is the most intellectually
interesting work in the plan. Do them in this order anyway.

**P2 produces no user-visible change at all.** It is second because every phase
after it edits the calculation path, and editing a calculation path that nothing
tests is how a working formula silently becomes a broken one. P2 is the
insurance policy for P3, P4 and P5.

There is a sibling to P2 in the other track. **B3** repairs a data-integrity
guard that is currently unable to fire, the same class of problem, something
trusted that nothing verifies, and the two belong in the same stretch of
calendar even though they are in different repositories.

---

## 2. What has to be added, in one view

Grouped by nature of the work, because these are five different kinds of task.

**Things that exist but are not connected** (P1, P4 partly)
Guard banding and the test uncertainty ratio are already implemented in the
codebase and have no callers. The strict session verdict is already computed and
is not the one printed. This is wiring, not building.

**Things that do not exist and must be written** (P2, P5)
A test vector per formula. Effective degrees of freedom. Sensitivity
coefficients. A printed decision rule.

**Things that exist in the wrong number** (P3)
Four parallel uncertainty implementations, two drift engines, two linearity
routines. The work is deletion and consolidation, not addition.

**Things borrowed from elsewhere in the platform** (P4)
Ed25519 signing and verification already run for update manifests, and HQ
already runs per-device API keys. The certificate needs the same treatment.

**Things that need one owner instead of two** (Track B, section 10)
Certificate numbers are allocated by two different algorithms on two different
databases, and the platform's last-write-wins guard cannot engage on the path it
protects. These span both repositories and are tracked separately.

---

## 3. Phase 1, The certificate states one verdict, and it reconciles

**Moves:** Decision-rule integrity 1->4 | Output fidelity 2->4
**Score:** 4.6 -> **5.9**
**Effort:** 3-5 days | **Blocks:** nothing. Start here.

### Why first

Two unrelated rules currently decide conformity. The database applies strict
conformity: every test point must pass. The certificate applies a 40%
failure-rate threshold. Between those two rules is a band in which a device is
recorded as failed and certified as **PASSED**, up to 39% of its test points
outside tolerance. This is the only defect in the plan that can put a
non-conforming device back into clinical service with a clean certificate.

### To add

| # | Item | Acceptance criterion |
|---|---|---|
| 1.1 | Certificate verdict reads the strict session result | A session with any failed point never prints PASSED |
| 1.2 | Failure rate demoted to a maintenance-triage signal | The rate still appears; it no longer decides the verdict |
| 1.3 | Reference-uncertainty column added to the printed budget | Type A^2 + Type B^2 + Ref^2 has a square root equal to Combined, on the page |
| 1.4 | Sign convention resolved and labelled | The column heading matches the quantity, and the on-screen preview agrees with the certificate |
| 1.5 | `n` printed per test point | Each row states how many readings produced it |
| 1.6 | Actual coverage factor printed in the notes | The note and the `k` column never disagree |
| 1.7 | Uncertainty columns widened past 4 decimal places | A component of 0.000029 does not print as 0.0000 |

### The decision this phase forces

Item 1.4 requires a choice that only the laboratory can make: report **error of
indication** (indication - reference, positive means reads high) or
**correction** (reference - indication, add to fix). Both are legitimate. The
requirement is that one is chosen, the column is labelled to match, the data is
migrated, and the technician's on-screen preview computes the same sign as the
certificate. At present the preview and the certificate disagree, so the
technician sees one sign and the customer receives the other.

### Done looks like

Take one session with a single failed point out of six. Before: certificate says
PASSED. After: certificate says FAILED, the budget table's arithmetic closes by
hand, each row shows `n`, and the notes quote the same `k` as the column.

---

## 4. Phase 2, Every formula pinned by a test vector

**Moves:** Test coverage 1->5 | Pure/IO separation 4->5
**Score:** 5.9 -> **7.0**
**Effort:** 1-2 weeks | **Blocks:** P3, P4, P5 should not start before this lands.

### Why second

There is currently no assertion anywhere in the test suite on a mean, a standard
deviation, an uncertainty component, a tolerance decision or a drift rate. The
one test that touches linearity does so by replacing the regression with a mock
so the suite reports success while the feature has never worked.

Every remaining phase modifies the calculation path. Doing that without tests is
the highest-risk activity in this plan.

### The enabling fact

The calculation engine is already **pure**, it depends on arithmetic and
decimal handling only, with no database and no framework. It can be tested
directly, in milliseconds, with no fixtures and no database, **today, with no
refactoring**. The cost of this phase is writing assertions, not restructuring
code.

A sibling module in the same repository already does exactly this, with a stated
design rule: pure logic above the database boundary, unit-tested without a
database. The pattern is proven in-house.

### To add

| # | Item | Acceptance criterion |
|---|---|---|
| 2.1 | Vector test for mean and standard deviation | Asserts 201.8 and 0.836660 from the five-reading vector |
| 2.2 | Vector test per uncertainty component | `u_A`, `u_res`, `u_ref` each asserted to six decimals |
| 2.3 | Vector test for combination and expansion | `u_c` = 0.488834, `U` = 0.977668 |
| 2.4 | Vector test for the deviation and the tolerance decision | Both signs; boundary case at exactly the tolerance |
| 2.5 | Vector tests for linearity and drift | Slope, intercept, largest residual, drift rate, `R^2` |
| 2.6 | Vector test for the outlier score | The 248-in-five case scores 31.0 and is flagged |
| 2.7 | Edge cases | `n` = 1, `n` = 2, all readings identical, MAD = 0, zero tolerance, missing reference |
| 2.8 | Rounding-behaviour test | Confirms the quantisation applied at each step, so a later change to it fails loudly |

### The ready-made first vector

The worked example in `01_CALIBRATION_MATHEMATICS.pdf` section 15 is a complete test
vector, computed by hand and verified: five readings in, eleven intermediate
values out, every one exact. Items 2.1 to 2.4 are that example transcribed into
assertions.

### Done looks like

The suite asserts a specific number for every formula in the handbook.
Deliberately changing `sqrt(12)` to `sqrt(3)` fails a test naming the resolution
component. Today it would fail nothing.

---

## 5. Phase 3, One calculation engine; failures surface

**Moves:** Single source of truth 2->5 | Failure visibility 2->4
**Score:** 7.0 -> **8.0**
**Effort:** 1-2 weeks | **Requires:** P2

### Why it matters beyond tidiness

Four implementations of the same uncertainty arithmetic disagree about which is
authoritative. The live-preview path computes the Type A component from the
unrounded standard deviation; the stored path computes it from the rounded one.
The technician therefore sees one number while typing and a different number is
saved. Both are defensible; having both is not.

Two features compound this by reporting confident wrong answers. The drift
display reads result keys the drift engine never produces, so every device on
every screen shows **"No significant drift detected"** regardless of the data. The
linearity display calls a routine that does not exist, and a broad exception
handler converts the failure into "Unable to calculate linearity" for every
parameter. Neither announces itself. Both look like working features, and one of
them actively reassures.

### To add

| # | Item | Acceptance criterion |
|---|---|---|
| 3.1 | One calculation engine; the other three deleted | Exactly one implementation of each formula in the tree |
| 3.2 | Live preview and stored value computed identically | Preview and record agree to the last digit |
| 3.3 | Drift display reads what the drift engine returns | A device with real drift never displays "No significant drift detected" |
| 3.4 | Linearity routine repaired and connected | Slope, intercept and largest residual appear for a multi-point parameter |
| 3.5 | Broad exception handlers in analysis paths replaced | A failed analysis says so, visibly, and is logged as an error |
| 3.6 | Decimal precision set once | One precision setting for the application, not two that contradict |
| 3.7 | Rounding applied once, at presentation | Intermediates carry full precision; only the printed value is rounded |
| 3.8 | Dead metrology routines deleted or repaired | Nothing in the tree references fields that no longer exist |

### On item 3.7

At present each intermediate is rounded to six decimals and the rounded value
feeds the next step, four sequential roundings between the readings and the
expanded uncertainty. At millimetre-of-mercury magnitudes this is immaterial. For
a parameter in millivolts or microamps it is not: a resolution component of
0.0000289 becomes 0.000029, and the error compounds. Round once, at the end.

### Done looks like

Searching the tree for the combination formula returns one result. A seeded
device with genuine drift shows that drift on screen. Breaking the linearity
routine on purpose produces a visible error, not a reassuring sentence.

---

## 6. Phase 4, The certificate is tamper-evident and uniquely numbered

**Moves:** Security of issued artefact 1->5
**Score:** 8.0 -> **8.6**
**Effort:** 2-3 weeks | **Requires:** P2, and B1/B2/B4 from Track B | **Spans both repositories**

### Where this phase actually lives

This is the one phase that is **not** a `CalSoft` change. Certificate issuance
spans two separate repositories, and the authority is the second one:

| Path | Repository | Role in issuance |
|---|---|---|
| `cirqen-labs/` | `cirqen-labs` | Captures readings, computes results, renders the PDF |
| `~/Desktop/hq_server/` | `wairegimaina/hq_server` | Allocates the certificate number, flips the session to approved |

Signing therefore belongs at HQ, because HQ is what decides that a certificate
exists and what it is called. A signature applied in the field, to a number the
field did not authoritatively allocate, would be signing a claim the signer
cannot make.

Note the naming trap: `cirqen-labs/hq_server/` is the **software update** server
and is a different codebase from `~/Desktop/hq_server`. The Ed25519 pattern
cited below comes from the former; the work in this phase lands in the latter.

### The current position

Every certificate carries the note *"Certificate authenticity can be verified by
scanning the QR code."* The verifier behind that promise returns **valid for any
input**, with an in-code note that a database lookup was intended. The
verification token is reversible encoding with no signature, so anyone who has
seen one certificate can mint another that verifies. The signature block is a
pasted image file. The configuration records digital signing as disabled.

Of everything in this plan. This is the item most likely to be discovered by
someone outside the organisation.

### The numbering problem this phase must also settle

Two algorithms allocate `BNH-####` into the same column, on two different
databases, with locks that cannot see each other:

- **HQ** reuses the lowest missing number, then max+1, under a Postgres
  advisory lock on the HQ database.
- **CalSoft** allocates strictly max+1, under `select_for_update` on the local
  field database.

Each is correct against concurrent callers on its own database. Neither
constrains the other. On the offline-first design this platform is built
around, a site calibrating through a network outage, both can allocate, and
because the column is `unique=True` the result is a sync conflict rather than an
early, clean error.

A signature is only as meaningful as the identity it binds. Signing a
certificate whose number might later be allocated to a different device does not
produce a trustworthy artefact, which is why numbering and signing belong in the
same phase.

Three things do **not** need fixing, and were verified rather than assumed: gap
reuse is deliberate and covered by `tests/test_cert_numbering.py`; soft delete
retains the row and its number, so a deleted certificate's number never returns
to the pool; and wholesale renumbering is reachable only behind an explicit
double confirmation.

### The enabling fact

The platform already signs and verifies with Ed25519, the update system signs
release manifests with a private key held only on the server, and clients refuse
anything failing verification against an embedded public key. The cryptography
library is already a dependency and key generation is already a command-line
flag. HQ additionally already runs per-device API keys with hashing, issuance and
revocation. This phase reuses working in-house patterns rather than introducing
cryptography.

### To add

Numbering authority is **not** listed here. It is Track B, items **B1**
(server-only allocation), **B2** (no reassignment of an issued number) and **B4**
(an allocated number cannot be erased by an upload). They are prerequisites for
this phase rather than part of it: a signature is only as meaningful as the
identity it binds, so the number must be settled before signing it is worth
doing. Section 11 sequences them ahead of P4.

| # | Item | Where | Acceptance criterion |
|---|---|---|---|
| 4.1 | Signing keypair, private key server-side only | HQ | Key generation documented; private key never in a repository |
| 4.2 | Certificate payload signed at allocation | HQ | Signature covers the number, device, date, every result and every verdict |
| 4.3 | Verification looks the certificate up and checks the signature | HQ | Returns truthful validity; a tampered payload is rejected |
| 4.4 | QR carries a verification reference, not the results | CalSoft | Removes the silent truncation of long result sets |
| 4.5 | Signature block records who approved and when | CalSoft | Bound to the signed payload, not decorative |
| 4.6 | Re-issue produces a new signature and a versioned record | HQ | The superseded certificate remains verifiable and is marked superseded |
| 4.7 | Note 4 matches reality | CalSoft | Either verification works, or the claim comes off the certificate |

### On the numbering prerequisite

The full treatment is in section 10.3 and in `05_HQ_AND_SYNC_AUDIT.pdf` section 3. In short:
the stated rule, certificate numbers are allocated on the HQ server only,
settles a question this plan originally left open. The offline branch already
complies by assigning no number and queuing a `PendingCertificate`. The online
branch does not, and the cheapest fix is to make it behave like the offline one.

Once that lands, `sync/cert_conflict_guard.py` becomes a dormant safety net over
a collision that can no longer occur, rather than a working part of issuance.

### On item 4.4

The QR currently embeds up to fifteen results and silently drops the rest, so a
twenty-point procedure's QR omits a quarter of its data with no indication. It
also prints the deviation after a `±` sign, where a reader expects an
uncertainty. Carrying a reference to a verifiable record instead removes both
problems.

### Done looks like

Alter one digit in a certificate's stored results and verification fails.
Present an unmodified certificate and it verifies, naming the approver and the
date. Two sites calibrating offline, then reconnecting, produce no number
collision and no sync conflict. Item 4.9 is satisfied on the day the first two
hold.

---

## 7. Phase 5, The metrology is completed

**Moves:** Core formula correctness 4->5 | Decision-rule integrity 4->5
**Score:** 8.6 -> **9.2**
**Effort:** 3-4 weeks | **Requires:** P1, P2

### What is still missing

With P1 to P4 done, the system computes correct uncertainties, uses them in the
verdict, tests every formula and issues a tamper-evident certificate. Four
metrological capabilities remain absent, and they are what separate a competent
in-house system from one that can be defended to an accreditation assessor.

### To add

| # | Item | Why it matters | Acceptance criterion |
|---|---|---|---|
| 5.1 | Effective degrees of freedom (Welch-Satterthwaite) | With 3-5 readings, `k` = 2 understates the interval by up to 40%. The existing routine returns a constant | `k` derived from effective degrees of freedom; `n` = 3 yields a larger `k` than `n` = 20 |
| 5.2 | Sensitivity coefficients | Every component is assumed to act one-for-one, so no influence quantity needing a coefficient can enter the budget at all | A temperature coefficient can be declared and contributes correctly |
| 5.3 | Type B components as a table, not two fixed slots | Only resolution and reference can be represented today | An arbitrary number of Type B components, each with its distribution and coefficient |
| 5.4 | Three-outcome guarded decision | A marginal point currently gets a bare pass or fail | Pass / Fail / **Indeterminate**, with the indeterminate band computed from `U` |
| 5.5 | Decision rule printed on the certificate | Required whenever a statement of conformity is issued | The certificate states the rule and the guard band applied |
| 5.6 | TUR computed, printed and warned on | The existing routine has no callers | TUR appears per parameter; below 4:1 raises a visible warning |
| 5.7 | Drift judged relative to tolerance | Bare-number stability grades compare millimetres of mercury with millivolts | Grades and interval advice are dimensionless fractions of tolerance |
| 5.8 | Drift-based interval estimation | The existing routine has no callers | Interval advice derived from measured drift and remaining margin |

### On item 5.4

This is the change that most improves the system's honesty. A three-outcome
decision refuses to produce a verdict when the measurement cannot support one.
A device landing in the indeterminate band is not condemned. It needs a better
measurement, or an accepted-risk decision recorded by someone with the authority
to make it. Two commercial calibration platforms have worked this way for
decades; one has since 1999.

### On item 5.7

Stability is currently graded by comparing an absolute yearly drift rate against
fixed constants, with no reference to the unit or the tolerance. The same
threshold therefore grades a pressure channel, an ECG amplitude and an infusion
rate, and the resulting grade drives a printed recommendation to change a
service interval. Dividing by that parameter's tolerance makes the quantity
dimensionless and comparable, and makes the printed advice mean something.

### Done looks like

A three-reading calibration produces a visibly larger `k` than a twenty-reading
one. A temperature coefficient can be declared and shows up in the budget. A
marginal point returns INDETERMINATE. The certificate states its decision rule.
Drift advice for a pressure channel and an ECG channel are on the same scale.

---

## 8. Track A sequencing

```
P1  Certificate truth  ----------------------------+
      (independent - start immediately)           |
                                                  v
P2  Test vectors  -------> P3  One engine ---> P5  Metrology
      (gates P3, P4, P5) |                     ^      completed
                         +-> P4  Signing  -----+
                               (independent of P3)
```

- **P1 needs nothing.** It can begin today and run alongside P2.
- **P2 gates P3, P4 and P5.** Do not edit the calculation path before it lands.
- **P3 and P4 are independent of each other** and can run in parallel.
- **P5 needs P1 and P2**; it is easier after P3.

Track A alone: **9 to 12 weeks**, roughly half of it in P5.

If only three weeks are available, do **P1 and P2**. That reaches 7.0/10 and
removes every defect that affects the correctness of an issued certificate. It
is the correct stopping point if Track A must be cut, because P1 fixes what is
actively wrong and P2 ensures the next person cannot break it.

---

## 9. Headroom beyond 9.2

Deliberately outside the plan, because past this point effort is better spent
elsewhere. Listed so the remaining gap is visible rather than mysterious.

| Add | Reaches |
|---|---|
| Handbook reconciled against the code and dated | 9.3 / 10 |
| Significant-figure formatting throughout the certificate | 9.6 / 10 |
| One role helper and one identity source across the module | 9.6 / 10 |
| Database-level integrity constraints on computed fields | 9.7 / 10 |
| Every silent failure path surfaced | 9.9 / 10 |

The documentation item deserves a note. The module's technical handbook is the
best such document in the repository, and it is stale on the one finding it
labels most important: it describes a calculation defect that has since been
fixed, and marks as dead code the routine that is now the live calculation path
for every certificate issued. Dating it and reconciling its defect register
against the tree is a few hours' work and prevents the next reader from chasing
a defect that no longer exists.

---

---

## 10. Track B, Platform integrity

### 10.1 Why this track is not scored

The rubric in `02` has eleven dimensions chosen for a Django calibration module
and it was applied to four modules in `cirqen-labs`. The HQ server is a separate
repository of 25,542 lines, and only its certificate and record-integrity paths
have been audited. Scoring it on a rubric built for something else, or on a
partial audit, would produce a number that looks like measurement and is not.

So Track B has **binary gates** instead: each item is done or it is not, with an
acceptance criterion that can be checked. The Track A score of 9.2/10 is
unaffected by Track B, because it measures the calibration module.

This is also the honest reading of the situation: Track A is about a certificate
being *correct*. Track B is about a certificate being *real*, uniquely
numbered, and reflecting a record that survived the trip. A 9.2 on Track A with
Track B unaddressed would be a well-calculated certificate that might carry
another device's number.

### 10.2 The six items

| # | From | Item | Acceptance criterion | Effort |
|---|---|---|---|---|
| **B1** | H-2 | Server-only allocation | The online approval branch no longer computes a number; the local allocation path is **deleted**, not left reachable | 2-3 d |
| **B2** | H-1 | No number bound to an issued document is ever reassigned | The conflict guard voids and reissues with a superseded record; the superseded certificate stays verifiable and is marked | 3-5 d |
| **B3** | H-3 | The last-write-wins guard can actually engage | Client synced tables carry `source_updated_at`, populated on write; a stale upload provably loses to a newer HQ value | 3-5 d |
| **B4** | H-6 | An allocated number cannot be erased by an upload | HQ refuses to overwrite a non-null `certificate_number` with null | 1 d |
| **B5** | H-4 | A missing version column loses, not wins | `EXCLUDED.source_updated_at IS NULL` no longer grants an unconditional overwrite | 1 d |
| **B6** | H-5 | Conflicts are real conflicts | `is_conflict` compares payloads, `_content_hash` already exists in the same module | 0.5 d |

Total: **11 to 16 days**.

### 10.3 Order, and why

**B4 first.** One day, and it is the only item that stops an active
loss-of-data path rather than preventing a future one. Until the guard is live
(B3), a null arriving from a field site can erase a certificate number that HQ
allocated. B4 is a narrow field-specific refusal, cheap, uncontroversial, and
it buys time for everything else.

**B1 second.** It removes the collision at source, which is what the stated rule
already requires. Once numbers are allocated only at HQ, the conflict guard
stops being part of normal issuance and becomes a dormant safety net.

**B3 third.** The largest item, and the one with the widest blast radius: it
makes the platform's central integrity protection live for the first time. It
needs a migration across the synced tables plus a write-path change, so it wants
a quiet window and a verification test, not a Friday.

**B2 fourth.** Existing collisions still need a correct repair, and after B1 the
population stops growing.

**B5 and B6 last.** Small, safe, and neither is urgent once B3 is in.

### 10.4 What B3 must not break

`sync/mirror.py` already documents the failure mode from the last time this
column moved: HQ gained `source_updated_at`, and mirroring those rows *down*
raised `column "source_updated_at" of relation "..." does not exist` for every
affected row. The client now tolerates unknown columns on the way down, and HQ
tolerates them on the way up.

So B3 adds a column the client is already prepared to ignore, the risk is not
schema breakage but **silent partial adoption**: tables that get the column and
tables that miss it, leaving the guard live for some records and inert for
others. That is worse than uniformly inert, because it is unpredictable.

The acceptance criterion is therefore per-table: every table in the sync
registration either carries the column and populates it, or is explicitly and
deliberately listed as exempt.

### 10.5 A note on where the effort actually goes

Track B is 11-16 days against Track A's 9-12 weeks, and it addresses four
high-severity findings against Track A's seventeen of mixed severity. It is the
better return per day of the two tracks.

That is not an argument for doing Track B instead. Track A fixes a certificate
that states the wrong verdict and a module where no formula is tested. Those are not optional. It is an argument for **not deferring Track B** on the grounds
that it is in another repository and therefore someone else's problem. B4 alone
is a day.

---

## 11. Combined sequencing

```
WEEK 1      B4 null guard (1d)        P1 certificate truth (3-5d)
              both independent, start together

WEEK 2-3    B1 server-only alloc (2-3d)    P2 test vectors (1-2w)
            B2 void + reissue   (3-5d)        gates P3, P4, P5

WEEK 3-4    B3 make LWW live    (3-5d)
            B5 B6               (1.5d)

WEEK 4-6                                  P3 one engine (1-2w)

WEEK 5-7                                  P4 signing (2-3w)
                                             needs P2 + B1 + B2 + B4

WEEK 7-11                                 P5 metrology completed (3-4w)
```

The tracks barely contend for the same code. Track B lives in `~/Desktop/hq_server`,
`sync/` and one branch of the approval view; Track A lives in the calculation
path, the PDF generators and the test suite. The one genuine overlap is **B1 and
P1**, which both touch the approval and certificate path, do B1 first or
together, not after P1 has been merged.

**Total for both tracks: 11 to 13 weeks**, since Track B runs mostly in
parallel and finishes inside the first four. Track A alone would be 9 to 12, so
Track B costs roughly one week of calendar, not three.

---

## 12. What to do next

Items 3 and 4 of the original first week are done: the certificate reads the
strict verdict, and the test vectors are in. What remains, in order of
consequence:

1. **B4, HQ refuses to null an allocated certificate number.** One day, in the
   HQ repository. Still the only item that closes an *active* data-loss path
   rather than preventing a future one.
2. **Confirm the H-6 race empirically.** Half a day. Approve a session, let HQ
   allocate, then replay the client's upload of that row and see whether the
   stored number survives. This is the one finding in the review established
   from code alone; knowing whether it fires tells you how much history to check.
3. **Take the two decisions above.** They gate P1.4 and the minimum-readings
   enforcement, and both change what the field can submit or what stored data
   means, so they are not implementation details.
4. **B3, make the last-write-wins guard able to engage.** The largest remaining
   integrity item, and it wants a quiet window plus the per-table acceptance
   criterion in section 10.4.
5. **P5.4, store the three-valued verdict.** Needs a decision of its own: what
   does an `INDETERMINATE` point do to a session's overall pass? Today the
   certificate reports three outcomes while the database stores two, which is an
   internal inconsistency worth closing before it is relied on.

---

## 13. What "done" will mean in practice

Not a score. Six properties from Track A, and two from Track B:

1. **One verdict.** The certificate, the record and the QR agree, always, and
   strict conformity is the rule.
2. **A budget that reconciles.** Every component printed; the arithmetic closes
   by hand; the coverage factor stated and correct for the sample size.
3. **Uncertainty that changes the answer.** Guard-banded decisions with a real
   third outcome, a stated decision rule, and a TUR warning when the measurement
   is not sharp enough to judge the tolerance.
4. **A tamper-evident, uniquely numbered certificate.** Signed at allocation by
   the one component with authority to allocate, genuinely verifiable, with the
   approver bound to the signed payload and no number ever reissued.
5. **Analyses that fail loudly.** No feature that prints reassurance when it has
   computed nothing.
6. **Formulas that cannot silently change.** One implementation each, every one
   pinned to a hand-computed vector.
7. **One authority for certificate numbers.** Allocated at HQ only, never
   reassigned once bound to an issued document, never erasable by an upload.
8. **Records that survive the trip.** The last-write-wins guard engages on the
   path it was built for, and a stale field row provably loses to a newer
   stored value.

The remaining 0.8 of Track A is mostly formatting, documentation currency and
internal consistency, worth doing, and not worth delaying anything above for.
Track B has no remainder: its six items are the whole of it, within the paths
`05` audited.

---

*Track A score movements are computed from the weighted rubric established in
`02_RATING_AND_COMPARISON.pdf` section 2; the weights are a judgement about consequence
for a module that issues conformity statements, and are stated there so they can
be argued with. Track B is deliberately unscored (section 10.1). Effort estimates
assume one engineer familiar with the codebase, and Track B assumes access to
both repositories and to HQ's database for the migration in B3.*
