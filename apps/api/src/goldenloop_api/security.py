import ipaddress
from contextlib import contextmanager
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

LOCAL_IDENTITY = "local-synthetic-reviewer"
LOCAL_ROLES = frozenset({"read", "review", "execute"})


def local_host(value: str) -> bool:
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def require(role):
    async def authorize(request: Request):
        if not request.app.state.settings.local_demo or role not in LOCAL_ROLES:
            raise HTTPException(
                403, "Local synthetic role required; shared authentication is not implemented"
            )
        return LOCAL_IDENTITY

    return authorize


class LocalOnlyMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        error, status = None, 403
        if not self.settings.local_demo:
            error = "API disabled. Explicit GOLDENLOOP_LOCAL_DEMO=true required; no shared authentication is implemented"
        try:
            host = urlsplit("http://" + headers.get("host", "")).hostname or ""
            peer = (scope.get("client") or ("",))[0]
            origin = headers.get("origin")
            if not local_host(host) or not local_host(peer):
                error = "Only loopback clients and hosts are permitted"
            if origin:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not local_host(parsed.hostname or "")
                    or parsed.username
                ):
                    error = "Only local browser origins are permitted"
            if headers.get("sec-fetch-site") == "cross-site":
                error = "Cross-site requests are not permitted"
            if int(headers.get("content-length", "0")) > self.settings.max_request_bytes:
                error, status = "Request body limit exceeded", 413
        except ValueError:
            error, status = "Invalid request headers", 400
        if error:
            return await JSONResponse({"detail": error}, status_code=status)(scope, receive, send)
        # Bound chunked bodies as well as Content-Length before multipart/JSON parsing.
        body, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.settings.max_request_bytes:
                return await JSONResponse({"detail": "Request body limit exceeded"}, status_code=413)(
                    scope, receive, send
                )
            body.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            if body:
                return body.pop(0)
            return await receive()

        await self.app(scope, replay, send)


@contextmanager
def process_lock(path):
    # OS advisory lock is released on crash. A second Uvicorn worker must not reconcile active jobs.
    handle = path.open("a+b")
    acquired = False
    try:
        import os

        if os.name == "nt":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Another API process owns this database; use --workers 1") from None
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("Another API process owns this database; use --workers 1") from None
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
