import warnings

from sanic_session.base import BaseSessionInterface

try:
    import aiomcache
except Exception:  # pragma: no cover
    aiomcache = None

# memcached treats an expiry over 30 days as a timestamp, so it is the limit
MEMCACHE_MAX_TTL = 2592000


class MemcacheSessionInterface(BaseSessionInterface):
    def __init__(
        self,
        memcache_connection,
        domain: str = None,
        expiry: int = 2592000,
        httponly: bool = True,
        cookie_name: str = "session",
        prefix: str = "session:",
        sessioncookie: bool = False,
        samesite: str = "Lax",
        session_name: str = "session",
        secure: bool = True,
    ):
        """Initializes the interface for storing client sessions in memcache.
        Requires a client object established with `aiomcache`.

        Args:
            memcache_connection (aiomcache.Client):
                The memcache client used for interfacing with memcache.
            domain (str, optional):
                Optional domain which will be attached to the cookie.
            expiry (int, optional):
                Seconds until the session should expire (0 or None: no
                expiry, the cookie lasts until the browser closes).
                memcached keeps an entry at most 30 days: longer values keep
                the full cookie lifetime but expire unused sessions in the
                store after 30 days.
            httponly (bool, optional):
                Adds the `httponly` flag to the session cookie.
            cookie_name (str, optional):
                Name used for the client cookie.
            prefix (str, optional):
                Memcache keys will take the format of `prefix+session_id`;
                specify the prefix here.
            sessioncookie (bool, optional):
                Specifies if the sent cookie should be a 'session cookie', i.e
                no Expires or Max-age headers are included. Expiry is still
                fully tracked on the server side. Default setting is False.
            samesite (str, optional):
                The cookie's SameSite setting: 'Lax' (default), 'Strict',
                'None', or None to leave it out.
            session_name (str, optional):
                Name of the session that will be accessible through the
                request.
                e.g. If ``session_name`` is ``alt_session``, it should be
                accessed like that: ``request.ctx.alt_session``
                e.g. And if ``session_name`` is left to default, it should be
                accessed like that: ``request.ctx.session``
                Default: 'session'
            secure (bool, optional):
                Adds the `Secure` flag to the session cookie (default True).
        """
        if aiomcache is None:
            raise RuntimeError(
                "Please install aiomcache: pip install "
                "sanic_session[aiomcache]"
            )

        self.memcache_connection = memcache_connection

        if expiry and expiry > MEMCACHE_MAX_TTL:
            warnings.warn(
                "memcached keeps entries at most 30 days: sessions unused "
                "for longer expire in the store, the cookie keeps the full "
                "expiry",
                stacklevel=2,
            )

        super().__init__(
            expiry=expiry,
            prefix=prefix,
            cookie_name=cookie_name,
            domain=domain,
            httponly=httponly,
            sessioncookie=sessioncookie,
            samesite=samesite,
            session_name=session_name,
            secure=secure,
        )

    def _store_ttl(self):
        return min(self.expiry, MEMCACHE_MAX_TTL) if self.expiry else 0

    async def _get_value(self, prefix, sid):
        key = (self.prefix + sid).encode()
        value = await self.memcache_connection.get(key)
        return value.decode() if value else None

    async def _delete_key(self, key):
        return await self.memcache_connection.delete(key.encode())

    async def _set_value(self, key, data):
        return await self.memcache_connection.set(
            key.encode(), data.encode(), exptime=self._store_ttl()
        )
