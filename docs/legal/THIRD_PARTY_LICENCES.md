# Third-party licences

> **Draft.** Compiled from package metadata. Have the advocate reviewing the
> EULA confirm the LGPL section before the first paid release.

Regenerate the Python table before each release with
`python helper_scripts/licence_report.py` (it exits non-zero if a GPL, AGPL
or undeclared licence appears).

## Decisions taken

- **zipstream-new (GPL-3.0) removed.** It was used only for the bulk
  certificate download; the standard library's `zipfile` now does the job
  (`CalSoft/view_modules/certificates.py`, tested by
  `CalSoft/test_bulk_download.py`). Shipping a GPL-3.0 package inside the
  proprietary desktop build would have required releasing Cirqen's source
  under the GPL.
- **chartjs (PyPI, no licence declared) removed.** It was listed in
  `INSTALLED_APPS` but never used. Charts use the Chart.js JavaScript
  library (MIT), which is separate.

## LGPL components and what Cirqen must do

| Component | Licence | Where |
|---|---|---|
| PySide6 / Qt 6 | LGPL-3.0 (Cirqen uses it under LGPL, not GPL) | Desktop app window and tray |
| psycopg2-binary (and the bundled libpq) | LGPL-3.0 with exceptions | Database driver |
| docxtpl | LGPL-2.1 | Work order Word documents |

Obligations, met as follows:

1. **Dynamic linking, replaceable.** The desktop build is a PyInstaller
   one-directory build (`COLLECT`, `exclude_binaries=True` in `build.py`):
   the Qt and PySide6 libraries sit as separate files in the install folder,
   so a user can replace them with their own build. Do not switch to a
   one-file build without revisiting this.
2. **Notices.** Ship this file and the full LGPL-3.0 and LGPL-2.1 texts (and
   the GPL-3.0 text, which LGPL-3.0 incorporates) in the installer, and show
   "Third-party licences" in the app's About screen.
3. **Source.** Provide the source of the exact PySide6, Qt, psycopg2 and
   docxtpl versions shipped, or a written offer valid for three years. The
   simplest route: a page on the Cirqen Labs website linking to the
   upstream source archives of each shipped version, kept for three years.
4. **Modifications.** Cirqen ships these unmodified. If that ever changes,
   the modified source must be published.
5. **EULA terms.** The EULA must not forbid modifying these libraries or
   reverse engineering needed to debug such modifications (LGPL-3.0 section
   4). `EULA.md` carries this carve-out.

## Bundled programs (desktop build)

| Program | Licence | Notes |
|---|---|---|
| PostgreSQL 16 | PostgreSQL Licence (permissive) | Include its copyright notice |
| Redis 5 for Windows (tporadowski port) | BSD-3-Clause | Include its notice |
| Python runtime | PSF Licence | Include its notice |

## Front-end libraries (served with the app)

| Library | Licence |
|---|---|
| Bootstrap 5 | MIT |
| jQuery | MIT |
| Chart.js | MIT |
| Plotly.js | MIT |
| React / ReactDOM (via Dash) | MIT |
| Select2 | MIT |
| Flatpickr | MIT |
| signature_pad | MIT |
| XRegExp | MIT |
| Font Awesome Free | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT: keep the attribution comment in the CSS |
| Boxicons | MIT (icons CC BY 4.0) |

## Python packages (from installed metadata)

| Package | Version | Licence |
|---|---|---|
| Django | 5.2.17 | BSD-3-Clause |
| channels | 4.3.2 | BSD License |
| channels-redis | 4.3.0 | BSD |
| daphne | 4.2.3 | BSD License |
| celery | 5.6.3 | BSD-3-Clause |
| django-celery-beat | 2.9.0 | BSD License |
| redis | 8.1.0 | MIT |
| psycopg2-binary | 2.9.13 | GNU Library or Lesser General Public License (LGPL) |
| django-redis | 7.0.0 | BSD-3-Clause |
| djangorestframework | 3.18.1 | BSD-3-Clause |
| django-cors-headers | 4.9.0 | MIT |
| fastapi | 0.141.1 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| httpx | 0.28.1 | BSD License |
| python-multipart | 0.0.32 | Apache-2.0 |
| requests | 2.34.2 | Apache Software License |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| flask | 3.1.3 | BSD-3-Clause |
| reportlab | 5.0.1 | BSD License |
| pypdf | 6.19.0 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT License |
| python-docx | 1.2.0 | MIT License |
| docxtpl | 0.20.2 | LGPL-2.1-only |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | 3.0.6 | BSD License |
| matplotlib | 3.11.2 | Python Software Foundation License |
| plotly | 7.1.0 | MIT |
| dash | 4.4.1 | MIT License |
| django-plotly-dash | 2.5.1 | MIT License |
| Pillow | 12.3.0 | MIT-CMU |
| qrcode | 8.2 | BSD License; Other/Proprietary License |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License |
| pytz | 2026.4 | MIT License |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| kafka-python | 3.0.11 | Apache-2.0 |
| psutil | 7.2.2 | BSD-3-Clause |
| networkx | 3.6.1 | BSD-3-Clause |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| appdirs | 1.4.4 | MIT License |
| PySide6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| django-debug-toolbar | 8.0.0 | BSD License |
| django-extensions | 4.1 | MIT |
| sentry-sdk | 2.70.0 | MIT |
| pytest | 9.1.1 | MIT |
| pytest-django | 4.14.0 | BSD License |
