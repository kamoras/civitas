"""Shared pagination arithmetic for the entity list/sub-resource services."""


def paginate_bounds(total: int, page: int, per_page: int) -> tuple[int, int]:
    """Clamp a requested page number against the actual page count.

    Returns (total_pages, clamped_page). Ceiling division via -(-total //
    per_page) avoids importing math.ceil for one call. Was copy-pasted at
    every paginated list endpoint across senator_service.py and
    representative_service.py.
    """
    total_pages = max(1, -(-total // per_page))
    return total_pages, max(1, min(page, total_pages))
