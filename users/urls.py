# urls.py - User Management URLs Configuration
from django.urls import path
from .views import (
    admin_reset_user_password,
    api_create_user,
    custom_login_view,
    force_setup,
    forgot_password_view,
    get_workshops_by_department,
    logout_view,
    reset_password_view,
    verify_reset_code_view,
    manage_users_view,
    api_get_users,
    api_update_user,
    api_delete_user,
    api_get_user_signature,
    api_regenerate_signature,
    api_get_departments,
    api_get_workshops,
    download_signature,
    update_theme,
    update_sidebar,
    update_settings,
    saved_filters_list_api,
    saved_filter_create_ajax,
    saved_filter_delete_ajax,
)

urlpatterns = [
    # ============================================================================
    # AUTHENTICATION URLS
    # ============================================================================

    path('', custom_login_view, name='custom_login'),
    path('logout/', logout_view, name='logout'),

    # Password Reset Flow
    path('forgot-password/', forgot_password_view, name='forgot_password'),
    path('verify-reset-code/', verify_reset_code_view, name='verify_reset_code'),
    path('reset-password/', reset_password_view, name='reset_password'),

    # ============================================================================
    # USER MANAGEMENT URLS (HOD and NIC Access)
    # ============================================================================

    path('api/create-user/', api_create_user, name='api_create_user'),

    path('manage/', manage_users_view, name='manage_users'),

    # Admin Password Reset (HOD Only) - now UUID
    path('admin-reset-password/<uuid:user_id>/', admin_reset_user_password, name='admin_reset_user_password'),

    # ============================================================================
    # API ENDPOINTS (JSON Responses)
    # ============================================================================

    # User CRUD API (now UUID-based)
    path('api/users/', api_get_users, name='api_get_users'),
    path('api/users/<uuid:user_id>/', api_update_user, name='api_update_user'),
    path('api/users/<uuid:user_id>/delete/', api_delete_user, name='api_delete_user'),

    # Signature Management API (UUID-based)
    path('api/users/<uuid:user_id>/signature/', api_get_user_signature, name='api_get_user_signature'),
    path('api/users/<uuid:user_id>/signature/regenerate/', api_regenerate_signature, name='api_regenerate_signature'),

    # Data Helper APIs
    path('api/departments/', api_get_departments, name='api_get_departments'),
    path('api/workshops/', api_get_workshops, name='api_get_workshops'),
    path('get-workshops-by-department/', get_workshops_by_department, name='get_workshops_by_department'),

    # ============================================================================
    # FILE DOWNLOAD URLS
    # ============================================================================

    path('download-signature/<uuid:user_id>/', download_signature, name='download_signature'),

    path('force_setup/', force_setup, name='force_setup'),

    # ============================================================================
    # THEME & SETTINGS URLS
    # ============================================================================

    path('update-theme/', update_theme, name='update_theme'),
    path('update-sidebar/', update_sidebar, name='update_sidebar'),
    path('update-settings/', update_settings, name='update_settings'),

    # ============================================================================
    # SAVED FILTERS (generic — any GET-filtered list page can use these)
    # ============================================================================
    path('api/saved-filters/', saved_filters_list_api, name='saved_filters_list_api'),
    path('api/saved-filters/create/', saved_filter_create_ajax, name='saved_filter_create_ajax'),
    path('api/saved-filters/<uuid:pk>/delete/', saved_filter_delete_ajax, name='saved_filter_delete_ajax'),

]
