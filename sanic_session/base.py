import abc
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from sanic.log import logger

from sanic_session.utils import CallbackDict

# Session ids are uuid4().hex: anything else in the cookie is ignored
SID_PATTERN = re.compile(r"[0-9a-f]{32}")

# Stored with the session data (and hidden from the application): when the
# session was last written, so an unchanged session can be refreshed
SAVED_AT_KEY = "_sanic_session_saved_at"


def get_request_container(request):
    return request.ctx.__dict__ if hasattr(request, "ctx") else request


def _dumps(data):
    return json.dumps(data, separators=(",", ":"))


class SessionStoreError(Exception):
    """The session could not be saved.  The middleware replaces the response
    with ``status`` (503: the store could not be written or read)."""

    status = 503
    message = "The session store is unavailable, please try again later."


class SessionConflictError(SessionStoreError):
    """Another request changed the session first (memcached ``cas=True``)."""

    status = 409
    message = "The session was changed by another request, please try again."


class SessionSerializationError(SessionStoreError):
    """A session value is not a JSON type (str, int, float, bool, None, list
    or dict), so the session could not be saved."""

    status = 500
    message = "Internal Server Error"


class SessionDict(CallbackDict):
    def __init__(self, initial=None, sid=None, stored=False):
        def on_update(self):
            self.modified = True

        super().__init__(initial, on_update)

        self.sid = sid
        self.modified = False
        # True when this session id has data in the store
        self.stored = stored
        # False when the store could not be read: the data is unknown, so the
        # session is not saved over the stored one
        self.loaded = True
        # True on paths the Session ignores: nothing is read or saved
        self.ignored = False
        # The data as loaded, to notice changes inside nested values
        try:
            self._loaded_json = _dumps(dict(self))
        except (TypeError, ValueError):
            self._loaded_json = None
        self._saved_at = 0
        self._token = None
        # A stored id replaced by regenerate(), removed once the new id is saved
        self._old_sid = None


class BaseSessionInterface(metaclass=abc.ABCMeta):
    """Loads the session from the store before each request and saves it
    after, when it changed (including changes inside nested lists and dicts),
    or when it was last written more than ``refresh_interval`` seconds ago.

    Concurrent requests for the same session each save the whole session
    dict, so the last one to finish wins: changes made to different keys by
    overlapping requests can be lost (see ``cas`` for memcached).

    Session values must be JSON types.
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
        refresh_interval=None,
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
        # None: a tenth of the expiry; 0: never (expiry counts from the
        # last change)
        if refresh_interval is None:
            refresh_interval = expiry / 10 if expiry else 0
        self.refresh_interval = refresh_interval

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
    async def _get_value(self, key: str):
        """Get the value stored for key (``prefix + sid``), or None."""
        raise NotImplementedError

    @abc.abstractmethod
    async def _delete_key(self, key: str):
        """Delete key from datastore"""
        raise NotImplementedError

    @abc.abstractmethod
    async def _set_value(self, key: str, data: str):
        """Set value for datastore"""
        raise NotImplementedError

    async def _load(self, key):
        """Return the stored value and a token for ``_store`` (None here)."""
        return await self._get_value(key), None

    async def _store(self, key, data, token):
        """Write the value; returns False when it was not written because the
        stored value changed since ``_load`` (only backends with tokens)."""
        await self._set_value(key, data)
        return True

    async def open(self, request) -> SessionDict:
        """
        Opens a session onto the request. Restores the client's session
        from the datastore if one exists. The session data will be available
        on `request.ctx.session`.

        A cookie that is not a session id, or names a session that is not in
        the store, gets a new session id: client-chosen ids are never used.
        Stored data that is not valid gives a new, empty session (and a log
        message) instead of an error.  When the store cannot be read, the
        request gets an empty session that keeps the client's id and is not
        saved (so the stored session is not replaced), unless it is
        regenerated.

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
                val, token = await self._load(self.prefix + sid)
            except Exception as e:
                logger.warning(
                    "Session store read failed, the session is unavailable for "
                    f"this request: {type(e).__name__}: {e}"
                )
                session_dict = SessionDict(sid=sid)
                session_dict.loaded = False
                val = None
            if val is not None:
                try:
                    data = json.loads(val)
                except ValueError:
                    logger.warning("Stored session is not valid JSON, ignoring it")
                    data = None
                if isinstance(data, dict):
                    saved_at = data.pop(SAVED_AT_KEY, 0)
                    session_dict = SessionDict(data, sid=sid, stored=True)
                    if isinstance(saved_at, (int, float)):
                        session_dict._saved_at = saved_at
                    session_dict._token = token

        if session_dict is None:
            session_dict = SessionDict(sid=self._new_sid())

        # attach the session data to the request, return it for convenience
        req = get_request_container(request)
        req[self.session_name] = session_dict

        return session_dict

    async def regenerate(self, request) -> SessionDict:
        """Give the request's session a new id, keeping its data.

        Call this when the user's privileges change (logon, switching user)
        so an id known to anyone else stops working.  The new id is saved and
        sent at the end of the request, and the old id is then removed from
        the store (if the save fails, the old session is left as it was).
        """
        session = get_request_container(request)[self.session_name]
        if (session.stored or not session.loaded) and session._old_sid is None:
            session._old_sid = session.sid
        session.sid = self._new_sid()
        session.stored = False
        session.loaded = True
        session._token = None
        session.modified = True
        return session

    async def _delete_old_sid(self, session):
        if session._old_sid is None:
            return
        try:
            await self._delete_key(self.prefix + session._old_sid)
        except Exception as e:
            logger.warning(
                "Could not delete the old session id from the store: "
                f"{type(e).__name__}: {e}"
            )
        session._old_sid = None

    @staticmethod
    def _serialize(session):
        try:
            return _dumps(dict(session))
        except (TypeError, ValueError) as e:
            bad = []
            for k, v in session.items():
                try:
                    _dumps({k: v})
                except (TypeError, ValueError):
                    bad.append(repr(k))
            keys = ", ".join(bad) or "?"
            raise SessionSerializationError(
                f"session value is not a JSON type (key {keys}): {e}"
            ) from e

    async def save(self, request, response) -> None:
        """Saves the session to the datastore, when it changed (also inside
        nested values) or is due a refresh.

        An emptied session is deleted from the store and its cookie removed.

        Args:
            request (sanic.request.Request):
                The sanic request which has an attached session.
            response (sanic.response.Response):
                The Sanic response. Cookies with the appropriate expiration
                will be added onto this response.

        Raises:
            SessionStoreError: the store could not be written, or (for a
                changed session) read.
            SessionConflictError: memcached ``cas=True`` and another request
                saved the session first.
            SessionSerializationError: a value is not a JSON type.

        Returns:
            None
        """
        req = get_request_container(request)
        session = req.get(self.session_name)
        if session is None:
            return
        if session.ignored:
            if session.modified:
                logger.warning(
                    f"The session was changed on an ignored path ({request.path}) "
                    "and was not saved"
                )
            return
        # A new session nobody touched
        if not session.modified and not session.stored:
            return

        val = self._serialize(session)
        now = time.time()
        changed = session.modified or val != session._loaded_json
        refresh = (
            session.stored
            and self.refresh_interval
            and now - session._saved_at >= self.refresh_interval
        )
        if not changed and not refresh:
            return
        if not session.loaded:
            raise SessionStoreError(
                "the session could not be read from the store, so the change "
                "was not saved"
            )

        key = self.prefix + session.sid
        if not session:
            if session.stored:
                try:
                    await self._delete_key(key)
                except Exception as e:
                    raise SessionStoreError(f"{type(e).__name__}: {e}") from e
                session.stored = False
            await self._delete_old_sid(session)
            self._delete_cookie(request, response)
            session.modified = False
            session._loaded_json = val
            return

        stored_val = _dumps({**session, SAVED_AT_KEY: round(now, 3)})
        token = session._token if session.stored else None
        try:
            written = await self._store(key, stored_val, token)
        except Exception as e:
            raise SessionStoreError(f"{type(e).__name__}: {e}") from e
        if written is False:
            raise SessionConflictError("the session was changed by another request")
        session.stored = True
        session.modified = False
        session._loaded_json = val
        session._saved_at = round(now, 3)
        await self._delete_old_sid(session)
        self._set_cookie_props(request, response)
