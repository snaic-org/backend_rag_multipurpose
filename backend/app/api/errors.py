from typing import NoReturn

from fastapi import HTTPException, status


def raise_value_error(exc: ValueError) -> NoReturn:
    """Turn a service ValueError into 404 when it reports a missing resource, else 400."""
    message = str(exc)
    status_code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=status_code, detail=message) from exc
