# sanic-session fork: Review (third pass, v0.10.0)

Reviewed: `main` / tag `v0.10.0` at `4c28b01` (2026-11-04), for use with modular-sanic.

This pass covers bugs, runtime errors, security, design, performance and health, as before. It also adds **§4 Enhancement opportunities**.

Tags:
- **[verified]**: reproduced with Sanic 25.12.1 (sanic-testing, and modular-sanic's live instance).
- **[measured]**: has numbers.
- **[reasoned]**: from reading the code.

No package code was changed.

## 1. Where the project stands

The fork is small: 5 modules, about 820 lines, plus 66 tests. Since the first review it has gained:
- random-only session ids and `regenerate()`;
- saving only when the session changed, including nested values;
- JSON errors that keep the app's headers;
- an `ignore` option for paths that need no session;
- check-and-set (`cas`) for memcached;
- sliding expiry;
- CI on Python 3.10 to 3.14.

modular-sanic uses all of it.

The one serious problem is in the newest feature: the sliding-expiry refresh can overwrite a concurrent change (H-1). The rest are health items and enhancements.

## 2. Checks run

| Check | Result |
|---|---|
| `pytest` (66 tests, memory and memcached) | All pass. Coverage **94%** (372 statements). One upstream DeprecationWarning (`websockets.legacy`, from Sanic). |
| `ruff check sanic_session tests example` | Clean. |
| `bandit -r sanic_session` | No findings. |
| GitHub Actions "Tests" on `main` | Green (run 8, `4c28b01`). |
| modular-sanic `main` with v0.10.0 | All its regression probes pass, apart from fixture gaps (see modular-sanic REVIEW.md §2). |
| Refresh race, see H-1 | Reproduced on both backends. |

## 3. Findings

### HIGH

#### H-1. The sliding-expiry refresh writes stale data over a concurrent change, and with `cas=True` gives read-only requests a 409 **[verified]**

Since v0.10.0, `save()` rewrites an **unchanged** session once `refresh_interval` has passed, so active sessions don't expire (`base.py`, `refresh = session.stored and ...`). It writes the data **as this request loaded it**.

Reproduced with two overlapping requests on one session (`refresh_interval=0.01`):

1. A slow read-only request loads `{"n": 1}`.
2. A second request changes `n` to 2 and saves.
3. The read-only request finishes and refreshes, writing `{"n": 1}` back.

Afterwards `n` is **1** (it should be 2). With `refresh_interval=0` the change survives.

With memcached and `cas=True`, the refresh write uses the token from the read. So the **read-only request fails with 409 Conflict**, though it changed nothing.

Before v0.10.0, read-only requests never wrote, so this window didn't exist. The fork's README says the last write wins between overlapping *changing* requests, but this adds losses caused by requests that change nothing.

For modular-sanic the effect is small: `verify()` writes `visit` every minute, and its refresh interval is 16.8 hours. For any other app it's silent data loss.

**Fix:** a refresh must never overwrite a newer value:
- **memcached:** always do refresh writes with `gets`/`cas`, even when `cas=False`. When the `cas` fails, **skip the refresh silently**: another request has just written the session, which refreshed it anyway. Never return 409 for a refresh.
- **In-memory:** write only if the stored string still equals the one loaded (one event loop, so this is atomic).
- **Tests:** add both scenarios: a concurrent read and write keeps the write, and a refresh under `cas=True` returns 200.

### MEDIUM

#### M-1. CI doesn't test the minimum Sanic version the package claims **[reasoned]**

`pyproject.toml` says `sanic>=23.3`. CI installs the latest Sanic only. Version 0.10.0 was tested on Sanic 23.3 by hand once (66 passed). But `_replace_response` relies on `HTTPResponse` internals (`headers.clear()`, the `body` setter), and the cookie code on `add_cookie` signatures, which changed between Sanic releases.

**Fix:** a CI matrix row with `sanic==23.3.*` and `sanic-testing==23.3.*` (on Python 3.11), next to the latest. Or raise the minimum to what is actually tested.

#### M-2. The in-memory store has no size limit **[reasoned]**

`ExpiringDict` purges expired entries every 60 s, but has no maximum. An app that stores sessions for anonymous visitors in memory (modular-sanic's fallback, or a library user) grows by one entry per new client until the entries expire. That is 7 days in modular-sanic, 30 by default. Each entry is small, but it's an easy memory leak under scraping.

**Fix:** a `max_entries` option, default 100,000, that evicts the oldest entries (`OrderedDict`).

### LOW

| ID | Where | Issue | Fix |
|---|---|---|---|
| L-1 | `base.py` (`SAVED_AT_KEY`) | An app that sets `session["_sanic_session_saved_at"]` has it silently overwritten and then hidden on load. | Raise on that key in `__setitem__`, or store the time outside the data (see E-1's `touch` design). |
| L-2 | releases | There is no CHANGELOG. 0.9.0 and 0.10.0 changed behaviour: status codes (503, 409, 500), the reserved key, `_get_value(key)`, and saving on nested changes. Upgrade notes exist only in commit messages. | `CHANGELOG.md`, and GitHub releases for the tags. |
| L-3 | `__init__.py` | `on_store_error` can raise or return a non-response. If it raises, Sanic logs a response-middleware error and sends the original (now inconsistent) response. | Catch exceptions from the hook, log them, and fall back to the default 503. |
| L-4 | docs | `docs/` (Sphinx) is accurate but isn't built or published anywhere; the README links to `configuration.rst` as source. | Build in CI and publish to GitHub Pages, or fold it into the README. |

## 4. Enhancement opportunities

| ID | Enhancement | Why | Effort |
|---|---|---|---|
| **E-1** | **Safe refresh** (the H-1 fix), optionally with memcached `touch`. A refresh only extends the store expiry and re-sends the cookie, never rewriting data. The last-refresh time would then live in the cookie's own `Expires`, or in a tiny side key. | Fixes H-1, and makes refreshes cheap: no serialising, no write of the whole session. | Small |
| **E-2** | **Lazy loading.** `request.ctx.session` loads from the store on first access, not in middleware. | `ignore` becomes unnecessary in most apps. Routes that never look at the session cost nothing, which is most API routes in an app that also uses API keys. | Medium (the session is a dict; it needs a lazy proxy, or an explicit `await request.ctx.session.load()` in middleware that accessors call) |
| **E-3** | **A per-user session index and `revoke_user(user)`.** Optionally record `session ids for user X` (a memcached set per user, or an in-memory dict) when the app calls `interface.bind_user(request, user)`. | "Log out everywhere", and revoking sessions on password or access changes, for modular-sanic (its E-11). | Medium |
| **E-4** | **Idle and absolute lifetimes in the library.** `idle_timeout` and `max_lifetime` options checked on load, with a hook when a session expires. | modular-sanic re-implements both (`AUTH_VALID`, `AUTH_MAX`); every app needs them. | Small |
| **E-5** | **Session events.** `on_create`, `on_regenerate`, `on_destroy` and `on_store_error` hooks. | Audit logging (modular-sanic E-4) and metrics without subclassing. | Small |
| **E-6** | **Pluggable serialisation and size checks.** `dumps`/`loads` options (for example orjson), and a clear `SessionStoreError` when a session is bigger than the backend allows (memcached's default item limit is 1 MB). | Speed for large sessions; a readable error instead of a backend exception. | Small |
| **E-7** | **Typing.** Type hints throughout, `py.typed`, and mypy (or pyright) in CI. | Better editor help for extension authors; catches API drift. | Small |
| **E-8** | **CI hardening.** The minimum-Sanic row (M-1), a coverage floor (`--cov-fail-under=90`), and Dependabot for the workflow actions. | Keeps the 94% coverage, and the version claims honest. | Small |
| **E-9** | **Release process.** CHANGELOG (L-2), GitHub releases, and optionally publishing a wheel to a package index under a fork-specific name. | modular-sanic and its extensions pin `@v0.x.y` from git today, which needs `git` at install time (it's why modular-sanic-docker keeps git in its image). | Small |
| **E-10** | **A concurrency-safe mode for counters and carts.** A `session.atomic(key)` helper built on `cas`: retry the change on conflict instead of failing with 409. | `cas=True` currently gives an error the app has to handle. Most apps want "retry my change". | Medium |

## 5. Prioritized top 10 (issues and enhancements together)

1. **H-1 / E-1:** make refresh never overwrite: cas-or-skip, or `touch`. Release 0.10.1 and tell modular-sanic to upgrade.
2. **M-1 / E-8:** a CI row for the minimum Sanic, and a coverage floor.
3. **M-2:** cap the in-memory store.
4. **L-2 / E-9:** CHANGELOG and releases.
5. **E-4:** idle and absolute lifetimes in the library.
6. **E-3:** a per-user index and `revoke_user`, for "log out everywhere".
7. **E-5:** session event hooks, for auditing.
8. **E-2:** lazy loading.
9. **L-3:** make a failing `on_store_error` hook fall back to the default 503.
10. **E-7, E-6, L-1, L-4:** typing, serialisation options, the reserved key, published docs.

## 6. Status of findings from the second review

| Item | Status | Notes |
|---|---|---|
| H-1 nested changes lost | **Fixed** (0.10.0) | Verified in modular-sanic: the cart keeps `[1, 2, 3]`. |
| M-1 503 skipped the app's headers | **Fixed** | Verified: the 503 carries modular-sanic's security headers. |
| M-2 non-JSON values | **Fixed** | 500, with the key logged. |
| M-3 no sliding expiry | **Fixed, but with a new race** | See H-1. |
| M-4 store read for every request | **Fixed** (`ignore`) | modular-sanic ignores static files. E-2 would generalise it. |
| L-1 read blip replaced the session | **Fixed** | |
| L-2 concurrency | **Fixed** (`cas=True`) | The refresh interplay is in H-1. |
| L-3 regenerate order | **Fixed** | |
| L-4 backend API | **Fixed** | |
| L-5 in-memory with several workers | **Fixed** | Warning verified with 2 workers. |
| L-6 tests and coverage | **Fixed** | 66 tests, 94%. |
