import abc
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sanic.log import logger

from sanic_session.utils import CallbackDict

# Session ids are uuid4().hex: anything else in the cookie is ignored
SID_PATTERN = re.compile(r"[0-9a-f]{32}")


def get_request_container(request):
    return request.ctx.__dict__ if hasattr(request, "ctx") else request


class SessionStoreError(Exception):
    """The session store could not be written (or deleted from)."""


class SessionDict(CallbackDict):
    def __init__(self, initial=None, sid=None, stored=False):
        def on_update(self):
            self.modified = True

        super().__init__(initial, on_update)

        self.sid = sid
        self.modified = False
        # True when this session id has data in the store
        self.stored = stored


class BaseSessionInterface(metaclass=abc.ABCMeta):
    """Loads the session from the store before each request and saves it
    after, when it changed.

    Concurrent requests for the same session each save the whole session
    dict, so the last one to finish wins: changes made to different keys by
    overlapping requests can be lost.
    """

    def __init__(
        self,
        expiry,
        prefix,
        cookie_name,
        domain,
        httponly,
        sessioncookie,
        samesite,
        session_name,
        secure,
    ):
        self.expiry = expiry
        self.prefix = prefix
        self.cookie_name = cookie_name
        self.domain = domain
        self.httponly = httponly
        self.sessioncookie = sessioncookie
        self.samesite = samesite
        self.session_name = session_name
        self.secure = secure

    @staticmethod
    def _new_sid():
        return uuid.uuid4().hex

    def _store_ttl(self):
        """Seconds the store keeps a session (0: no expiry)."""
        return self.expiry or 0

    def _delete_cookie(self, request, response):
        # An expired cookie with the same path, domain and flags it was set
        # with, so the browser replaces (and drops) the original.
        response.add_cookie(
            self.cookie_name,
            "",
            path="/",
            domain=self.domain,
            secure=self.secure,
            httponly=self.httponly,
            samesite=self.samesite,
            max_age=0,
            expires=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def _calculate_expires(expiry):
        return datetime.now(timezone.utc) + timedelta(seconds=expiry)

    def _set_cookie_props(self, request, response):
        req = get_request_container(request)
        options = {
            "path": "/",
            "domain": self.domain,
            "secure": self.secure,
            "httponly": self.httponly,
            "samesite": self.samesite,
        }
        # expiry 0/None, or sessioncookie, sends a browser-session cookie
        if self.expiry and not self.sessioncookie:
            options["max_age"] = self.expiry
            options["expires"] = self._calculate_expires(self.expiry)
        response.add_cookie(self.cookie_name, req[self.session_name].sid, **options)

    @abc.abstractmethod
    async def _get_value(self, prefix: str, sid: str):
        """
        Get value from datastore. Specific implementation for each datastore.

        Args:
            prefix:
                A prefix for the key, useful to namespace keys.
            sid:
                a uuid in hex string
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def _delete_key(self, key: str):
        """Delete key from datastore"""
        raise NotImplementedError

    @abc.abstractmethod
    async def _set_value(self, key: str, data: str):
        """Set value for datastore"""
        raise NotImplementedError

    async def open(self, request) -> SessionDict:
        """
        Opens a session onto the request. Restores the client's session
        from the datastore if one exists. The session data will be available
        on `request.ctx.session`.

        A cookie that is not a session id, or names a session that is not in
        the store, gets a new session id: client-chosen ids are never used.
        A store that cannot be read, or stored data that is not valid, also
        gives a new, empty session (and a log message) instead of an error.

        Args:
            request (sanic.request.Request):
                The request, which a session will be opened onto.

        Returns:
            SessionDict:
                the client's session data,
                attached as well to `request.ctx.session`.
        """
        sid = request.cookies.get(self.cookie_name)
        session_dict = None

        if sid and SID_PATTERN.fullmatch(sid):
            try:
                val = await self._get_value(self.prefix, sid)
            except Exception as e:
                logger.warning(
                    "Session store read failed, using an empty session: "
                    f"{type(e).__name__}: {e}"
                )
                val = None
            if val is not None:
                try:
                    data = json.loads(val)
                except ValueError:
                    logger.warning("Stored session is not valid JSON, ignoring it")
                    data = None
                if isinstance(data, dict):
                    session_dict = SessionDict(data, sid=sid, stored=True)

        if session_dict is None:
            session_dict = SessionDict(sid=self._new_sid())

        # attach the session data to the request, return it for convenience
        req = get_request_container(request)
        req[self.session_name] = session_dict

        return session_dict

    async def regenerate(self, request) -> SessionDict:
        """Give the request's session a new id, keeping its data.

        Call this when the user's privileges change (logon, switching user)
        so an id known to anyone else stops working.  The old id is removed
        from the store, and the new id is saved and sent at the end of the
        request.
        """
        session = get_request_container(request)[self.session_name]
        if session.stored:
            try:
                await self._delete_key(self.prefix + session.sid)
            except Exception as e:
                logger.warning(
                    "Could not delete the old session id from the store: "
                    f"{type(e).__name__}: {e}"
                )
        session.sid = self._new_sid()
        session.stored = False
        session.modified = True
        return session

    async def save(self, request, response) -> None:
        """Saves the session to the datastore, when it was modified.

        An emptied session is deleted from the store and its cookie removed.

        Args:
            request (sanic.request.Request):
                The sanic request which has an attached session.
            response (sanic.response.Response):
                The Sanic response. Cookies with the appropriate expiration
                will be added onto this response.

        Raises:
            SessionStoreError: the store could not be written.

        Returns:
            None
        """
        req = get_request_container(request)
        session = req.get(self.session_name)
        if session is None or not session.modified:
            return

        key = self.prefix + session.sid
        if not session:
            if session.stored:
                try:
                    await self._delete_key(key)
                except Exception as e:
                    raise SessionStoreError(f"{type(e).__name__}: {e}") from e
                session.stored = False
            self._delete_cookie(request, response)
            session.modified = False
            return

        val = json.dumps(dict(session), separators=(",", ":"))
        try:
            await self._set_value(key, val)
        except Exception as e:
            raise SessionStoreError(f"{type(e).__name__}: {e}") from e
        session.stored = True
        session.modified = False
        self._set_cookie_props(request, response)
