def paginate(items, page, size):
    """Return the items on a 1-based page."""
    if page < 1 or size < 1:
        raise ValueError("page and size must be >= 1")
    start = (page - 1) * size
    return items[start:start + size]
