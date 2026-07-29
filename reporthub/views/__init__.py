"""reporthub views package.

This package replaces the old single ``reporthub/views.py``. The views are now
grouped by concern across submodules:

* :mod:`.report`   – main report dashboard + weekly-choices JSON endpoint
* :mod:`.exports`  – CSV export and PDF download
* :mod:`.hod`      – HOD per-workshop report view and landing overview
* :mod:`.filters`  – the ``ReportFilterForm`` shared by the views
* :mod:`.helpers`  – pure data-shaping helpers (weekly choices, annual data)

Everything the URLconf needs is re-exported here so existing imports
(``from . import views``) keep working unchanged. The form and helpers are
re-exported too, preserving the previous public surface of this package.
"""

from .report import report_hub, get_weekly_choices_json
from .exports import export_report, download_pdf_report
from .hod import hod_reports_view, hod_reports_landing
from .filters import ReportFilterForm
from .helpers import get_weekly_choices, get_annual_data, get_annual_periods

__all__ = [
    # report
    "report_hub",
    "get_weekly_choices_json",
    # exports
    "export_report",
    "download_pdf_report",
    # hod
    "hod_reports_view",
    "hod_reports_landing",
    # filters / helpers (internal, re-exported for back-compat)
    "ReportFilterForm",
    "get_weekly_choices",
    "get_annual_data",
    "get_annual_periods",
]
