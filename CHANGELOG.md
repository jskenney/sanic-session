# Changelog

All notable changes to this fork of sanic-session.  Versions are git tags;
install one with
`pip install "sanic-session[aiomcache] @ git+https://github.com/jskenney/sanic-session@vX.Y.Z"`.

## 0.10.1

- **Fixed:** the sliding-expiry refresh (0.10.0) rewrote a session a
  request had only read, from the data that request loaded.  When another
  request saved a change in the meantime, the refresh overwrote it, and with
  `cas=True` the read-only request got a 409.  A refresh now writes only if
  the stored session is unchanged since it was read (memcached `gets`/`cas`,
  a compare in the in-memory store), and is skipped silently otherwise.
- **Fixed:** an `on_store_error` hook that raises, or returns something that
  is not a response, no longer leaves the original response in place: the
  default error response is sent and the problem logged.
- memcached sessions are now always read with `gets` (to get the cas token
  refreshes need); this costs nothing extra.
- Backend API: new `_refresh(key, data, raw, token)`, a compare-and-set
  write.  The default suits stores whose `_get_value`/`_set_value` do not
  wait; backends with real I/O should override it.

## 0.10.0

- **Fixed:** changes inside nested values (`session["cart"].append(x)`)
  were not saved: a session is now saved when its JSON differs from what was
  loaded.
- A session that cannot be saved turns the response into the error in place,
  so the application's response middleware (security headers) still run.
  New `Session(..., on_store_error=fn)` supplies the response.  Status codes:
  503 store unavailable, 409 `SessionConflictError`, 500
  `SessionSerializationError` (a value that is not a JSON type).
- Sliding expiry: `refresh_interval` (default a tenth of `expiry`, 0 off).
  The last write time is stored under the reserved key
  `_sanic_session_saved_at`, hidden from the application.
- `Session(..., ignore=...)`: path prefixes or a predicate; those paths get
  an empty session and no store access.
- A store read failure keeps the client's session id and never saves over
  the stored session (changing it gives a 503 unless regenerated).
- `MemcacheSessionInterface(..., cas=True)`: check-and-set saves; a save over
  a newer session gets a 409.
- `regenerate()` removes the old id after the new one is saved.
- Backend API: `_get_value(key)` takes the full key.
- A warning when the in-memory store runs with more than one worker.

## 0.9.0

- Packaging: `pyproject.toml`, Python 3.10+, Sanic 23.3+; tagged releases.
- `expiry=0` (or `None`) is a browser-session cookie with no store expiry;
  the memcached store TTL is capped at 30 days while the cookie keeps the
  full `Max-Age`.
- Session ids are random only: invalid or unknown ids get a new id; new
  public `interface.regenerate(request)`.
- Read errors and corrupt data give a new, empty session (logged); write
  errors raise `SessionStoreError` and the response becomes a 503.
- Sessions are saved only when changed.
- In-memory store: O(1) lookups and periodic purging.
- Cookies: `Secure`, `HttpOnly`, `SameSite=Lax` defaults, UTC `Expires`,
  deletion with the same flags.
- Redis, aioredis and MongoDB backends removed (memcached and in-memory
  only); standard `json` instead of `ujson`.
