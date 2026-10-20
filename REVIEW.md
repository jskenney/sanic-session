# sanic-session fork: Review for use with modular-sanic

Reviewed at `f1494fb` on main, 2026-10-20. The review covers the fork itself, and what it needs so that [modular-sanic](https://github.com/jskenney/modular-sanic) (at `deba8a1`) can rely on it rather than working around it. No code was changed. This file is the only addition.

**How it was reviewed:**
- I read every source, test, packaging and CI file, and the modular-sanic code that uses the fork.
- I ran the test suite, `ruff`, and a normal `pip install` on Python 3.13.
- I ran a small Sanic 25.12.1 app on the memcached and in-memory backends against real memcached 1.6, and tested it with `curl`.

Findings marked **[verified]** were reproduced that way.

---

## 1. What the fork is, and how modular-sanic uses it

**History.** The fork is upstream `sanic-session` 0.8.0 (Adam Hopkins' last commit was 2022-12-27) plus two commits:
- `f77201b` (2024): Sanic's new cookie API (`response.add_cookie`, cookie attributes instead of dict access), from a community fix;
- `f1494fb` (2025): drops the removed `loop` argument from the MongoDB listener.

**Structure.**

| File | Role |
|---|---|
| `__init__.py` | `Session(app, interface)` registers two middlewares by pushing onto `app.request_middleware` / `app.response_middleware`, and imports every backend |
| `base.py` | `SessionDict` (a dict with a `modified` flag), `BaseSessionInterface.open()` (cookie → stored JSON → `request.ctx.session`) and `save()` (JSON → store, then `Set-Cookie`) |
| `memcache.py`, `memory.py` | The two backends modular-sanic uses: aiomcache, and an in-process `ExpiringDict` |
| `redis.py`, `aioredis.py`, `mongodb.py` | Other backends (asyncio_redis, aioredis 1.x/2.x, sanic_motor) |
| `utils.py` | `CallbackDict` (sets `modified`) and `ExpiringDict` |

**How modular-sanic uses it** (`server.py` on its `main`):
- It creates `MemcacheSessionInterface` or `InMemorySessionInterface` with `cookie_name="__Host-session"`, `secure=True`, a configurable `samesite`, and `expiry=min(AUTH_VALID, 2592000)` (`server.py:42`). That cap exists only because of H-1 below.
- It subclasses both interfaces with a `SaveWhenModified` mixin that skips `save()` for unmodified sessions (`server.py:47-52`). That is a workaround for M-1.
- It rotates the session ID on every logon or user switch by reaching into private internals (`server.py:386-389`): `request.app.ctx.extensions['session'].interface`, `interface._delete_key(interface.prefix + session.sid)`, then assigning `session.sid` and `session.modified`. This is the gap behind H-4.
- It installs the fork as `sanic_session@git+https://github.com/jskenney/sanic-session` with no tag or commit pinned (`requirements.txt:16`), and needs `pip install --no-build-isolation` (`start.sh:16`) because of H-3.

---

## 2. Tool and test results

| Check | Result |
|---|---|
| `pytest tests` | **21 failed, 18 passed, 1 error.** 10 tests still read cookies dict-style (`TypeError: 'CookieJar' object is not subscriptable`). 11 Redis tests fail because `asyncio_redis` isn't installed. The error is a missing `event_loop` fixture, removed from pytest-asyncio. |
| GitHub Actions | **No runs have ever been recorded on the fork.** Forks start with workflows disabled. The workflow targets Python 3.7–3.11 with `actions/*@v2`. Python 3.7 isn't available on current `ubuntu-latest`. |
| `ruff --select E,F,W,B` | 2 × B028 (`warnings.warn` without `stacklevel`). |
| `pip install .` (Python 3.13) | **Fails:** `BackendUnavailable: Cannot import 'setuptools.build_meta'`. It only installs with `--no-build-isolation` and setuptools already present. |
| Installed metadata | `Version: 0.8.0` (the same as upstream on PyPI), `Home-page: http://github.com/subyraman/sanic_session`. No tags exist. |

---

## 3. Findings

### HIGH

#### H-1. "Permanent" sessions, and memcached sessions longer than 30 days, are deleted immediately **[verified]**
- `base.py:69-71` sends `Max-Age=self.expiry`.
- The docs (`docs/source/configuration.rst`) say `expiry=0` makes a session permanent. In practice `expiry=0` sends **`Max-Age=0`**, which tells the browser to delete the cookie at once. This happens with both memcached and in-memory.
- With the in-memory backend, `expiry=0` also stores the session as already expired (`utils.py:90`), so the next request sees `{}`.
- `memcache.py:70-72` turns any `expiry` over 30 days into `0`, again producing `Max-Age=0`. modular-sanic hit this with `AUTH_VALID=3000000` (everyone logged out at once) and now caps the value itself.

**Fix.**
- Treat `expiry=0`/`None` as "no expiry": send a session cookie (no `Max-Age`/`Expires`) and store with no TTL (memcached `exptime=0`).
- For memcached, cap the **store** TTL at 2,592,000 seconds but keep the requested `Max-Age` on the cookie.
- Never send `Max-Age=0` except when deleting.

#### H-2. Backend and input problems become 500 errors, or silently lost sessions **[verified]**
- `base.py:121-133`: the cookie value goes straight into a memcached key. A cookie containing a space (`session="a b"`), or longer than 250 characters, makes aiomcache raise `ValidationException`, and **every request from that browser gets a 500**, including static pages.
- `base.py:130`: a corrupt stored value (`ujson.loads`) also gives a 500.
- **When memcached is down**, any request that carries a session cookie gives a 500.
- Worse, a session **write** fails silently: `save()` runs in response middleware, Sanic logs the exception, and the client gets **200 with no `Set-Cookie`**. A logon therefore "succeeds" while the session is lost. modular-sanic's logon endpoints can't detect this.

**Fix.**
- Validate the cookie before use: for example, accept only 32 lowercase hex characters, otherwise treat it as absent.
- Treat read errors (connection errors, bad JSON) as an empty session and log them, instead of raising.
- Make write failures visible. One option is a `save()` that raises a specific `SessionStoreError` the app can turn into a 503. Another is a `strict=True` mode that saves before the handler returns (for example, an awaitable `await request.ctx.session.save_now()` that logon endpoints can call).

#### H-3. Packaging blocks a normal install, the package can crash on import, and it can't be pinned **[verified]**
- `pyproject.toml:2` requires `setuptools<60`, which can't build on Python ≥3.12. modular-sanic's `start.sh`, and the Docker image, need `--no-build-isolation` because of this.
- `__init__.py:1-5` imports every backend. `aioredis.py:3-6` only catches `ImportError`. If `aioredis` 2.0.1 is installed (for example, pulled in by another package), **`import sanic_session` fails** on Python 3.11+ with `TypeError: duplicate base class TimeoutError`, and modular-sanic can't start.
- The version is still `0.8.0` (`setup.py:36`), the same as the upstream release on PyPI, and no tags exist. modular-sanic installs whatever the fork's HEAD is at install time, so builds aren't reproducible.

**Fix.**
- Use `requires = ["setuptools>=61", "wheel"]`, and move the metadata into `pyproject.toml` (`requires-python = ">=3.10"`).
- Catch `Exception` (not just `ImportError`) around optional driver imports, or import backends lazily.
- Bump to a distinct version (for example `0.9.0`), tag it, and pin modular-sanic to `@v0.9.0`.

#### H-4. Client-chosen session IDs are accepted, and there is no supported way to change the ID **[verified]**
`base.py:121-133` accepts any cookie value as the session ID, even one that was never issued, and `save()` stores data under it. A request with `Cookie: session=attacker-chosen` got its data stored under `session:attacker-chosen` and the same ID echoed back. modular-sanic closes the session-fixation hole by changing the ID at logon, but it has to use private attributes to do so (section 1).

**Fix.**
- When the cookie's ID has no stored value, issue a new random ID.
- Add a public `async def regenerate(self, request)` on the interface (delete the old key, assign a new ID, mark the session modified), or `request.ctx.session.regenerate()`.

### MEDIUM

#### M-1. The store is written on every response, even when nothing changed **[verified]**
`base.py:158-168`:
- **Unchanged sessions:** for a request carrying a valid session, `save()` rewrites the session and re-sends `Set-Cookie` even when nothing changed. One `/static` request caused one memcached `set` and one `Set-Cookie`.
- **No session at all:** for a request with no cookie, `save()` sends a memcached `DELETE` for the brand-new, never-stored ID. 20 cookie-less requests caused 20 `delete_misses`.

On a busy site, every static file costs a memcached round trip. modular-sanic's `SaveWhenModified` mixin avoids both.

**Fix.** Save only when `session.modified`. Skip the delete when the session was never stored (track whether `open()` found a value). If sliding expiry is wanted, add an option that refreshes the TTL and cookie at most once per interval.

#### M-2. The in-memory backend never removes expired sessions, and lookups copy the whole store **[verified]**
- `utils.py:96-97`: `ExpiringDict.get()` runs `dict(self).get(key)`, which copies the entire store on every lookup. That took **1.2 ms per request at 100,000 sessions**, versus 0.005 ms at 1,000.
- Expired entries are removed only when that exact key is read again. After 20,000 sessions with `expiry=1` had expired, **all 20,000 were still held**.
- modular-sanic falls back to this backend when memcached is unavailable, and each anonymous visit to a logon page creates a session (`motd`). Memory therefore grows without limit, and requests slow down as it does.

**Fix.** Use `super().get(key)`. Purge expired entries periodically, or on `set()` every N inserts, for example with a min-heap or an ordered dict keyed by expiry. Optionally cap the number of entries.

#### M-3. Deleting a cookie ignores its domain and path **[verified]**
`base.py:51-52` calls `response.delete_cookie(self.cookie_name)`, with no domain or path. A cookie set with `domain="example.test"` was "deleted" with a cookie that had no `Domain`, so browsers keep the original and the user stays logged on after `session.clear()`.

**Fix.** Pass the same attributes the cookie was set with: `path="/", domain=self.domain, secure=self.secure, samesite=...`. This also matters for `__Host-` cookies, which must be deleted with `Secure; Path=/` and no `Domain`.

#### M-4. Tests, CI and release workflow are stale
- **Tests:** 21 fail, as summarised in section 2.
- **CI:** it has never run on the fork, and targets Python 3.7–3.11 with `@v2` actions. `tox.ini:17-18` installs extras that don't exist (`.[memcached]`, `.[msgpack]`).
- **Release:** `python-publish.yml` would upload to PyPI as `sanic-session`, the upstream project's name, on any GitHub release.

**Fix.**
- Update the tests to `response.cookies.get_cookie(name)`, and to `pytest-asyncio`'s current API (`@pytest.mark.asyncio` with no `event_loop` fixture).
- Add end-to-end tests that use `sanic-testing` with a real Sanic app, covering memcached via a service container in GitHub Actions.
- Test on Python 3.12–3.14 with Sanic 25.
- Enable Actions on the fork, and delete or disable the PyPI publish workflow unless the fork is published under its own name.

#### M-5. The non-memcached backends depend on abandoned drivers
- **`aioredis.py`:** aioredis was archived in 2021 and breaks on Python 3.11+ (H-3).
- **`redis.py`:** `asyncio_redis`'s last release was 0.16.0, and it is unmaintained.
- **`mongodb.py`:** `sanic_motor`/Motor are superseded by PyMongo's async API, and `mongodb.py:137` uses the deprecated `datetime.utcnow()`.

modular-sanic uses none of these, but their import side effects can break it (H-3).

**Fix.** Drop them, or replace the two Redis backends with one on `redis.asyncio` (redis-py ≥4.2). Keep only memcached and in-memory if the fork is meant for modular-sanic.

### LOW

| ID | Location | Issue | Fix |
|---|---|---|---|
| L-1 | `base.py:55-57,70` | `Expires` is built with `datetime.fromtimestamp()` (local time, no time zone) and then sent labelled `GMT`. With `TZ=America/New_York`, the default in modular-sanic-docker, it was **4 hours early** **[verified]**. Browsers prefer `Max-Age`, but other clients may not. | `datetime.fromtimestamp(expires, tz=timezone.utc)`. |
| L-2 | `__init__.py:45-46` | Middleware is added by pushing onto `app.request_middleware`/`app.response_middleware` directly. That bypasses Sanic's `register_middleware(..., priority=)` ordering and depends on internals. | `app.register_middleware(open_fn, "request", priority=1000)` and `app.register_middleware(save_fn, "response", priority=-1000)`. |
| L-3 | `base.py:62-80`; interface defaults | `secure` defaults to `False`, `samesite` to unset (Sanic then uses Lax), and `add_cookie` is called with `secure=False` and then patched afterwards. | Default `secure=True` and `samesite="Lax"`, and pass the final values to `add_cookie` in one call. |
| L-4 | whole session | Concurrent requests on one session overwrite each other, because the whole dict is saved and the last write wins. For example, two tabs changing different keys at once lose one of the changes. | Document it. Optionally save only changed keys, or support compare-and-swap (`cas`) with memcached. |
| L-5 | `setup.py:36-66`, `README.md`, `docs/` | Metadata and docs still describe upstream: author, `url` (subyraman), Python 3.7–3.11 classifiers, Read the Docs links. The docs also promise the broken `expiry=0` behaviour (H-1). | Update to the fork, and correct the expiry docs. |
| L-6 | `base.py:6`, `setup.py` | `ujson` is a required dependency just for JSON. | Use the standard `json` module, or let the caller pass `dumps`/`loads`, for example `orjson`. |
| L-7 | `memcache.py:70`, `utils.py:96-101` | `warnings.warn` has no `stacklevel` (ruff B028). `ExpiringDict.get` treats falsy stored values as missing. | `stacklevel=2`; check `key in self` explicitly. |

---

## 4. What modular-sanic needs from the fork

These changes would let modular-sanic delete its workarounds and trust its session layer. They are listed in the order that unblocks the most.

| # | Change in the fork | Then in modular-sanic |
|---|---|---|
| 1 | **Packaging (H-3):** `setuptools>=61` build backend, import-safe optional backends, version `0.9.0` and a git tag | Pin `sanic_session@git+https://github.com/jskenney/sanic-session@v0.9.0`; drop `--no-build-isolation` from `start.sh` and from modular-sanic-docker's Dockerfile |
| 2 | **Expiry semantics (H-1):** no `Max-Age=0` except on delete; memcached TTL capped at 30 days while the cookie keeps the requested age | Remove the `min(AUTH_VALID, 2592000)` cap (`server.py:42`) |
| 3 | **Save only when modified (M-1)**, with no DELETE for sessions that were never stored | Remove the `SaveWhenModified` mixin and its two subclasses (`server.py:47-58`) |
| 4 | **Public `regenerate()` (H-4)**, and new IDs for unknown ones | `_rotate_session` (`server.py:386-389`) calls `await interface.regenerate(request)` instead of private attributes |
| 5 | **Safe input and backend errors (H-2):** validated session IDs, read errors treated as an empty session, write errors raised as `SessionStoreError` | Catch `SessionStoreError` in the logon endpoints and return 503 instead of a false success; malformed cookies stop causing 500s |
| 6 | **Correct cookie deletion (M-3)** | Logoff reliably clears the cookie, including if `domain` is ever used |
| 7 | **In-memory store purge and O(1) lookups (M-2)** | The memcached-down fallback stops leaking memory |
| 8 | **UTC `Expires` (L-1)** | Correct cookie dates under modular-sanic-docker's `TZ` |
| 9 | **Tests and CI (M-4)** for memcached and in-memory on Python 3.12–3.14 / Sanic 25 | modular-sanic's own tests (when added) can rely on the session layer's behaviour |
| 10 | **Drop or replace the abandoned backends (M-5)** | Smaller install, and no import-time breakage |

---

## 5. Prioritized top 10

1. **H-3.** Fix the build backend and the import-time crash, then version and tag the fork so modular-sanic can pin it.
2. **H-1.** Correct the expiry handling (no `Max-Age=0`, a proper 30-day memcached cap, working "permanent" sessions).
3. **H-2.** Validate session IDs and handle backend errors: no 500s for bad cookies or corrupt data, and no silently lost logons when memcached is down.
4. **H-4.** Reject unknown session IDs, and add a public `regenerate()` for modular-sanic's ID rotation.
5. **M-1.** Save only modified sessions, and stop sending memcached DELETEs for every cookie-less request.
6. **M-4.** Repair the tests, add end-to-end memcached tests, and enable CI on Python 3.12–3.14; disable the PyPI publish workflow.
7. **M-3.** Delete cookies with the same domain, path and flags they were set with.
8. **M-2.** Make the in-memory store purge expired sessions and stop copying itself on every lookup.
9. **M-5.** Drop or modernise the Redis and MongoDB backends.
10. **L-1 to L-3.** UTC `Expires`, middleware registered through Sanic's public API with priorities, and secure cookie defaults.
