import re
import time
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import pytest
from sanic import Sanic, response

from sanic_session import InMemorySessionInterface, Session
from sanic_session.base import SID_PATTERN

from .conftest import MEMCACHED, make_app, make_interface

memcached_only = pytest.mark.skipif(not MEMCACHED, reason="memcached not running")


def set_cookie(res):
    return res.headers.get("set-cookie", "")


def sid_of(res, name="session"):
    m = re.search(name + r"=([^;]*)", set_cookie(res))
    return m.group(1) if m else None


async def get(app, path, sid=None, cookie=None):
    headers = {}
    if sid is not None:
        headers["cookie"] = f"session={sid}"
    if cookie is not None:
        headers["cookie"] = cookie
    _, res = await app.asgi_client.get(path, headers=headers)
    return res


async def test_new_session_is_saved_with_secure_cookie(backend):
    app = make_app(make_interface(backend))
    res = await get(app, "/set")
    header = set_cookie(res)
    sid = sid_of(res)
    assert SID_PATTERN.fullmatch(sid)
    for part in ("Path=/", "Max-Age=2592000", "HttpOnly", "Secure", "SameSite=Lax"):
        assert part in header
    res = await get(app, "/get", sid)
    assert res.json == {"n": 1}


async def test_expires_is_utc(backend):
    app = make_app(make_interface(backend, expiry=3600))
    res = await get(app, "/set")
    expires = re.search(r"expires=([^;]+)", set_cookie(res), re.I).group(1)
    when = parsedate_to_datetime(expires.replace("-", " "))
    drift = (when - datetime.now(timezone.utc)).total_seconds() - 3600
    assert abs(drift) < 5


async def test_unmodified_session_is_not_saved(backend):
    interface = make_interface(backend)
    app = make_app(interface)
    sid = sid_of(await get(app, "/set"))
    writes = []
    original = interface._set_value

    async def counting(key, data):
        writes.append(key)
        return await original(key, data)

    interface._set_value = counting
    res = await get(app, "/get", sid)
    assert res.json == {"n": 1}
    assert not set_cookie(res)
    assert writes == []


async def test_requests_without_session_touch_nothing(backend):
    interface = make_interface(backend)
    app = make_app(interface)
    calls = []

    async def fail(*args):
        calls.append(args)
        raise AssertionError("store used")

    interface._set_value = interface._delete_key = fail
    res = await get(app, "/static")
    assert res.status == 200
    assert not set_cookie(res)
    assert calls == []


async def test_unknown_session_id_is_replaced(backend):
    app = make_app(make_interface(backend))
    planted = "0" * 32
    res = await get(app, "/set", planted)
    assert sid_of(res) != planted
    res = await get(app, "/get", planted)
    assert res.json == {}


@pytest.mark.parametrize(
    "cookie",
    ['session="a b"', "session=" + "x" * 300, "session=attacker-chosen"],
)
async def test_malformed_cookie_gets_new_session(backend, cookie):
    app = make_app(make_interface(backend))
    res = await get(app, "/set", cookie=cookie)
    assert res.status == 200
    assert SID_PATTERN.fullmatch(sid_of(res))


async def test_corrupt_stored_value_gives_empty_session(backend):
    interface = make_interface(backend)
    app = make_app(interface)
    sid = sid_of(await get(app, "/set"))
    await interface._set_value(interface.prefix + sid, "{not json")
    res = await get(app, "/get", sid)
    assert res.status == 200
    assert res.json == {}


async def test_clear_deletes_session_and_cookie(backend):
    interface = make_interface(backend, domain="example.test")
    app = make_app(interface)
    sid = sid_of(await get(app, "/set"))
    res = await get(app, "/clear", sid)
    header = set_cookie(res)
    assert "Max-Age=0" in header
    assert "Domain=example.test" in header
    assert "Secure" in header
    assert await interface._get_value(interface.prefix, sid) is None


async def test_delete_cookie_follows_secure_setting(backend):
    app = make_app(make_interface(backend, secure=False))
    sid = sid_of(await get(app, "/set"))
    header = set_cookie(await get(app, "/clear", sid))
    assert "Max-Age=0" in header
    assert "Secure" not in header


async def test_expiry_zero_is_a_browser_session_that_lasts(backend):
    app = make_app(make_interface(backend, expiry=0))
    res = await get(app, "/set")
    header = set_cookie(res)
    assert "Max-Age" not in header and "expires" not in header.lower()
    res = await get(app, "/get", sid_of(res))
    assert res.json == {"n": 1}


async def test_sessioncookie_omits_max_age(backend):
    app = make_app(make_interface(backend, sessioncookie=True, expiry=60))
    res = await get(app, "/set")
    assert "Max-Age" not in set_cookie(res)
    res = await get(app, "/get", sid_of(res))
    assert res.json == {"n": 1}


async def test_samesite_none_value_is_left_out(backend):
    app = make_app(make_interface(backend, samesite=None))
    assert "SameSite" not in set_cookie(await get(app, "/set"))


async def test_regenerate_keeps_data_and_drops_old_id(backend):
    interface = make_interface(backend)
    app = make_app(interface)
    old = sid_of(await get(app, "/set"))
    res = await get(app, "/regenerate", old)
    new = sid_of(res)
    assert new and new != old and res.json == {"sid": new}
    assert (await get(app, "/get", new)).json == {"n": 1}
    assert (await get(app, "/get", old)).json == {}


@memcached_only
async def test_memcache_long_expiry_keeps_cookie_and_caps_store():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        interface = make_interface("memcache", expiry=3000000)
    assert any("30 days" in str(w.message) for w in caught)
    # stacklevel points the warning at the caller, not at sanic_session
    assert "sanic_session" not in caught[0].filename
    assert interface._store_ttl() == 2592000
    app = make_app(interface)
    res = await get(app, "/set")
    assert "Max-Age=3000000" in set_cookie(res)
    assert (await get(app, "/get", sid_of(res))).json == {"n": 1}


async def test_store_write_failure_gives_503():
    interface = InMemorySessionInterface()

    async def broken(*args):
        raise ConnectionRefusedError("store down")

    interface._set_value = broken
    app = make_app(interface)
    res = await get(app, "/set")
    assert res.status == 503
    assert not set_cookie(res)


async def test_store_read_failure_gives_empty_session():
    interface = InMemorySessionInterface()
    app = make_app(interface)
    sid = sid_of(await get(app, "/set"))

    async def broken(*args):
        raise ConnectionRefusedError("store down")

    interface._get_value = broken
    res = await get(app, "/get", sid)
    assert res.status == 200 and res.json == {}


@memcached_only
async def test_memcached_down():
    app = make_app(make_interface("memcache", port=11999))
    assert (await get(app, "/static", "0" * 32)).status == 200
    assert (await get(app, "/set")).status == 503


async def test_app_middleware_can_use_the_session():
    seen = []
    app = Sanic("middleware_order")
    Session(app, interface=InMemorySessionInterface())

    @app.middleware("request")
    async def uses_session(request):
        seen.append(request.ctx.session.get("n"))

    @app.middleware("response")
    async def after(request, res):
        seen.append(res.headers.get("set-cookie") is not None)

    @app.get("/set")
    async def set_(request):
        request.ctx.session["n"] = 1
        return response.text("x")

    await get(app, "/set")
    assert seen == [None, True]


class SaveWhenModified:
    # modular-sanic's mixin (server.py)
    async def save(self, request, response):
        session = getattr(request.ctx, self.session_name, None)
        if session is not None and not session.modified:
            return
        await super().save(request, response)


class MixinSessions(SaveWhenModified, InMemorySessionInterface):
    pass


async def test_modular_sanic_style_subclass_and_rotation():
    interface = MixinSessions(cookie_name="__Host-session", samesite="Strict")
    app = Sanic("modular_compat")
    Session(app, interface=interface)

    @app.get("/logon")
    async def logon(request):
        session = request.ctx.session
        # modular-sanic's _rotate_session, using the interface internals
        iface = request.app.ctx.extensions["session"].interface
        await iface._delete_key(iface.prefix + session.sid)
        session.sid = iface._new_sid()
        session.modified = True
        session["user"] = "alice"
        return response.text("ok")

    @app.get("/who")
    async def who(request):
        return response.json({"user": request.ctx.session.get("user")})

    res = await get(app, "/logon", cookie="__Host-session=" + "1" * 32)
    sid = sid_of(res, "__Host-session")
    assert sid and sid != "1" * 32
    assert "SameSite=Strict" in set_cookie(res)
    res = await get(app, "/who", cookie=f"__Host-session={sid}")
    assert res.json == {"user": "alice"}
    assert not set_cookie(res)


async def test_in_memory_store_expires_entries():
    interface = InMemorySessionInterface(expiry=1)
    app = make_app(interface)
    sid = sid_of(await get(app, "/set"))
    time.sleep(1.2)
    assert (await get(app, "/get", sid)).json == {}
