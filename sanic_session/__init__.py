from sanic.log import logger
from sanic.response import text

from .base import SessionDict, SessionStoreError
from .memcache import MemcacheSessionInterface
from .memory import InMemorySessionInterface

__all__ = (
    "MemcacheSessionInterface",
    "InMemorySessionInterface",
    "Session",
    "SessionDict",
    "SessionStoreError",
)

__version__ = "0.9.0"

# The session is opened before the application's own request middleware (so
# they can use it) and saved before its response middleware run.
REQUEST_PRIORITY = 1000
RESPONSE_PRIORITY = -1000


class Session:
    def __init__(self, app=None, interface=None):
        self.interface = None
        if app:
            self.init_app(app, interface)

    def init_app(self, app, interface):
        self.interface = interface or InMemorySessionInterface()
        if not hasattr(app.ctx, "extensions"):
            app.ctx.extensions = {}

        app.ctx.extensions[
            self.interface.session_name
        ] = self  # session_name defaults to 'session'

        async def add_session_to_request(request):
            """Before each request initialize a session
            using the client's request."""
            await self.interface.open(request)

        async def save_session(request, response):
            """After each request save the session, pass
            the response to set client cookies.  When the store cannot be
            written, the response is replaced with a 503 so the client does
            not believe a change (e.g. a logon) was kept.
            """
            try:
                await self.interface.save(request, response)
            except SessionStoreError as e:
                logger.error(f"Session store write failed: {e}")
                return text(
                    "The session store is unavailable, please try again later.",
                    status=503,
                )

        app.register_middleware(
            add_session_to_request, "request", priority=REQUEST_PRIORITY
        )
        app.register_middleware(save_session, "response", priority=RESPONSE_PRIORITY)
