"""
Pure, framework-light helpers extracted from the old monolithic
``Inventory/views.py``.

These functions used to be copy-pasted inline inside ``inventory`` and
``inventory_for_hod`` (equipment serialization, pagination metadata, page/per-page
parsing). They are pulled out here so the logic lives in exactly one place and can
be unit-tested WITHOUT a database — see ``Inventory/tests/test_helpers.py``.

Behaviour is preserved byte-for-byte from the original views. In particular the
two list views historically parsed ``per_page`` with *different* fallbacks, so
``normalize_per_page`` is parameterised rather than hard-coded.
"""

ALLOWED_PER_PAGE = [10, 25, 50, 100]


def parse_page_number(raw):
    """Parse the ``page`` query param.

    Mirrors the original inline logic: coerce to int, clamp to >= 1, and fall
    back to page 1 on any bad input. Identical in both list views.
    """
    try:
        page_number = int(raw)
        if page_number < 1:
            page_number = 1
    except (ValueError, TypeError):
        page_number = 1
    return page_number


def normalize_per_page(raw, invalid_fallback, error_fallback,
                       allowed=ALLOWED_PER_PAGE):
    """Parse the ``per_page`` query param.

    ``invalid_fallback`` is used when the value parses but is not one of the
    allowed page sizes; ``error_fallback`` is used when it does not parse at all.
    These differ between the two original list views, so callers pass them
    explicitly to preserve the exact legacy behaviour.
    """
    try:
        per_page = int(raw)
        if per_page not in allowed:
            per_page = invalid_fallback
    except (ValueError, TypeError):
        per_page = error_fallback
    return per_page


def serialize_equipment(e):
    """Serialize an Equipment instance to the dict shape the AJAX table expects.

    Only reads attributes, so it can be tested against a lightweight stand-in
    object without touching the ORM.
    """
    return {
        'id': e.id,
        'description': e.description.name,
        'description_id': e.description.id,
        'manufacturer': e.manufacturer.name if e.manufacturer else "",
        'manufacturer_id': e.manufacturer.id if e.manufacturer else "",
        'model': e.model if e.model else "",
        'serial': e.serial_number if e.serial_number else "",
        'department': e.department.name if e.department else "",
        'department_id': e.department.id if e.department else "",
        'workshop': e.department.workshop.name if e.department and e.department.workshop else "",
        'workshop_id': str(e.department.workshop.id) if e.department and e.department.workshop else "",
        'status': e.status,
        # getattr: the unit tests serialize a lightweight stand-in object.
        'asset_tag': getattr(e, 'asset_tag', '') or "",
    }


def _elided_page_range(paginator, page_obj):
    """Compute the page-range list for pagination controls (Django-version safe)."""
    if paginator.num_pages > 1:
        try:
            return list(paginator.get_elided_page_range(
                page_obj.number,
                on_each_side=2,
                on_ends=1
            ))
        except AttributeError:
            # Fallback for older Django versions
            return list(paginator.page_range)
    return [1]


def build_pagination_data(page_obj, paginator, per_page, total_count):
    """Build the ``pagination`` block of the AJAX JSON response.

    Reproduces the original inline block exactly, including the empty-result
    branch and the elided page-range fallback.
    """
    if total_count > 0:
        pagination_data = {
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'next_page_number': page_obj.next_page_number() if page_obj.has_next() else None,
            'previous_page_number': page_obj.previous_page_number() if page_obj.has_previous() else None,
            'num_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'start_index': page_obj.start_index(),
            'end_index': page_obj.end_index(),
            'total_count': total_count,
            'per_page': per_page,
            'has_other_pages': page_obj.has_other_pages(),
            'page_range': [1],
        }
    else:
        pagination_data = {
            'has_next': False,
            'has_previous': False,
            'next_page_number': None,
            'previous_page_number': None,
            'num_pages': 1,
            'current_page': 1,
            'start_index': 0,
            'end_index': 0,
            'total_count': 0,
            'per_page': per_page,
            'has_other_pages': False,
            'page_range': [1],
        }

    pagination_data['page_range'] = _elided_page_range(paginator, page_obj)
    return pagination_data
