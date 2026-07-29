# Calibration scheduling — how it fits together (and known divergences)

This note documents the scheduling/cert flow after the `grouping.py`
consolidation. **No behaviour was changed** in that refactor: shared concepts
now live in one module, but every caller kept its exact prior filters. The
inconsistencies listed under "Divergences" are pre-existing and are recorded
here for a future go/no-go decision — they were **not** silently unified.

## The canonical cert → complete → reschedule path

```
Job Card submitted
   → CalibrationSession approved
   → certificate_number assigned  (CalSoft.models.CalibrationSession)
   → post_save signal auto_complete_on_certificate   (instant_reconciliation.py)
        • finds the matching pending CalibrationSchedule
        • sets completed_date = actual cal date  (scheduled_month is NOT changed)
        • status = 'completed'  +  immediate lock
        • trigger_sync_for_changes(...)  → HQ
   → post_save signal instant_reschedule_on_completion  (instant_reconciliation.py)
        • group-aware: creates the NEXT schedule at scheduled_month + period
          with the same planning_logic
```

Overlapping mechanisms that must stay consistent with the above (all now share
`grouping.next_period_month` for the "next month" decision):

- `tasks.auto_advance_completed_calibrations` — Celery batch equivalent of the
  instant signal (used for catch-up / periodic runs).
- `locker._reschedule_locked_group` / `_reschedule_single_schedule` — reschedule
  after a group is locked.
- `reconciliation._basic_auto_reschedule` — fallback when
  instant_reconciliation is unavailable.

## The shared brain: `grouping.py`

Pure (no DB, unit-tested): `group_id_for`, `group_key`, `planning_year`,
`next_period_month`, `clamp_far_future_month`, `find_optimal_month`,
`is_protected`, `completion_stats`, `next_certificate_number`.

DB (one place): `group_members_qs(...)` — takes **explicit** `require_*` flags so
each caller reproduces its historical filters.

## Planning-logic vocabulary (FIXED)

Two vocabularies exist for the same two concepts:

- runtime cycle (`instant_reconciliation`, `locker`, `grouping`): `'department'` / `'description'`
- reorganizer + UI + model defaults: `'date_based'` / `'description_based'`

They previously did NOT reconcile: the Smart Reorganizer stamps
`planning_logic='date_based'` when you reorganize "by department", but the runtime
group-math tested `== 'department'`, so those schedules were regrouped by
**description** during the PPM auto-reschedule cycle. Fixed via
`grouping.canonical_logic()` (`date_based→department`, `description_based→description`,
pass-through otherwise), applied in `group_field_lookup` / `group_id_for` /
`group_key`. No data migration — stored values are normalised at read time. Pinned
by `tests/test_cycle.py::DateBasedGroupsByDepartmentTests`.

Special categories are now also wired into the Smart Reorganizer (view → task →
`is_special` in the fallback month assignment) and the reorg modal, matching the
initialize flow. Previously special classes only applied at initialization.

## Divergences (pre-existing — decide later, do NOT assume they're intentional)

The "members of a group in a month" query is filtered **differently** depending
on which module you came through. This is the most likely source of
"scheduling doesn't make sense" symptoms.

| Caller | equipment active? | schedule active? | status filter |
|---|---|---|---|
| `tasks._get_group_members` | no | **yes** (`active_status=True`) | none |
| `instant.find_group_members` | **yes** (`equipment__active_status`) | no | none |
| `locker._basic_group_status` | **yes** | **yes** (both) | none |
| `instant.get_group_scheduled_month` | **yes** | no | `pending`/`pushed`, `scheduled_month >= today` |
| `reconciliation._basic_grouping_check` | **yes** | no | `pending`/`pushed` |

Consequences to weigh when deciding a canonical definition:
- A schedule whose **equipment** was deactivated is still counted by
  `tasks`, but excluded by `instant`/`locker` — so "is the group complete?" can
  answer differently in the two paths, which can both block locking and cause
  premature/late rescheduling.
- A schedule with `active_status=False` (soft-deleted schedule) is excluded by
  `tasks`/`locker` but **included** by `instant.find_group_members`.

Recommended (needs approval before implementing): pick one canonical
`group_members` definition — most likely `equipment__active_status=True` **and**
schedule `active_status=True` (the strictest, `locker`'s) — switch all callers to
it, and re-run the cycle tests. That is a behaviour change and is deliberately
out of scope for the consolidation refactor.
