from contextvars import ContextVar
import re
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# Request ID contextvar for thread/async-safe request tracking
request_id_ctx: ContextVar[str] = ContextVar("request_id_ctx", default="")

REQUEST_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def get_request_id() -> str:
    """Returns the current request ID from contextvar, or empty string if unassociated."""
    return request_id_ctx.get("")


def validate_request_id(req_id: str | None) -> str | None:
    """Validates incoming X-Request-ID against length and character boundaries."""
    if not req_id or not isinstance(req_id, str):
        return None
    req_id = req_id.strip()
    if REQUEST_ID_REGEX.match(req_id):
        return req_id
    return None


class RequestCorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware for X-Request-ID correlation, format validation, and safe header propagation."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_id = request.headers.get("X-Request-ID")
        valid_id = validate_request_id(incoming_id)

        if not valid_id:
            valid_id = str(uuid.uuid4())

        token = request_id_ctx.set(valid_id)
        request.state.request_id = valid_id

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = valid_id
            return response
        finally:
            request_id_ctx.reset(token)
