# CalSoft, Module Reference

### What the calibration module contains, and how a certificate is made

CalSoft is the part of Cirqen that turns manual readings into a numbered
calibration certificate. This document covers what it is built from, and
follows one certificate from the moment a technician opens a session to the
moment a PDF is handed over.

The mathematics behind the numbers is in *The Mathematics of Calibration*; this
document is about the machinery around it.

---

## 1. The measurement hierarchy

Five levels, each narrowing the measurement. Everything else in the module
hangs off this.

$$ \text{Procedure} \rightarrow \text{Parameter} \rightarrow \text{Sub-parameter} \rightarrow \text{Set value} \rightarrow \text{Reading} $$

| Level | What it is | Example |
|---|---|---|
| **Procedure** | The method. Owns the environmental conditions the calibration should be performed under. | "NIBP calibration" |
| **Parameter** | What is measured. Carries the unit, the tolerance, the reference standard's uncertainty, the coverage factor and how many readings to take. | Systolic pressure, mmHg, ±3 |
| **Sub-parameter** | An optional split within a parameter, where one measurement has two channels. | Systolic / diastolic |
| **Set value** | A test point: the nominal value applied by the reference. | 100, 150, 200, 250, 300 mmHg |
| **Reading** | Up to ten raw values at one test point, plus every computed result. | 201, 202, 201, 203, 202 |

**Resolution is not on this list, and that is deliberate.** It belongs to the
device on the day, not to the procedure, the same procedure may be used on a
monitor reading whole mmHg and another reading tenths. It is stored per session
in `SessionParameterResolution`, and a session with no resolution recorded for a
parameter cannot compute that parameter's uncertainty.

---

## 2. The session

A **session** is one event: one device, one procedure, one moment, one
technician, one set of environmental conditions. It carries the device identity
(serial, model, manufacturer, description), the department and workshop, the
actual temperature, humidity and pressure at the time, the overall verdict, and,
once HQ has issued one, the certificate number.

### 2.1 Session states

| State | Meaning |
|---|---|
| `pending_review` | Readings captured; awaiting a reviewer. |
| `approved_pending_certificate` | Approved. A certificate number has been requested from HQ and not yet issued. |
| `approved` | Approved and numbered. A certificate can be issued. |
| `rejected` | Declined, with a reason and comments recorded. |

`approved_pending_certificate` is a real, ordinary state rather than an error.
Certificate numbers are allocated by the HQ server only, so there is always a
moment between approval and issue.

---

## 3. How a certificate is made

### 3.1 Capture

The technician selects equipment and a procedure, records the environmental
conditions, and enters readings for every test point. Environmental conditions
are compared against the procedure's requirements and any deviation beyond
2 °C, 10 %RH or 1 kPa is surfaced as a warning, recorded, not blocking.

As readings are typed, the live preview calls the same uncertainty calculation
the saved record will use, so the figures on screen and the figures stored are
computed identically.

### 3.2 Reduction

On submit, each test point is reduced to a complete uncertainty budget:

$$ \bar{x},\; s,\; u_A,\; u_{\text{res}},\; u_{\text{ref}},\; u_c,\; U,\; k,\; \nu_{\text{eff}} $$

The coverage factor is **derived** from the effective degrees of freedom rather
than assumed, with the parameter's configured factor acting as a floor. The
deviation from nominal is computed and compared with the tolerance.

The session's verdict is **strict conformity**: a single test point outside
tolerance fails the session. A point left blank also fails it, because an
unmeasured point is not a passed one.

### 3.3 Review and approval

A reviewer with the `approve_session` permission approves or rejects. On
approval the session moves to `approved_pending_certificate`, a
`PendingCertificate` row is queued, and the linked schedule is advanced.

**No certificate number is allocated locally.** HQ is the only allocator. The
field application never mints one, because two sites allocating from their own
local sequences produce the same number and collide on sync.

### 3.4 Issue

HQ allocates the next number, writes it to the session and flips the status to
`approved`. The client syncs it down, and the `PendingCertificate` row is marked
complete. A recovery loop re-requests any number that has not arrived.

### 3.5 Rendering

The PDF is assembled from eight composable parts, in this order:

| Section | What it carries |
|---|---|
| Title and number | Certificate or reference number, issue date |
| Three-section table | Device, customer and calibration details |
| Calibrator & standard | Which reference standards were used, and their certificates |
| Linearity analysis | Slope, intercept, largest deviation from the fitted line, and a residual chart |
| Drift analysis | Historical drift per parameter, graded against tolerance |
| Results & statistics | Per point: set value, mean, standard deviation, error, tolerance, TUR, verdict |
| Failure analysis | Only when points failed: per-parameter failure rates and recommendations |
| Uncertainty budget | Per point: n, Type A, Type B, Reference, Combined, Expanded, k |
| Traceability | The unbroken chain to SI |
| Notes | Decision rule, coverage factor, measurement-capability warnings |
| Signatures | Who performed and who approved |
| Footer | Identity and QR reference |

A watermark is drawn over the content on every page, and a QR code carries a
summary for reference.

### 3.6 What the verdict means on the page

Two independent things are reported, and they are not the same:

- **Conformity**, did the device pass? Strict: one failed point fails it.
- **Failure rate**, how widespread is the failure? Above 40 % indicates a
  device fault rather than isolated drift, and changes the wording of the notes.

The rate is a triage signal for the workshop. It never decides conformity.

---

## 4. The parts of the module

### 4.1 Data model

| Model | Holds |
|---|---|
| `CalibrationProcedure` | The method, environmental requirements, equipment mappings |
| `CalibrationParameter` | Unit, tolerance, reference uncertainty, coverage factor, reading count |
| `SubParameter` | An optional channel split, with its own tolerance |
| `SetValue` | A test point |
| `CalibrationSession` | One calibration event and its verdict |
| `SessionParameterResolution` | The resolution in force for a parameter in that session |
| `CalibrationReading` | Ten raw slots plus nine computed results |
| `HistoricalCalibration` | A flattened copy of every computed reading, for drift |
| `PendingCertificate` | A queued request for a number from HQ |
| `CalibrationAuditLog` | Who did what, in readable form |
| `Standard`, `StandardParameter` | Reference standards and their accredited uncertainties |

### 4.2 The calculation engine

One module, pure arithmetic, no database, no framework, so it can be tested
directly. It provides the statistics, the three uncertainty components, the
combination and expansion, the effective degrees of freedom, the test
uncertainty ratio, the guarded conformity decision, linearity by least squares,
drift grading against tolerance, and robust outlier detection.

Every formula in it is pinned to a hand-computed test vector.

### 4.3 Views

Session capture and submission, review and approval, certificate listing and
download, procedure and parameter management, standards import from Excel, a
dashboard, and a JSON API used by the live preview.

### 4.4 The PDF generator

Composed from eight mixins, styles, results, QR, header, sections, analysis,
signatures, drift, so each concern is separately readable and separately
testable.

---

## 5. What a certificate guarantees, and what it does not

Worth stating plainly, because a certificate is a document people rely on.

**It does guarantee**

- the arithmetic is correct and reproducible from the printed figures;
- the verdict is strict conformity, stated with the decision rule used;
- the coverage factor shown is the one actually applied;
- the number was allocated by one authority and was not invented locally.

**It does not yet guarantee**

- **tamper-evidence.** The QR code carries a summary for reference. It is not a
  cryptographic signature, and scanning it does not by itself prove the document
  is genuine. Confirm a certificate number against the issuing record.
- **a signed approver identity.** The signature block records who approved and
  when, as an image, not as a cryptographic signature.

These are the outstanding items in the platform plan, and the certificate no
longer claims otherwise: the note and footer state what the QR code is for
rather than overstating it.

---

## 6. Known limits

Recorded here rather than left to be discovered.

| Limit | Consequence |
|---|---|
| **Sensitivity coefficients are not modelled** | Every component is assumed to act one-for-one on the measurand, so an influence acting indirectly, a temperature coefficient, lead resistance, hydrostatic head, cannot enter the budget. |
| **Type B components are fixed at two** | Resolution and reference standard only. |
| **The stored verdict is two-valued** | The certificate reports `PASS`, `FAIL` and `INDETERMINATE`; the database stores pass or fail. |
| **Minimum readings are not enforced from below** | A parameter declaring five readings will certify on two. Two readings give one degree of freedom and a coverage factor of 12.706, which is correct and not a certifiable measurement. |
| **The deviation sign convention** | Confirm the column heading before interpreting the sign; see *The Mathematics of Calibration* section 9.1. |

---

*Cirqen Calibration Software. The mathematics behind these figures is derived in
full in the companion volume, The Mathematics of Calibration.*
