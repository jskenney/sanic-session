import socket
import uuid

import pytest
from sanic import Sanic, response

from sanic_session import InMemorySessionInterface, MemcacheSessionInterface, Session

Sanic.test_mode = True


def memcached_available(host="127.0.0.1", port=11211):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


MEMCACHED = memcached_available()


def make_interface(backend, **kwargs):
    if backend == "memory":
        return InMemorySessionInterface(**kwargs)
    import aiomcache

    port = kwargs.pop("port", 11211)
    return MemcacheSessionInterface(aiomcache.Client("127.0.0.1", port), **kwargs)


def make_app(interface):
    """A Sanic app with a session and a few routes that use it."""
    app = Sanic(f"test_{uuid.uuid4().hex}")
    Session(app, interface=interface)

    @app.get("/set")
    async def set_(request):
        request.ctx.session["n"] = request.ctx.session.get("n", 0) + 1
        return response.json({"n": request.ctx.session["n"]})

    @app.get("/get")
    async def get(request):
        return response.json(dict(request.ctx.session))

    @app.get("/clear")
    async def clear(request):
        request.ctx.session.clear()
        return response.json({})

    @app.get("/regenerate")
    async def regenerate(request):
        await interface.regenerate(request)
        return response.json({"sid": request.ctx.session.sid})

    @app.get("/static")
    async def static(request):
        return response.text("x")

    return app


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "memcache",
            marks=pytest.mark.skipif(not MEMCACHED, reason="memcached not running"),
        ),
    ]
)
def backend(request):
    return request.param
