"""users views package (split from the former single views.py)."""

from .auth import (
    custom_login_view,
    force_setup,
    _redirect_based_on_role,
    logout_view,
    forgot_password_view,
    verify_reset_code_view,
    reset_password_view,
)
from .user_management import (
    api_create_user,
    create_user_view,
    admin_reset_user_password,
    user_management_page,
    manage_users_view,
)
from .apis import (
    api_update_user,
    api_check_setup_status,
    get_workshops_by_department,
    api_get_users,
    api_delete_user,
    api_toggle_user_status,
    api_get_departments,
    api_get_workshops,
)
from .signature import (
    api_force_signature_reset,
    download_signature,
    api_get_user_signature,
    api_regenerate_signature,
)
from .settings import (
    update_theme,
    update_sidebar,
    update_settings,
)
from .saved_filters import (
    saved_filters_list_api,
    saved_filter_create_ajax,
    saved_filter_delete_ajax,
)

__all__ = [
    "custom_login_view",
    "force_setup",
    "_redirect_based_on_role",
    "logout_view",
    "forgot_password_view",
    "verify_reset_code_view",
    "reset_password_view",
    "api_create_user",
    "create_user_view",
    "admin_reset_user_password",
    "user_management_page",
    "manage_users_view",
    "api_update_user",
    "api_check_setup_status",
    "get_workshops_by_department",
    "api_get_users",
    "api_delete_user",
    "api_toggle_user_status",
    "api_get_departments",
    "api_get_workshops",
    "api_force_signature_reset",
    "download_signature",
    "api_get_user_signature",
    "api_regenerate_signature",
    "update_theme",
    "update_sidebar",
    "update_settings",
    "saved_filters_list_api",
    "saved_filter_create_ajax",
    "saved_filter_delete_ajax",
]
