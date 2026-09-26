"""machineReports views package (split from the former single views.py)."""

from .helpers import (
    calculate_manufacturer_performance,
    get_user_workshop_context,
    get_rating,
    generate_recommendations,
)
from .dashboard import (
    equipment_dashboard,
)
from .repairs import (
    equipment_repair_details,
)
from .exports import (
    export_equipment_history,
    export_equipment_category_detailed_pdf,
    ManufacturerPerformanceDocTemplate,
    export_manufacturer_performance_pdf,
    EquipmentListDocTemplate,
)
from .risk import (
    failure_risk,
)
from .apis import (
    equipment_count_preview_api,
)

__all__ = [
    "calculate_manufacturer_performance",
    "get_user_workshop_context",
    "get_rating",
    "generate_recommendations",
    "equipment_dashboard",
    "equipment_repair_details",
    "export_equipment_history",
    "export_equipment_category_detailed_pdf",
    "ManufacturerPerformanceDocTemplate",
    "export_manufacturer_performance_pdf",
    "EquipmentListDocTemplate",
    "equipment_count_preview_api",
    "failure_risk",
]
