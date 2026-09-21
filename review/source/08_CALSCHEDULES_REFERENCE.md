# calSchedules, Module Reference

### How calibration work is planned, grouped and advanced

calSchedules decides *when* each device is calibrated and *with whom*. CalSoft
performs the calibration; this module plans it, keeps devices that should be
visited together in the same month, and rolls a group forward once it is done.

This document covers what the module contains, how a schedule moves through its
life, and, in section 7, what is not yet finished, stated in full rather than left to
be discovered.

---

## 1. The central idea: groups

A schedule is not planned per device. It is planned per **group**, because a
technician visiting a ward calibrates everything in that ward on one trip.

$$ \text{group} \;=\; (\text{grouping key},\; \text{scheduled month}) $$

Two grouping keys are supported:

| Planning logic | Groups by | Suits |
|---|---|---|
| **Department** | Where the device lives | A visit to one ward |
| **Description** | What kind of device it is | One technician, one device type, across wards |

Everything else follows from this. A group **moves together**, **completes
together**, and **advances together**.

### 1.1 Two vocabularies, one meaning

The same two concepts carry two sets of names, for historical reasons:

| Stored as | Also stored as | Means |
|---|---|---|
| `date_based` | `department` | Group by department |
| `description_based` | `description` | Group by equipment type |

These are normalised when read, so a schedule stamped either way groups the
same. No migration was needed and stored values are left alone.

### 1.2 Who counts as a member

**One rule: the equipment and the schedule must both be active.**

This matters more than it sounds. Two parts of the system previously disagreed,
one required the equipment active and ignored the schedule flag, the other the
reverse, so a retired device held its group open in one path and a deleted
schedule held it open in the other. Either way the group never read as complete
and never advanced. There is now one definition, and it is the default, so a new
caller cannot reintroduce the split by omission.

---

## 2. The life of a schedule

| Status | Meaning |
|---|---|
| `pending` | Planned, not yet started |
| `pushed` | Deliberately moved to a later month |
| `in_progress` | Work has begun |
| `pending_approval` | Calibration done, awaiting review |
| `completed` | Approved and closed |
| `overdue` | Past its due month |

### 2.1 Due dates fall at the end of the month

A calibration is due **within a month**, not on a particular day of it:

$$ \text{due} \;=\; \text{month-end}\!\left(\text{last calibration} + \text{interval}\right) $$

A device calibrated on 3 March with a 12-month interval is due **31 March** the
following year, not the 3rd. The workshop therefore has the whole month to fit
the visit, and every calibration performed in a month shares one due date
which is what makes a group movable.

Intervals are calendar months, not day counts. Adding `30 × months` treats a
year as 360 days, so a yearly calibration drifts about five days earlier each
cycle and slips into the previous month after roughly six years.

### 2.2 Completion and advance

When the **last** member of a group completes:

1. the completed schedule is locked immediately;
2. the whole group advances to `scheduled month + interval`, keeping its
   planning logic;
3. the new schedules are stamped `signal`, marking them as automatically
   created.

The new month is based on the **scheduled** month, not the actual calibration
date, so calibrating early or late does not drag the schedule with it.

---

## 3. Regrouping

Two different operations, often confused.

### 3.1 Moving a selection

Move a chosen set of schedules into another month, a ward being refurbished, or
devices that were scheduled apart and should be visited together. Previewable:
the preview runs the same code and rolls back, so what you are shown is what
will happen, including what will be refused and why.

**Alignment stands down during a deliberate move.** The system normally enforces
"equipment never jump between months within a group", which is right for
accidental drift and fatal for an intentional move, moving the first member
would snap it back to where the rest still are, so the move could never start.

### 3.2 Changing the planning logic

Re-normalise every pending schedule when the whole estate switches from
department-based to description-based planning, or back. This is an estate-wide
operation and a different decision from moving one group.

### 3.3 What can never be moved

Three protections, applied identically by both operations:

| Protected | Why | Remedy |
|---|---|---|
| **Completed** | A finished calibration is history | None. It is a record |
| **Locked** | Deliberately frozen | Unlock it first |
| **Automatically created** (`signal`, `locker`, `job_card`) | Will be regenerated on the next cycle | Change the cycle, not the row |

The reason is always reported, not just the refusal, because the remedy differs.

---

## 4. The group view

Groups are shown as cards, because a group is what the scheduler acts on:
completion out of total, a progress bar, how many remain, where the group rolls
to when it finishes, and per-member state with protected rows marked and
explained. The least-covered groups sort first.

---

## 5. The parts of the module

| Part | Responsibility |
|---|---|
| `grouping` | The shared brain: group identity, membership, next period, due dates, certificate-number format. Pure, no database above the one query builder, and unit-tested without one. |
| `regroup` | Targeted moves and the group snapshot |
| `instant_reconciliation` | Signals: complete on certificate, advance on group completion, align to group |
| `locker` | Freezing completed work and rescheduling locked groups |
| `tasks` | Batch equivalents for catch-up and periodic runs |
| `reconciliation` | Fallback paths when the instant machinery is unavailable |
| `views` | Dashboard, schedules, groups, bulk operations, exports |

---

## 6. Protections in force

| Protection | Guards against |
|---|---|
| Three-layer move protection | Rewriting history or fighting the auto-scheduler |
| Group alignment | A device drifting out of its group by accident |
| Immediate locking on completion | A finished calibration being reorganised |
| Conflict detection on move | Two schedules for one device in one month |
| Advisory-locked numbering at HQ | Two certificates sharing a number |

---

## 7. What is not yet finished

Stated in full. Each of these is real, and none is hidden behind a reassuring
default.

### 7.1 Two models for one concept

There are **two** `CalibrationSchedule` models, one in calSchedules, one in
CalSoft. The machine reports read one; calibration sessions link to the other.
They now agree on due dates, but they remain two models for one idea, and a
future change must be made in both. **Not yet reconciled.**

### 7.2 Overlapping reschedule mechanisms

Five paths can advance a schedule: the instant signal, the batch task, the
locker, the reconciliation fallback, and a manual move. They share the
next-period calculation, so they agree on *when*. They have not been
consolidated into one path, so they can still differ in *whether* they fire.

### 7.3 Remaining membership divergences

The canonical rule is applied in the paths that decide completion and
alignment. Dashboard counters elsewhere still use their own filters. Those are
display totals rather than scheduling decisions, so they cannot block a group
but a count on one screen may not match a count on another.

### 7.4 No department-level scheduling view

Coverage by department is shown on the HOD dashboard. The scheduling screens
themselves are workshop-wide, so there is no way to plan one department's
calendar in isolation.

### 7.5 Intervals are limited to 6 or 12 months

The interval field offers two values. A device needing a 3-month or 24-month
cycle cannot be represented, and drift-based interval estimation, which the
calculation engine can compute, is not wired into planning.

### 7.6 No capacity planning

Nothing checks whether a month's scheduled work can actually be done by the
people available. A reorganisation can place more work in one month than the
workshop can absorb, and nothing objects.

### 7.7 Overdue is computed, not stored

`overdue` exists as a status but is derived on read from the due date. A
schedule sitting in `pending` past its month is overdue in every display and
still `pending` in the database, so a report written against the stored status
will undercount.

---

*Cirqen Calibration Software. The calibration this module plans is documented in
CalSoft, Module Reference; the mathematics in The Mathematics of Calibration.*
