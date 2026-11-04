import inspect

from sanic.log import logger
from sanic.response import HTTPResponse, text

from .base import (
    SessionConflictError,
    SessionDict,
    SessionSerializationError,
    SessionStoreError,
    get_request_container,
)
from .memcache import MemcacheSessionInterface
from .memory import InMemorySessionInterface

__all__ = (
    "MemcacheSessionInterface",
    "InMemorySessionInterface",
    "Session",
    "SessionDict",
    "SessionStoreError",
    "SessionConflictError",
    "SessionSerializationError",
)

__version__ = "0.10.0"

# The session is opened before the application's own request middleware (so
# they can use it) and saved before its response middleware run.
REQUEST_PRIORITY = 1000
RESPONSE_PRIORITY = -1000


def _ignore_matcher(ignore):
    if ignore is None or callable(ignore):
        return ignore
    prefixes = (ignore,) if isinstance(ignore, str) else tuple(ignore)
    return lambda request: request.path.startswith(prefixes)


def _replace_response(response, error):
    """Turn ``response`` into ``error`` in place, so the application's
    response middleware still run (and add their headers).  Streaming
    responses cannot be changed: ``error`` is returned instead."""
    if not isinstance(response, HTTPResponse) or not isinstance(
        error, HTTPResponse
    ):
        return error
    response.status = error.status
    # The handler's headers (cookies, content length and type, caching)
    # describe a body that is not sent
    response.headers.clear()
    response.headers.extend(error.headers)
    response.content_type = error.content_type
    response.body = error.body
    return None


async def _warn_in_memory_workers(app):
    workers = getattr(getattr(app, "state", None), "workers", 1) or 1
    if workers > 1:
        logger.warning(
            f"Sessions are kept in memory, separately in each of the {workers} "
            "workers: a session only works on the worker that created it.  Use "
            "MemcacheSessionInterface with more than one worker."
        )


class Session:
    def __init__(self, app=None, interface=None, ignore=None, on_store_error=None):
        """Adds sessions to ``app``.

        Args:
            app (sanic.Sanic): The application.
            interface: A session interface (default: InMemorySessionInterface,
                per process and lost on restart).
            ignore (str, iterable of str, or callable, optional):
                Paths that get no session: path prefixes (e.g.
                ``("/static/", "/favicon.ico")``), or a function of the request
                returning True to ignore it.  The store is not read or written
                for them; ``request.ctx.session`` is an empty session whose
                changes are not saved (and logged).
            on_store_error (callable, optional):
                ``on_store_error(request, exception)`` (may be async), called
                when the session cannot be saved; returns the response to send
                (e.g. JSON), or None for the default plain-text one.  The
                exception's ``status`` is 503 (store unavailable), 409
                (``SessionConflictError``) or 500 (``SessionSerializationError``).
        """
        self.interface = None
        self.ignore = ignore
        self.on_store_error = on_store_error
        if app:
            self.init_app(app, interface)

    def init_app(self, app, interface):
        self.interface = interface or InMemorySessionInterface()
        if not hasattr(app.ctx, "extensions"):
            app.ctx.extensions = {}

        app.ctx.extensions[
            self.interface.session_name
        ] = self  # session_name defaults to 'session'

        ignored = _ignore_matcher(self.ignore)

        async def add_session_to_request(request):
            """Before each request initialize a session
            using the client's request."""
            if ignored is not None and ignored(request):
                session = SessionDict()
                session.ignored = True
                get_request_container(request)[self.interface.session_name] = session
                return
            await self.interface.open(request)

        async def save_session(request, response):
            """After each request save the session, pass
            the response to set client cookies.  When the session cannot be
            saved, the response becomes an error (503 by default) so the
            client does not believe a change (e.g. a logon) was kept.
            """
            try:
                await self.interface.save(request, response)
            except SessionStoreError as e:
                logger.error(f"Session save failed: {type(e).__name__}: {e}")
                error = None
                if self.on_store_error is not None:
                    error = self.on_store_error(request, e)
                    if inspect.isawaitable(error):
                        error = await error
                if error is None:
                    error = text(e.message, status=e.status)
                return _replace_response(response, error)

        app.register_middleware(
            add_session_to_request, "request", priority=REQUEST_PRIORITY
        )
        app.register_middleware(save_session, "response", priority=RESPONSE_PRIORITY)
        if isinstance(self.interface, InMemorySessionInterface):
            app.register_listener(_warn_in_memory_workers, "main_process_start")
