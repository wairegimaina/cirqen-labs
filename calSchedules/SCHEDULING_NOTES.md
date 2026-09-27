# Calibration scheduling

Calibration and PPM are scheduled by one system: the scheduling plans in
`scheduling/` (see `scheduling/planner.py` and `scheduling/engine.py`).

- Every workshop has an active plan per programme (PPM, calibration). The
  first is created automatically: grouped by equipment description, months
  spread evenly across the year.
- A plan groups equipment by **department** or by **description**, gives each
  group its months, and each description how often it is due.
- A completed schedule (job card, certificate, or the Complete button) gives
  the device its next one from the plan. Nothing waits for the rest of a group.
- Changing the grouping or the months is a new plan version: preview it, then
  activate. Completed schedules never move; open ones that no longer fit are
  moved and each records why.
- `scheduling.tasks.run_plans` runs every 30 minutes: it creates missing
  plans, places anything unscheduled and picks up completions that arrived
  through sync.

The screens are under **Scheduling** in the sidebar, and a Scheduling panel
sits at the top of the PPM and calibration pages.

What was removed, and why: the group aligner (snapped every new schedule to
its group's most common month, so a new group landed in the current month),
the smart organiser and normaliser (aligned groups to the month most of the
group was already in, and the aligner undid their moves), the locker,
reconciliation and group-completion rescheduling (a group advanced only when
every member was done, so one missing device held the rest back).
