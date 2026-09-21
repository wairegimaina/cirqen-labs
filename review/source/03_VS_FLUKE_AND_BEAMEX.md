# CalSoft vs Fluke and Beamex, A Capability Comparison

Repository: `cirqen-labs` | Branch: `improvement-plan` | Reviewed: 17 September 2026
Third in the series, after `A1_ENGINEERING_AUDIT.pdf` (formula audit) and `02_RATING_AND_COMPARISON.pdf` (internal rating)
**Revised 18 September 2026: six matrix rows changed after implementation.**

---

## 0. Two notes before the comparison

**On "Beamer".** Read here as **Beamex**, the Finnish calibration company whose
CMX calibration management software and MC6 field calibrators are the natural
comparator alongside Fluke. If something else was meant (Beamer is also a LaTeX
presentation class, and BEAMER an e-beam lithography tool, neither relevant to
calibration), say so and this section can be redone.

**On scope.** "CalSoft" here means the calibration capability of the whole
platform, which spans two git repositories: the `cirqen-labs` Django
application, and a separate HQ server (`~/Desktop/hq_server`) that allocates
certificate numbers and acts as the sync authority. Rows in the matrix below
credit the platform for what either provides.

**On comparing unlike things.** The three names cover *three different product
classes*, and collapsing them into one league table would produce a misleading
answer. The single most useful result of this comparison is the recognition that
CalSoft is not competing with one product, it sits between two:

| Product | Class | What it is for |
|---|---|---|
| **Fluke Biomedical Ansur** | Hospital biomedical QA automation | Driving analyzers/simulators through electrical-safety and performance test protocols on medical devices |
| **Fluke MET/CAL + MET/TEAM** | Accredited metrology laboratory | Executing calibration procedures with a full GUM uncertainty budget and managing the asset estate |
| **Beamex CMX (+ MC6)** | Process/industrial calibration management | Managing calibration of process instrumentation end to end, with uncertainty and regulatory records |
| **CalSoft** | In-house hospital calibration and certification | Capturing manual readings, reducing them to an uncertainty budget, and issuing a numbered certificate |

CalSoft's functional peer is **Ansur** (same setting, same devices, same users).
Its *ambition*, a GUM uncertainty budget feeding a conformity statement, is
**MET/CAL and CMX** territory. That split is the whole story, and section 3 is the
punchline.

---

## 1. Capability matrix

Sourced to vendor documentation (section 8). "Not documented" means the vendor's own
literature does not describe the capability, not that it is impossible.

| Capability | CalSoft | Ansur | MET/CAL + MET/TEAM | Beamex CMX |
|---|---|---|---|---|
| **Reading capture** | Manual keyboard entry | Automated from analyzer via per-model plug-in | Automated, procedure drives the instruments | Automated, MC6 field calibrator syncs to CMX |
| **Type A (repeatability)** | Yes, `s/sqrt(n)` | Not documented | Yes (GUM since v6.0, 1999) | Yes |
| **Type B components** | Two, fixed: resolution + reference | Not documented | User-defined, configurable | Up to **eight** user-defined |
| **Combined / expanded U** | Yes, RSS then k | u_c | Not documented | Yes, GUM-compliant | Yes, per calibration point |
| **U in the verdict** | **Yes**, guard banding applied | n/a (limit-based) | **Yes**, guard banding adjusts limits during execution | **Yes**, flags "passed, but at risk of fail once uncertainty is counted" |
| **Decision outcomes** | Pass / Fail / **Indeterminate** | Pass / Fail vs test limits | Pass / Fail / **Indeterminate** | Pass / Fail + documented risk view |
| **TUR ratio** | **Yes**, printed per point, named warning below 4:1 | Not documented | Yes | Yes, built into the uncertainty view |
| **Guard banding** | **Yes**, acceptance and rejection limits from U | Not documented | Yes, dynamic | Risk-based equivalent |
| **Degrees of freedom** | **Yes**, Welch-Satterthwaite; k derived, not assumed | Not documented | Part of GUM handling | Not separately documented |
| **Electronic signature** | Pasted signature **image** (base64 PNG) | Real e-signature, 21 CFR Part 11 | Yes, 21 CFR Part 11 | Yes, 21 CFR Part 11 e-records/e-signatures |
| **Audit trail on records** | App-level audit-log model, plus an HQ `audit_log` table | Version-controlled test records | All changes retained as audit trail | Full database change tracking |
| **Central certificate authority** | **Yes, HQ allocates numbers under an advisory lock** | Central PC / CMMS records | MET/TEAM over MET/CAL | CMX server |
| **Certificate integrity** | Numbers now single-authority; **QR check still always returns valid** | Version-controlled electronic records | Audit trail + e-sig | Permanent auditable store |
| **Accreditation** | None claimed | ISO 9001 / 13485 manufacturer; supports hospital QA regimes | Supports ISO/IEC 17025, ANSI Z540, 21 CFR 11 | Supports ISO 9001, ISO 17025, GMP/GAMP, 21 CFR 11 |
| **Intervals** | Yes, via calSchedules | PM justification from trend data | MET/TRACK: days, weeks, months, **use cycles** | Yes |
| **Offline operation** | **Yes, CDC sync engine, 17k LOC** | Central PC or standalone install | Central SQL Server | CMX server; MC6 works offline in the field |
| **Batch testing** | One session per device | **Yes, multiple devices at once** | Procedure-driven | Work-order driven |
| **Licence cost** | None (in-house) | Per analyzer plug-in, per computer | Substantial; needs MS SQL Server | Commercial licence |
| **Platform** | Django, PostgreSQL, Linux | Windows + MS SQL Server + .NET | Windows + MS SQL Server | Windows/server |

---

## 2. Where CalSoft already holds its own

Three of these are genuine, and one is a real competitive advantage.

**It computes an uncertainty budget where its functional peer does not.**
The Ansur technical datasheet describes the product as automatically assessing
"pass/fail against test limits specified by global standards or organizations."
Type A, Type B, combined and expanded uncertainty, coverage factors, guard
banding and TUR appear nowhere in it. Ansur is a *test-limit* tool. CalSoft
attempts the GUM budget that Ansur does not, and the five core formulas are
individually correct (per `A1_ENGINEERING_AUDIT.pdf` section 3). **On the mathematics of
uncertainty, CalSoft is ahead of the Fluke product actually sold for hospital
biomedical QA**, and behind the Fluke product sold for metrology labs.

**Offline-first operation is a real advantage, not a compromise.**
Ansur wants a central PC or a networked MS SQL Server; MET/CAL and MET/TEAM want
SQL Server; CMX is a licensed server product. CalSoft runs a 17,000-line
change-data-capture sync engine (`sync/`, Debezium table registration in
`sync/config.py`) that lets a workshop calibrate through a network outage and
reconcile afterwards. For a hospital estate where connectivity is intermittent,
this is worth more than several of the features in the matrix above, and none of
the three commercial products is architected for it.

**Cost and fit.** No per-analyzer plug-in licence, no per-computer licence, no
SQL Server, and the data model already knows about the hospital's departments,
workshops, roles and schedules. Ansur charges per analyzer plug-in per computer.

**Procedure and schedule integration.** CalSoft's procedures, parameters, set
values, sub-parameters, equipment mappings and calibration schedules are one
system. Reaching that with Fluke means MET/CAL *plus* MET/TEAM or MET/TRACK.

**There is a real central certificate authority.** This was understated in the
first draft of this comparison. A separate HQ server allocates every certificate
number under a Postgres advisory lock, writes its own `audit_log`, runs
per-device API keys with issuance and revocation, and has a dedicated test suite
for the numbering itself. Structurally that is closer to MET/TEAM's role over
MET/CAL than a single Django application would suggest, and it is more than
Ansur offers, which records to a central PC or CMMS without acting as an
allocating authority.

The qualification is that the authority is **not exclusive**: the field
application can also allocate, by a different algorithm, on a different
database. Commercial platforms have one allocator by construction. That is the
gap, not the absence of an authority, but the presence of two.

---

## 3. The one finding that matters

Put the matrix rows in the right order and the picture is unambiguous.

*This section described the position on 17 September. It has changed, and the
change is recorded at the end.*

CalSoft had built **the hard part**, a GUM-shaped uncertainty budget, which
Ansur does not attempt, and then omitted **the easy part that makes it mean
something**: using the number in the decision.

- MET/CAL takes the uncertainty and *moves the test limits with it*, yielding
  Pass, Fail, or **Indeterminate** when a reading sits too close to the limit to
  call.
- Beamex CMX takes the uncertainty and shows the calibrator that a point
  "may be passed but when taking the uncertainty into account then there is a
  risk that it is failed," and checks the reference standard is good enough via
  TUR.
- CalSoft computes `u_A`, `u_res`, `u_ref`, `u_c` and `U` to six decimal places,
  prints them on the certificate, and then decides conformity with
  `abs(error) <= tolerance` (`models.py:680`), uncertainty excluded.

The asymmetry is sharper still: **CalSoft already contains both missing pieces.**
`MetrologyUtils.calculate_guard_banding` and
`calculate_measurement_capability` (TUR) are written, correct in outline, and
have **no call sites anywhere in the repository**. The module's own 6,008-line
handbook works a TUR example by hand in section 5.9.2 and discusses the guard-band
`u_c`-versus-`U` ambiguity in section 5.10, concluding the function "must not be wired
in without a decision being taken and documented."

So the distance from CalSoft to MET/CAL-class decision behaviour was not a
development programme. It was **one decision and one function call**.

**That distance has since been closed.** Guard banding is applied, the decision
rule is printed as ISO/IEC 17025 requires, the test uncertainty ratio appears
per test point with a named warning below 4:1, and the coverage factor is
derived from the effective degrees of freedom rather than assumed to be 2. On
the six matrix rows covering decision behaviour, CalSoft now reads the same as
MET/CAL and CMX.

Two qualifications matter. Commercial guard banding is configurable per
laboratory and per capability; CalSoft applies one rule. And MET/CAL has done
this since 1999, which is the more useful way to read the gap: the arithmetic
was never the hard part, and being late to use it is the finding, not being
unable to.

The second finding is less flattering and cannot be closed by a function call:
**certificate integrity**. Ansur, MET/TEAM and CMX all implement 21 CFR Part 11
electronic signatures with audit trails over the records. CalSoft pastes a
base64 PNG of a handwritten signature into a PDF and prints "Certificate
authenticity can be verified by scanning the QR code" above a verifier that
returns `valid: True` unconditionally (`verification.py:195`). Against any of
the three. This is the widest real gap, and the pattern is already in-house:
the platform runs Ed25519 signing for update manifests
(`cirqen-labs/hq_server/build_package.py:53`, the update server), and the HQ
certificate server already runs per-device API keys with hashing and revocation.

Signing belongs at HQ rather than in `CalSoft`, because HQ is what allocates the
number, and a signature is only as trustworthy as the identity it binds. That
places it alongside the numbering question above: a signed certificate whose
number might later be allocated to another device is not a trustworthy artefact.
`04_PLAN_TO_9_OF_10.pdf` section 6 treats the two as one phase.

---

## 4. Where the gap is real, and what specifically to borrow

| Gap | Borrow from | Concrete change |
|---|---|---|
| Uncertainty excluded from the verdict | MET/CAL guard banding; CMX risk view | Call the existing `calculate_guard_banding`; add a third outcome |
| Binary Pass/Fail hides marginal results | MET/CAL **Indeterminate** | Add an `INDETERMINATE` state when `\|error\|` falls within `U` of the limit |
| No reference-adequacy check | CMX TUR view; MET/CAL | Call the existing `calculate_measurement_capability`; warn below 4:1 |
| Only two Type B components | CMX's **eight** user-defined | Make Type B a related table, not two fixed fields, required anyway for sensitivity coefficients |
| Signature is a pasted image | Ansur / MET/TEAM / CMX 21 CFR 11 | Ed25519 sign the certificate payload; keep the image as decoration only |
| Verification is a stub | All three | Look up the certificate, check the signature, return a truthful `valid` |
| Manual transcription of readings | Ansur plug-ins; Beamex MC6 | Longer-term: ingest from analyzers that expose a serial/USB interface |
| Interval by fixed period | MET/TRACK's **use cycles** | `estimate_calibration_interval` already exists (dead); drift-based intervals are a differentiator, not a catch-up |

---

## 5. What CalSoft should deliberately *not* copy

Worth stating, because the temptation after a comparison like this is to chase
every row.

**Do not chase full MET/CAL procedure automation.** MET/CAL's value is a
scripting language that drives calibrators through thousands of procedures in an
accredited electrical metrology lab. A hospital biomedical workshop calibrating
NIBP modules, infusion pumps and defibrillators against handheld analyzers has a
different problem. Automating *capture* from the analyzers is worth doing;
reimplementing a procedure scripting engine is not.

**Do not pursue 21 CFR Part 11 as a checklist.** It governs FDA-regulated
electronic records. Unless the hospital is in that regime, the parts to adopt
are the *engineering* ones, real electronic signatures, an audit trail over the
record, tamper-evident certificates, not the compliance apparatus.

**Do not add uncertainty components for their own sake.** CMX's eight Type B
slots are useful because process instrumentation has many influence quantities.
CalSoft's gap is not the *count* of components. It is that every component is
assumed to have a sensitivity coefficient of 1, so no influence quantity needing
a coefficient can enter at all (`A1_ENGINEERING_AUDIT.pdf` section 8). Fix the model, then
the count follows.

**Do not drop offline-first to match a commercial architecture.** It is the one
place CalSoft is ahead of all three.

---

## 6. Two realistic targets

**Target A, "Ansur-plus" (weeks).** Match the hospital-QA peer on records
integrity while keeping the uncertainty advantage. Real e-signatures, working
certificate verification, the verdict bug fixed, guard banding and TUR wired in
from the code that already exists. On the section 1 matrix this takes CalSoft past
Ansur on every row except automated capture and multi-device batch testing.

**Target B, "CMX-class uncertainty" (months).** Type B components as a related
table with sensitivity coefficients, Welch-Satterthwaite effective degrees of
freedom replacing the `return max(2, 10)` stub, a stated decision rule printed
on the certificate, and drift-based interval estimation. This is where CalSoft
would have a defensible ISO/IEC 17025 story.

Target A is almost entirely wiring code that has already been written and
documented. Target B is genuine engineering. Nothing here requires research.

---

## 7. Verdict

Against **Fluke Biomedical Ansur**, its true functional peer, CalSoft is
**ahead on measurement mathematics** (Ansur's datasheet documents limit-based
pass/fail and no uncertainty budget), **ahead on offline operation and cost**
and **behind on automated data capture, multi-device testing, electronic
signatures and record integrity**.

Against **Fluke MET/CAL + MET/TEAM** and **Beamex CMX**, CalSoft now has both
the uncertainty engine and the decision machinery: guard banding with three
outcomes, a declared decision rule, TUR warnings, and a derived coverage factor.
What still separates it from them is **not metrology but records**: neither the
certificate nor the signature is tamper-evident, the QR verifier still returns
valid for any input, and there is no configurable per-capability decision rule.

The remaining honest gap is therefore narrower and more specific than it was:
CalSoft computes and uses uncertainty about as well as the commercial products
for the parameters a hospital workshop calibrates, and trails them on
**automated data capture** and on **proving a certificate is genuine**.

The summary as it now stands: **CalSoft computes uncertainty more thoroughly
than the product sold for its job, and now reaches its verdict accordingly.**
The decision-logic gap that defined this comparison is closed. What remains is
the promise of QR verification that the code still does not keep, a records
problem rather than a measurement one, and the one place where all three
commercial products are clearly ahead.

---

## 8. Sources

Vendor documentation consulted 17 September 2026.

- Fluke Biomedical, *Ansur Test Automation Software, Technical Data* (ansur-3.0-datasheet_en.pdf): key features, plug-in list, electronic signature, MS SQL/.NET requirements
- Fluke, *MET/CAL Calibration Management Software* product pages and *New features in recent MET/CAL software versions*: GUM-compliant uncertainty since v6.0 (1999); guard banding yielding Pass / Fail / Indeterminate
- Fluke, *MET/TEAM* and *MET/TRACK Asset Management Software*: ISO/IEC 17025, ANSI Z540, 21 CFR Part 11 support; electronic signatures and audit trail; intervals in days, weeks, months or use cycles
- Beamex, *CMX Calibration Management Software* product pages and *CMX User Manual v2.17.1*: combined expanded uncertainty per calibration point; uncertainty page showing pass-with-risk-of-fail; TUR; up to eight user-defined Type B uncertainties; ISO 17025 / GMP / 21 CFR Part 11 posture
- CalSoft claims are cited to file and line in the working tree throughout

*Vendor capabilities are stated as their own documentation describes them and
were not independently tested. "Not documented" records the absence of a claim
in that vendor's literature, not a proven absence of capability. CalSoft claims
are from direct source reading; no application code was modified.*
