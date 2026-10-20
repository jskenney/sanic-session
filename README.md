# sanic-session (jskenney fork)

Server-backed sessions for [Sanic](https://sanic.dev), stored in memcached or in the server process. This fork of [ahopkins/sanic-session](https://github.com/ahopkins/sanic-session) is maintained for [Modular Sanic](https://github.com/jskenney/modular-sanic). It supports only the memcached and in-memory backends; the Redis and MongoDB backends were removed.

Requires Python 3.10+ and Sanic 23.3+.

## Installation

```bash
pip install "sanic-session[aiomcache] @ git+https://github.com/jskenney/sanic-session@v0.9.0"
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
- **Saving:** a session is stored, and its cookie sent, only when it changed during the request. Clearing it (`request.ctx.session.clear()`) deletes it from the store and removes the cookie. Requests that never touch the session cause no store traffic.
- **Session ids** are random. A cookie that isn't a valid id, or names a session that isn't stored, gets a new id, so clients can't choose their own. Call `await interface.regenerate(request)` on logon, or when privileges change, to move the session to a new id.
- **Store errors:** if the store can't be read (memcached down, corrupt data), the request continues with a new, empty session and a warning is logged. If it can't be written, the response is replaced with a **503**, so a client never believes a change (such as a logon) was kept when it wasn't.
- **Middleware order:** the session is opened before your request middleware (so they can use it), and saved before your response middleware run.
- **Concurrency:** each request saves the whole session, so when two requests for the same session overlap, the last one to finish wins. Changes the other made to different keys are lost.

See [docs/source/configuration.rst](docs/source/configuration.rst) for every option.

## Development

```bash
pip install -e .[dev]
ruff check sanic_session tests example
pytest            # the memcached tests need memcached on 127.0.0.1:11211
```

Also make sure to read [OWASP's Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
