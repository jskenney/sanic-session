# sanic-session fork: Review (second pass, v0.9.0)

Reviewed: `main` / tag `v0.9.0` at `fbcb14c` (2026-10-20). That is fix batch 5 (#1); there are no newer commits on this repository.

This pass focuses on **performance, bugs, runtime errors, architecture/design, and project health**, for use with modular-sanic.

Tags:
- **[verified]**: reproduced with Sanic 25.12.1, either through modular-sanic's live instance or a small sanic-testing app.
- **[measured]**: has numbers.
- **[reasoned]**: from reading the code.

No package code was changed.

## 1. What the fork is now

There are 5 modules, about 450 lines:

- **`base.py`:**
  - `SessionDict` is a `CallbackDict` that sets `modified` on top-level changes;
  - `BaseSessionInterface` provides `open`, `regenerate`, `save` and the cookie helpers.
- **`memcache.py`:** aiomcache, with the TTL capped at 30 days.
- **`memory.py`:** `ExpiringDict`.
- **`__init__.py`:** `Session` registers two middleware:
  - open at request priority 1000;
  - save at response priority -1000;
  - a `SessionStoreError` in save is turned into a plain-text 503.

**Request flow:**
1. **open:** read the cookie; if it is a 32-hex id, `get` it from the store; a missing or bad entry gives a new id.
2. **handler.**
3. **save:** only when `modified`. An empty session is deleted and its cookie removed. Otherwise the whole dict is written as JSON and `Set-Cookie` is sent.

## 2. Tool and test results

| Check | Result |
|---|---|
| `pytest` (46 tests, memory and memcached) | All pass. One DeprecationWarning, from `websockets.legacy` inside Sanic, not from this package. |
| `ruff check sanic_session tests` | Clean. |
| GitHub Actions "Tests" (Python 3.10–3.14 with a memcached service) | Green on `fixes-batch-5`, `main` and `v0.9.0` (runs 1–4). |
| modular-sanic regression probes with v0.9.0 | Passing, apart from fixture gaps; see modular-sanic REVIEW.md §2. |
| Store traffic (modular-sanic, memcached stats) | 0 sets for requests that don't change the session (was 1 per response before v0.9.0). 1 `get` for **every** request carrying the cookie, including static files. |

## 3. Findings

### HIGH

#### H-1. In-place changes to nested values are silently lost **[verified]**

"Save only when modified" (v0.9.0, M-1) relies on `CallbackDict`, which only sees **top-level** changes such as `__setitem__`, `pop` and `update`. Code like this changes the stored list without the dict noticing:

```python
cart = request.ctx.session.setdefault("cart", [])
cart.append(item)                      # or session["prefs"]["theme"] = "dark"
```

So `modified` stays False and nothing is written.

**Reproduced through modular-sanic with a two-route extension:**
- the first `/api/cart/add` returns `[1]` (the key is new, so the session saves);
- the second returns `[1, 2]`, but `/api/cart/show` then returns `[1]`.

The handler sees the change, the client is told it worked, and it is gone on the next request.

Before v0.9.0 this worked, because every response rewrote the store. It is a silent data-loss regression for any extension that keeps lists or dicts in the session. modular-sanic's own code only assigns top-level keys, so it isn't affected.

**Fix (small, keeps the write savings):**
1. In `open()`, keep the raw stored string on the session (`session_dict._loaded = val`).
2. In `save()`, serialize first. Write when `session.modified` **or** `val != session._loaded`. Skip only when both say unchanged.

This costs one `json.dumps` per request that has a stored session (microseconds for typical sessions) and no extra store traffic. Add a test for `setdefault(...).append(...)` and for nested-dict changes, for both backends.

### MEDIUM

#### M-1. The 503 for a store write failure skips the app's response middleware, and reveals a successful logon **[verified]**

`__init__.py:44-57`. `save_session` runs first among the response middleware (priority -1000) and **returns a new response**, so Sanic runs no further response middleware.

With memcached stopped, modular-sanic's 503 had only `content-length`, `connection` and `content-type`. It had:
- no `Content-Security-Policy`;
- no `X-Frame-Options`;
- no `nosniff`;
- no HSTS.

It is also plain text where the app speaks JSON.

The 503 only happens when the handler changed the session, so it tells a client that its request succeeded. For a logon, that turns an outage into a password oracle; see modular-sanic H-1.

**Fix:**
- Change the existing response in place rather than returning a new one: set `status = 503`, replace the body and content type, and drop any `Set-Cookie`. The app's middleware then still adds its headers.
- Offer an `on_store_error(request, exc)` hook, so the app can render its own JSON body.
- Fall back to returning a new response only for streaming responses, where the body can't be replaced.

#### M-2. A value that isn't JSON-serializable loses the whole save, silently, and skips the app's headers **[verified]**

`base.py:239`. `json.dumps(dict(session))` raises `TypeError` for a `set`, a `datetime`, `bytes` or a custom object. It is not a `SessionStoreError`, so:
- Sanic logs it as a response-middleware exception;
- the client receives the **original 200**, though nothing was saved;
- the remaining response middleware are skipped (the test app's `X-App` header was missing).

**Fix:** catch `TypeError`/`ValueError` around the `dumps` and log it as an application error naming the offending key. Either raise during the request, or treat it like a store error (M-1). Document that session values must be JSON types, or accept `dumps`/`loads` parameters.

#### M-3. Expiry doesn't slide: a session that is read but not written expires while in use **[verified]**

Since saves happen only on change, the store TTL and the cookie's `Max-Age` are refreshed only when something is written.

With `InMemorySessionInterface(expiry=2)`, a session read every 0.9 s was gone at 2.7 s. memcached behaves the same, because `get` doesn't extend `exptime`.

modular-sanic hides this today: `verify()` rewrites `visit` once a minute. Any other app, or a modular-sanic endpoint that doesn't call `verify()`, gets absolute expiry while the docs imply idle expiry.

**Fix:** add `refresh_interval` (for example `expiry // 10`):
- store the last save time in the session (`_saved`);
- in `save()`, also write (memcached: `touch`, which aiomcache supports, or a full write) and re-send the cookie when it is older than that.

Document the choice either way.

#### M-4. Every request with a session cookie reads the store, including static files **[measured]**

`open()` is eager. In modular-sanic each `/index.html`, `/logon/logo.webp` or `/favicon.ico` with a cookie costs one memcached `get` (1.00 per request, measured), and so does every route that never looks at the session.

A memcached `get` is fast (sub-millisecond on localhost), but it is pure overhead on asset-heavy pages, and it makes memcached a hard dependency of static files.

**Fix (pick one):**
- Add an `ignore` option (path prefixes or a predicate) that leaves those requests with a non-loading placeholder.
- Make `SessionDict` lazy: `open()` stores only the id, and an `await request.ctx.session.load()` (or a load on first access, done by the open middleware when the route is marked) fetches the data.

The first option is a small change and covers the static-file case.

### LOW

| ID | Where | Issue | Fix |
|---|---|---|---|
| L-1 | `base.py:155-174` | A transient **read** failure (timeout) gives a new empty session with a new id. If that request then changes the session (modular-sanic sets `motd` on `/auth/info`), the new id is saved and sent, and the user's real, still-stored session is replaced. That is a permanent logout from a momentary blip. | On read failure, keep the client's id and mark the session "unloaded" so `save()` skips it; only issue a new id when the id is invalid or truly absent. |
| L-2 | `base.py:40-43`, README | Concurrent requests: the last write wins (documented). With H-1's fix the window is unchanged; it is still worth offering memcached `cas` for apps that keep counters or carts. | Optional `gets`/`cas` in `MemcacheSessionInterface`. |
| L-3 | `base.py:182-202` | `regenerate()` deletes the old key immediately. If the later save fails (503), the user has neither the old nor the new session. Acceptable, but undocumented. | Document it, or delete the old key in `save()` after the new one is written. |
| L-4 | `base.py:110`, `memcache.py:98`, `memory.py:39` | `_get_value(prefix, sid)` takes a `prefix` that every backend ignores in favour of `self.prefix`, while `_set_value`/`_delete_key` take the full key. The backend API is inconsistent. | `_get_value(key)` with the full key, like the others. |
| L-5 | `__init__.py:31` | `Session(app)` without an interface silently uses the in-memory store (per worker, lost on restart). With several workers, logons appear to work, then don't. | Log a warning when the default is used and `app.state.workers > 1`. |
| L-6 | health | The package is small, typed loosely and fully tested end-to-end; CI is green across 3.10–3.14. There is no coverage report, and no test for nested mutation (H-1), non-JSON values (M-2) or idle expiry (M-3). | Add those three tests with the fixes; optionally `pytest --cov` in CI. |

## 4. Status of findings from the first review

| Item | Status | Notes |
|---|---|---|
| H-1 permanent and over-30-day sessions deleted | **Fixed** | `expiry=0` gives a browser-session cookie; the TTL is capped at 30 days. |
| H-2 backend and input errors give 500s or lost sessions | **Fixed** | Read errors give a new session; write errors give a 503. See the new M-1, M-2 and L-1. |
| H-3 packaging | **Fixed** | `pyproject.toml`, `v0.9.0` tag; installed by modular-sanic from the tag. |
| H-4 client-chosen ids, no regenerate | **Fixed** | Verified in modular-sanic (unknown id gives a new id; `regenerate` on logon). |
| M-1 write on every response | **Fixed** | Verified: 0 sets for unchanged sessions. It introduced H-1 and M-3. |
| M-2 in-memory store purge and lookups | **Fixed** | |
| M-3 cookie delete flags | **Fixed** | |
| M-4 tests, CI, release | **Fixed** | 46 tests, green CI. |
| M-5 abandoned drivers | **Fixed** | Redis and MongoDB removed. |
| L-1 to L-7 | **Fixed** | The L-4 concurrency note is now in the README; still listed above as L-2. |

## 5. Prioritized top 10

1. **H-1:** detect nested changes, by comparing against the loaded JSON, and add tests.
2. **M-1:** keep the app's headers on the store-error 503, by changing the response in place, and add an error hook.
3. **M-2:** handle values that aren't JSON-serializable explicitly, never as a silent 200.
4. **M-3:** add sliding expiry (`refresh_interval` or `touch`), or document absolute expiry.
5. **L-1:** don't replace a client's session id because of a transient read error.
6. **M-4:** add an `ignore` option for static paths, to skip the store read.
7. **L-6:** add tests for H-1, M-2 and M-3; add coverage in CI.
8. **L-5:** warn when the in-memory default is used with several workers.
9. **L-3:** document, or reorder, the regenerate delete.
10. **L-4 and L-2:** make the backend API consistent; optional `cas`.
