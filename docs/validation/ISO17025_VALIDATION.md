# Cirqen calibration software: validation pack (ISO/IEC 17025:2017)

| | |
|---|---|
| Document | CIRQ-VAL-001 |
| Applies to | Cirqen desktop and server editions, calibration module (CalSoft), certificate numbering and verification at Cirqen HQ |
| Status | Draft for the laboratory's quality manager to review, complete and sign |
| Re-issue | Whenever section 7 says a change needs re-validation |

ISO/IEC 17025:2017 clause 7.11.2 requires a laboratory to validate software
used to collect, process, record, report or store calibration data before
it is used, and after any change. This pack gives the laboratory what it
needs to do that: what the software is meant to do, how each requirement is
tested, a worked example it can repeat by hand, and the controls on change.

The supplier (Cirqen Labs) runs the automated tests listed here on every
change. **The laboratory's own acceptance** is sections 5 and 8: repeat the
worked example on the installed copy, record the result, and sign.

## 1. Intended use

Cirqen records the calibration of medical equipment in a hospital
biomedical engineering department and produces the calibration certificate.
For each calibration it:

1. Takes the procedure (parameters, set values, tolerances, number of
   readings) chosen for the device.
2. Takes the reference standards used, and refuses a standard whose own
   calibration is overdue.
3. Takes the technologist's readings at each set value.
4. Computes the mean, error, uncertainty budget and conformity verdict.
5. Routes the result for approval, then issues a numbered, signed PDF
   certificate with a QR code that anyone can use to check it at HQ.
6. Keeps the certificate exactly as issued, and the audit trail of who did
   what.

Out of scope: the laboratory's choice of procedure, tolerances and
reference standards, and the technical competence of the person taking the
readings. The software computes from what it is given.

## 2. Configuration under validation

Record on the sign-off sheet (section 8):

- Cirqen version: shown in the app under Settings, Updates.
- Edition: desktop (one PC) or server (see `docs/SERVER_MODE.md`).
- Operating system and database (PostgreSQL version).
- Site certificate prefix (Settings, Site details).

## 3. Calculation method

Implemented once, in `CalSoft/utils.py` (`compute_uncertainty_budget`). The
value shown while the technologist types and the value saved both come from
that one function.

| Quantity | Method |
|---|---|
| Mean | Arithmetic mean of the n readings |
| Standard deviation s | Sample standard deviation (n − 1) |
| Type A, u_A | s / √n |
| Resolution, u_res | resolution / √12 (rectangular, full step) |
| Reference, u_ref | expanded uncertainty from the standard's certificate / k stated on that certificate |
| Combined, u_c | √(u_A² + u_res² + u_ref²) |
| Coverage factor k | Student's t for ~95% at the Welch-Satterthwaite effective degrees of freedom (only Type A has finite degrees of freedom, n − 1); never less than the stated k (2) |
| Expanded, U | k × u_c |
| Error | set value − mean (see section 9, open decision) |
| TUR | tolerance / U; below 4:1 the certificate flags it |
| Verdict | Guard-banded, three outcomes: PASS if \|error\| ≤ T − U; FAIL if \|error\| ≥ T + U; otherwise INDETERMINATE |

Every stored result is rounded to 6 decimal places, half up.

**One-sided limits.** A parameter can be judged against a single limit
instead of ± tolerance: "at most" (upper) or "at least" (lower) the set
value, which is then the limit L. With expanded uncertainty U, an upper
limit gives PASS if x ≤ L − U, FAIL if x ≥ L + U, otherwise INDETERMINATE; a
lower limit gives PASS if x ≥ L + U, FAIL if x ≤ L − U. TUR does not apply.

**Readings taken by Fluke Ansur (instrument rule).** An Ansur session holds
one analyser measurement x per test point, so there is no Type A:

| Quantity | Method |
|---|---|
| u_spec | (\|x\| × accuracy % / 100 + fixed part) / √3, from the analyser's datasheet (rectangular) |
| u_res | analyser resolution / √12 |
| u_cal | expanded uncertainty on the analyser's certificate / its k |
| Reference component (stored) | √(u_spec² + u_cal²), the analyser's whole contribution |
| u_c | √(u_spec² + u_res² + u_cal²), from full-precision components |
| k, U | k = 2, U = 2 × u_c |
| Error, verdict | as above; Ansur's own Pass/Fail is stored beside Cirqen's verdict and never replaces it |

The minimum-readings rule (R4) does not apply to Ansur sessions: the source
is recorded on the session and printed on the certificate.

Minimum readings: each set value needs at least the number of readings the
procedure asks for, and never fewer than 3 (at most 10 are stored). The
software will not save a calibration with a shortfall and names the rows
that are short.

## 4. Requirements and how each is tested

The test modules are in the Cirqen source. "Django" tests run with
`python manage.py test`; "sync" tests with `pytest sync/tests`; "HQ" tests
in the HQ server repository; "e2e" drive a real browser (`e2e/tests.py`).
All run on every change in CI.

| # | Requirement | Tests |
|---|---|---|
| R1 | Uncertainty budget, k and TUR are computed as in section 3 | `CalSoft/test_validation_example.py`, `CalSoft/test_metrology.py`, `CalSoft/test_calculations.py`, `CalSoft/test_calibration_helpers.py` |
| R2 | Conformity verdict is guard-banded and shown on the certificate | `CalSoft/test_certificate_verdict.py` |
| R3 | Drift and linearity checks | `CalSoft/test_drift_and_linearity.py` |
| R4 | A calibration cannot be saved with fewer readings than required | `CalSoft/test_readings_and_issued_copy.py` |
| R5 | A reference standard past its due date cannot be used | `CalSoft/test_reference_standard_due.py` |
| R5a | Only a calibration-centre reviewer or the HOD may approve, and never the person who performed the calibration (clause 7.8.1.2) | `CalSoft/test_calibration_flow.py` |
| R6 | Full calibration journey: readings, approval, certificate | `CalSoft/test_calibration_flow.py`, `e2e/tests.py` (`CalibrationJourney`) |
| R7 | An issued certificate is kept byte-for-byte, with a SHA-256 fingerprint, and a changed file is refused, not served | `CalSoft/test_readings_and_issued_copy.py` |
| R8 | Certificate numbers are unique across all sites; a clash made offline is repaired by reissuing under a new number, with an audit entry | HQ `test_cert_numbering.py`, HQ `test_certificate_number_protection.py`, sync `test_cert_conflict_guard.py` |
| R9 | The QR code on a certificate links to HQ's public check, which confirms number, device and date | `CalSoft/test_certificate_qr_link.py`, `CalSoft/test_verification_payloads.py`, HQ `test_certificate_verify.py` |
| R10 | Bulk download returns each approved certificate as issued | `CalSoft/test_bulk_download.py` |
| R11 | People see only the records for their workshop or department | `core/tests/test_scope_leaks.py`, `core/tests/test_cross_workshop_access.py`, `machineReports/test_scoping.py` |
| R12 | Actions are recorded in the audit trail | `audit_log/tests.py`, HQ `test_audit_source.py` |
| R13 | Sign-in security: lockout after repeated failures, idle sign-out, two-factor, signatures stored safely | `users/test_throttle.py`, `users/test_idle_timeout.py`, `users/test_two_factor.py`, `users/test_signature_storage.py` |
| R14 | Records made offline reach HQ intact; the newer edit wins, deletes carry through | sync `test_conflict_resolver.py`, sync `test_upload_download.py`, HQ `test_upsert_lww.py`, HQ `test_apply_change_simplified_lww.py`, HQ `test_soft_delete_cascade.py`, HQ `test_null_propagation.py` |
| R15 | Backups are taken and are readable | `core/tests/test_backup_verify.py` |
| R16 | Only updates signed by Cirqen Labs are installed | `updates/test_package_signing.py`, `updates/test_updater_flow.py` |
| R17 | An Ansur record is imported only when its job number, serial, template and analysers (registered, in date) match, it is not aborted or incomplete, and every set value and tolerance of the procedure has exactly one result; otherwise nothing is saved and every reason is given | `CalSoft/test_ansur_import.py`, `CalSoft/test_ansur_start.py` |
| R18 | One-sided verdicts and the single-reading budget are computed as in section 3 | `CalSoft/test_ansur_maths.py` (pins section 5.1) |
| R19 | The imported Ansur record and PDF are kept with their SHA-256; Ansur's PDF is attached to the issued certificate only if it still matches | `CalSoft/test_ansur_import.py`, `CalSoft/test_ansur_review.py` |
| R20 | An Ansur session cannot be approved by its performer, and a disagreement between Ansur's verdict and Cirqen's must be acknowledged by the reviewer, with an audit entry | `CalSoft/test_ansur_review.py` |

## 5. Worked example (repeat this by hand)

One set value of 120 (any unit), tolerance ±3, resolution 0.1, reference
standard certificate uncertainty 0.3 at k = 2.

Readings: **120.2, 120.6, 120.4, 120.8, 120.5**

| Step | Working | Result |
|---|---|---|
| Mean | 602.5 / 5 | 120.500000 |
| s | deviations −0.3, 0.1, −0.1, 0.3, 0.0; Σd² = 0.20; √(0.20 / 4) | 0.223607 |
| u_A | 0.223607 / √5 | 0.100000 |
| u_res | 0.1 / √12 | 0.028868 |
| u_ref | 0.3 / 2 | 0.150000 |
| u_c | √(0.100000² + 0.028868² + 0.150000²) | 0.182574 |
| ν_eff | u_c⁴ / (u_A⁴ / 4) | ≈ 44 |
| k | t₉₅ at ≈ 44 degrees of freedom | 2.021 |
| U | 2.021 × 0.182574 | 0.368982 |
| Error | 120 − 120.500000 | −0.500000 |
| TUR | 3 / 0.368982 | 8.13 |
| Limits | acceptance 3 − U, rejection 3 + U | 2.631018, 3.368982 |
| Verdict | \|−0.5\| ≤ 2.631018 | PASS |

These values are pinned by `CalSoft/test_validation_example.py`, so any
change to the software that moves them fails the supplier's tests before it
can ship.

Laboratory acceptance: create a test procedure with these values, calibrate
a test device with these five readings, and compare the certificate with
the table. Record the result in section 8.

### 5.1 Worked example for an Ansur reading

Defibrillator energy, set 360 J, tolerance ±36 J. Ansur measured
**352.4 J**. Analyser accuracy ±(1% of reading + 0.1 J), resolution 0.1 J,
analyser certificate 1.0 J at k = 2.

| Step | Working | Result |
|---|---|---|
| u_spec | (0.01 × 352.4 + 0.1) / √3 | 2.092317 |
| u_res | 0.1 / √12 | 0.028868 |
| u_cal | 1.0 / 2 | 0.500000 |
| u_c | √(2.092317² + 0.028868² + 0.5²) | 2.151424 |
| U | 2 × u_c | 4.302848 |
| Error | 360 − 352.4 | 7.600000 |
| TUR | 36 / 4.302848 | 8.37 |
| Verdict | 7.6 ≤ 36 − 4.302848 = 31.697152 | PASS |

One-sided: earth leakage at most 500 µA, measured 112 µA, accuracy
±(1% + 1 µA), resolution 1 µA, certificate 2 µA at k = 2: U = 3.213389,
PASS because 112 ≤ 500 − 3.213389 = 496.786611.

## 6. Test log procedure

For each release the supplier keeps:

1. The CI run for the release commit (all Django, sync, HQ and e2e tests;
   coverage of the calculation, certificate and permission modules at 80%
   or more). The run's address goes in `RELEASES.md`.
2. The release's entry in `CHANGELOG.md`, which says whether it touches
   anything in section 7's re-validation list.

For each installation or update the laboratory keeps:

1. The sign-off sheet (section 8), with the worked-example certificate
   attached.
2. If the update is marked "re-validation required", a new sign-off before
   the updated copy is used for customer certificates.

Keep both for the laboratory's record retention period (at least the life
of the equipment plus one calibration interval, or as the quality manual
says).

## 7. Change control

- **Signed updates.** Update packages are signed with Cirqen Labs' Ed25519
  key; the installed copy checks the signature and refuses anything
  unsigned or altered (R16).
- **Staged rollout.** Each release has a `rollout_percent`; a new version
  goes first to a small share of sites, and can be withdrawn (`yanked`)
  before the rest receive it.
- **Re-validation.** A release that changes any of the following is marked
  "re-validation required" in `CHANGELOG.md`, and this pack is re-issued:
  - `CalSoft/utils.py` (calculations, k, verdict)
  - the certificate PDF layout or content (`CalSoft/pdf_generators/`)
  - certificate numbering, issued-copy storage or verification
  - the readings-entry rules
  - the Ansur import checks, single-reading budget or one-sided verdicts
    (`CalSoft/ansur/`, `CalSoft/utils.py`)
  Other releases need only the release note, and a laboratory may choose to
  repeat section 5 anyway.
- **Supplier review.** Changes are reviewed and must pass all tests in CI
  before release.

## 8. Laboratory sign-off

| | |
|---|---|
| Laboratory | |
| Cirqen version / edition | |
| Worked example repeated on | (date) |
| Results match section 5 | Yes / No (attach certificate) |
| Deviations and actions | |
| Validated by (name, signature, date) | |
| Approved by quality manager (name, signature, date) | |

## 9. Open decisions for the laboratory

1. **Sign of the error.** Cirqen reports error as **set value − mean**
   (the reference minus the device). Many biomedical laboratories report
   **mean − set value** (device reading minus reference, the "indication
   error"). The verdict is unaffected (it uses |error|); only the printed
   sign differs. Confirm which convention the quality manual uses; if it is
   the other one, Cirqen Labs will change it in a release marked
   "re-validation required".
2. **Coverage factor.** Cirqen uses the larger of 2 and the t-factor from
   the effective degrees of freedom. Confirm this matches the quality
   manual's uncertainty procedure.
