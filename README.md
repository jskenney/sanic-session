# sanic-session (jskenney fork)

Server-backed sessions for [Sanic](https://sanic.dev), stored in memcached or in the server process. This fork of [ahopkins/sanic-session](https://github.com/ahopkins/sanic-session) is maintained for [Modular Sanic](https://github.com/jskenney/modular-sanic). It supports only the memcached and in-memory backends; the Redis and MongoDB backends were removed.

Requires Python 3.10+ and Sanic 23.3+.

## Installation

```bash
pip install "sanic-session[aiomcache] @ git+https://github.com/jskenney/sanic-session@v0.10.0"
```

The `aiomcache` extra installs the memcached driver; leave it out if you only use the in-memory backend. Pin a tag (as above) so installs are reproducible.

## Example

```python
import aiomcache
from sanic import Sanic
from sanic.response import text
from sanic_session import Session, MemcacheSessionInterface

app = Sanic(name="ExampleApp")
Session(app, interface=MemcacheSessionInterface(aiomcache.Client("127.0.0.1", 11211)))

@app.route("/")
async def index(request):
    # interact with the session like a normal dict
    request.ctx.session["foo"] = request.ctx.session.get("foo", 0) + 1
    return text(str(request.ctx.session["foo"]))
```

For development and tests, use `InMemorySessionInterface()` instead. It is not shared between workers and is lost on restart.

## How it behaves

- **Cookies** are `HttpOnly`, `Secure` and `SameSite=Lax` by default, always with `Path=/`, so a `__Host-` cookie name works. `Secure` cookies are only sent over HTTPS, or to `http://localhost`. Pass `secure=False` for plain-HTTP development on other hosts.
- **Expiry:** `expiry` (default 30 days) sets the cookie's `Max-Age` and a UTC `Expires`. `expiry=0` (or `None`) means no expiry: the cookie lasts until the browser closes, and the store keeps the session indefinitely. memcached keeps entries at most 30 days, so with a longer `expiry` a session unused for 30 days expires in the store.
- **Expiry slides:** an unchanged session is written again (renewing the store expiry and the cookie) once it was last written more than `refresh_interval` seconds ago, by default a tenth of `expiry`, so a session in use does not expire. `refresh_interval=0` turns this off: the session then expires `expiry` seconds after it last changed.
- **Saving:** a session is stored, and its cookie sent, only when it changed during the request, including changes inside nested lists and dicts (`session.setdefault("cart", []).append(item)`), or when it is due a refresh. Clearing it (`request.ctx.session.clear()`) deletes it from the store and removes the cookie. Requests that never touch the session cause no store writes.
- **Values** must be JSON types (str, int, float, bool, None, list, dict). Anything else (a `set`, a `datetime`) makes the save fail with a 500, and the log names the key. The key `_sanic_session_saved_at` is reserved.
- **Session ids** are random. A cookie that isn't a valid id, or names a session that isn't stored, gets a new id, so clients can't choose their own. Call `await interface.regenerate(request)` on logon, or when privileges change, to move the session to a new id. The old id is removed once the new one is saved; if that save fails, the old session is left as it was.
- **Store errors:** stored data that isn't valid JSON gives a new, empty session, and a warning is logged. If the store can't be read, the request gets an empty session that keeps the client's id and is not saved, so the stored session survives the outage; changing it gives a 503, unless it was regenerated. If the store can't be written, the response becomes a **503**, so a client never believes a change (such as a logon) was kept when it wasn't. The error response is changed in place, so your response middleware (security headers, CORS) still run. Pass `Session(app, interface, on_store_error=handler)` to return your own response, for example JSON; `handler(request, exc)` may be async and gets an exception whose `status` is 503, 409 or 500.
- **Ignored paths:** `Session(app, interface, ignore=("/static/", "/favicon.ico"))` gives those paths an empty session without reading or writing the store. `ignore` takes a path prefix, a list of prefixes, or a function of the request. Changes made on an ignored path are not saved, and a warning is logged.
- **Middleware order:** the session is opened before your request middleware (so they can use it), and saved before your response middleware run.
- **Concurrency:** each request saves the whole session, so when two requests for the same session overlap, the last one to finish wins, and changes the other made to different keys are lost. With memcached, `MemcacheSessionInterface(client, cas=True)` saves with check-and-set instead: a request whose session was saved by another request after it was read gets a **409** and its change is not written.
- **Workers:** `InMemorySessionInterface` (also the default when no interface is given) keeps sessions in each worker process separately, and a warning is logged when the server starts more than one worker. Use memcached with several workers.

See [docs/source/configuration.rst](docs/source/configuration.rst) for every option.

## Development

```bash
pip install -e .[dev]
ruff check sanic_session tests example
pytest            # the memcached tests need memcached on 127.0.0.1:11211
```

Also make sure to read [OWASP's Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
