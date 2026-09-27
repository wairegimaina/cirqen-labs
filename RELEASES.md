# Releasing Cirqen

How a version gets from `main` to hospitals. Every release has an entry in
`CHANGELOG.md` and a row in the release log below.

## Version numbers

`MAJOR.MINOR.PATCH` (currently in `Equiper/settings.py`, `APP_VERSION`; the
build fails if the version references disagree).

- PATCH: fixes only, no migrations that change existing data.
- MINOR: new features, backward-compatible migrations.
- MAJOR: anything a site must prepare for (a new server requirement, a
  change HQ must receive first).

## Checklist

1. **Green CI on the release commit**: Django, sync and HQ tests, browser
   end-to-end tests, the coverage gate (80% on calculation, certificate and
   permission code), and the nightly Level 6 load test from the night before.
2. **Licences**: `python helper_scripts/licence_report.py` exits 0; update
   `docs/legal/THIRD_PARTY_LICENCES.md` if anything changed.
3. **Validation**: if the release touches anything in section 7 of
   `docs/validation/ISO17025_VALIDATION.md`, mark it **re-validation
   required** in `CHANGELOG.md` and re-issue the validation pack.
4. **HQ first**: if the release needs an HQ change (new synced tables, a new
   endpoint), deploy HQ and add tables to `TABLES` before any site updates.
5. **Build and sign**: build the package and sign it with the Ed25519 release
   key (`hq_server/build_package.py`). The key never leaves the release
   machine; sites refuse unsigned or altered packages.
6. **Staged rollout**: publish with `rollout_percent: 10` (the pilot and one
   other site). Watch `/api/sync/ops` and error monitoring for 48 hours.
7. **Widen**: 50%, then 100%, 24 hours apart, if nothing is wrong.
8. **Withdraw if needed**: set `"yanked": true` in the release's JSON; sites
   stop receiving it. Fix forward with a new PATCH release: never re-publish
   the same version number.
9. **Tell hospitals**: send the `CHANGELOG.md` entry to each HOD, highlighting
   anything users will notice and any re-validation needed.

## Release log

| Version | Date | CI run | Rollout | Re-validation | Notes |
|---|---|---|---|---|---|
| 1.5.5 | 2026-09-24 | (link) | 100% | n/a (pre-pack) | Work Orders naming, EAT times, AppImage |
| 1.6.0 | (planned) | | | **Required** | See CHANGELOG |
