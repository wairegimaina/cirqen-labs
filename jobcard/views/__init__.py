"""jobcard.views package (split from the former single views.py)."""

from .helpers import (
    get_user_context,
    get_or_create_user_signature,
    signature_to_image,
    generate_docx,
    get_filtered_jobcards,
    validate_stock_for_job_card,
    get_accessory_details,
    get_last_week_range,
)
from .creation import (
    get_pending_ppm_schedules,
    create_job_card,
)
from .nurse_approval import (
    handle_nurse_approval,
)
from .technician_approval import (
    handle_technician_job_card,
)
from .listing import (
    waiting_jobcards,
    approved_jobcards,
    declined_jobcards,
    hod_workshop_jobcards,
    hod_calibration_work_on_equipment,
)
from .downloads import (
    download_jobcard_docx,
    download_jobcard_pdf,
    bulk_download_approved_jobcards,
    bulk_download_waiting_jobcards,
    bulk_download_declined_jobcards,
)
from .ajax import (
    get_user_signature_data,
    load_accessories,
    check_stock_availability,
    load_equipment,
)

__all__ = [
    "get_user_context",
    "get_or_create_user_signature",
    "signature_to_image",
    "generate_docx",
    "get_filtered_jobcards",
    "validate_stock_for_job_card",
    "get_accessory_details",
    "get_last_week_range",
    "get_pending_ppm_schedules",
    "create_job_card",
    "handle_nurse_approval",
    "handle_technician_job_card",
    "waiting_jobcards",
    "approved_jobcards",
    "declined_jobcards",
    "hod_workshop_jobcards",
    "hod_calibration_work_on_equipment",
    "download_jobcard_docx",
    "download_jobcard_pdf",
    "bulk_download_approved_jobcards",
    "bulk_download_waiting_jobcards",
    "bulk_download_declined_jobcards",
    "get_user_signature_data",
    "load_accessories",
    "check_stock_availability",
    "load_equipment",
]
