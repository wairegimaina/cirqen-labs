"""parts_tools views package.

This package replaces the old single ``parts_tools/views.py``. The views are
now grouped by concern across submodules:

* :mod:`.dashboard`    – the main accessories & tools management page
* :mod:`.apis`         – JSON API endpoints consumed by accessories.js
* :mod:`.tools`        – tool CRUD, listing, and tool name/manufacturer helpers
* :mod:`.accessories`  – accessory direct management (HOD) + name/manufacturer helpers
* :mod:`.requests`     – accessory request workflow (submit/approve/accept/history)
* :mod:`.exports`      – Excel / PDF exports

Everything is re-exported here so existing imports (``from . import views`` in
``urls.py``) keep working unchanged.
"""

from .dashboard import accessories_dashboard
from .apis import (
    api_accessories,
    api_accessory_detail,
    api_tools,
    api_tool_detail,
    api_accessory_requests,
)
from .tools import (
    add_tool,
    edit_tool,
    get_tool,
    delete_tool,
    tool_list,
    ajax_add_tool_name,
    ajax_add_tool_manufacturer,
    delete_tool_name,
    delete_tool_manufacturer,
)
from .accessories import (
    edit_accessory,
    get_accessory,
    delete_accessory,
    accessory_list,
    ajax_add_accessory_name,
    ajax_add_manufacturer,
    delete_accessory_name,
    delete_accessory_manufacturer,
)
from .requests import (
    request_accessory,
    approve_accessory_request,
    accept_accessory_request,
)
from .exports import (
    export_excel_tools,
    export_excel_accessories,
    export_pdf_tools,
    export_pdf_accessories,
)

__all__ = [
    # dashboard
    "accessories_dashboard",
    # apis
    "api_accessories",
    "api_accessory_detail",
    "api_tools",
    "api_tool_detail",
    "api_accessory_requests",
    # tools
    "add_tool",
    "edit_tool",
    "get_tool",
    "delete_tool",
    "tool_list",
    "ajax_add_tool_name",
    "ajax_add_tool_manufacturer",
    "delete_tool_name",
    "delete_tool_manufacturer",
    # accessories
    "edit_accessory",
    "get_accessory",
    "delete_accessory",
    "accessory_list",
    "ajax_add_accessory_name",
    "ajax_add_manufacturer",
    "delete_accessory_name",
    "delete_accessory_manufacturer",
    # requests
    "request_accessory",
    "approve_accessory_request",
    "accept_accessory_request",
    # exports
    "export_excel_tools",
    "export_excel_accessories",
    "export_pdf_tools",
    "export_pdf_accessories",
]
