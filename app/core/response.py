def success(data=None, message="success"):
    """Return a standard success payload for non-paginated responses."""

    return {"code": 0, "message": message, "data": data or {}}


def page_success(items, total, page_no, page_size, message="success"):
    """Return a standard success payload for paginated responses."""

    return {
        "code": 0,
        "message": message,
        "data": {
            "page_no": page_no,
            "page_size": page_size,
            "total": total,
            "items": items,
        },
    }
