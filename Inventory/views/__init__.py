"""Inventory views package.

This package replaces the old single ``Inventory/views.py`` (~3,400 lines). The
views are now grouped by concern across submodules:

* :mod:`.apis`           – JSON API endpoints (analytics, summary, lookups)
* :mod:`.inventory_list` – equipment browsing / summary page views
* :mod:`.equipment`      – equipment create/edit/delete/transfer/reactivate
* :mod:`.departments`    – department CRUD + dependency transfer
* :mod:`.exports`        – Excel / PDF exports and the reports dashboard
* :mod:`.helpers`        – pure, unit-tested helpers shared by the list views

Everything is re-exported here so existing imports (``from Inventory import
views`` / ``from . import views`` in ``urls.py``) keep working unchanged.
"""

from .apis import (
    inventory_summary_api,
    equipment_analytics_api,
    get_available_departments_for_transfer,
    check_equipment_availability,
    check_pdf_generation_status,
    get_models_by_description,
)
from .inventory_list import (
    inventory,
    inventory_for_hod,
    dashboard_view,
    inventory_by_department,
    inventory_summary,
)
from .equipment import (
    check_equipment_ppm_locations,
    create_equipment_description,
    create_manufacturer,
    add_inventory,
    get_equipment_dependency_info,
    edit_inventory,
    delete_equipment,
)
from .transfers import (
    transfer_equipment,
    reactivate_equipment,
)
from .departments import (
    discover_department_dependencies,
    get_department_dependencies_count,
    transfer_department_dependencies_auto,
    create_department,
    edit_department,
    transfer_department,
    get_department_dependency_count,
    transfer_department_dependencies,
    delete_department,
)
from .exports import (
    export_equipment_to_excel,
    export_inventory_summary_excel,
    export_equipment_to_pdf,
    export_inventory_summary_to_pdf,
    equipment_reports_dashboard,
    bulk_export_departments_pdf,
)

__all__ = [
    # apis
    "inventory_summary_api",
    "equipment_analytics_api",
    "get_available_departments_for_transfer",
    "check_equipment_availability",
    "check_pdf_generation_status",
    "get_models_by_description",
    # inventory_list
    "inventory",
    "inventory_for_hod",
    "dashboard_view",
    "inventory_by_department",
    "inventory_summary",
    # equipment
    "check_equipment_ppm_locations",
    "create_equipment_description",
    "create_manufacturer",
    "add_inventory",
    "transfer_equipment",
    "reactivate_equipment",
    "get_equipment_dependency_info",
    "edit_inventory",
    "delete_equipment",
    # departments
    "discover_department_dependencies",
    "get_department_dependencies_count",
    "transfer_department_dependencies_auto",
    "create_department",
    "edit_department",
    "transfer_department",
    "get_department_dependency_count",
    "transfer_department_dependencies",
    "delete_department",
    # exports
    "export_equipment_to_excel",
    "export_inventory_summary_excel",
    "export_equipment_to_pdf",
    "export_inventory_summary_to_pdf",
    "equipment_reports_dashboard",
    "bulk_export_departments_pdf",
]
