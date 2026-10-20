.. _testing:

Testing
=====================

Use :code:`InMemorySessionInterface` in tests, for example by choosing the interface from the environment:

.. code-block:: python

    import os

    import aiomcache
    from sanic import Sanic
    from sanic_session import Session, InMemorySessionInterface, MemcacheSessionInterface

    app = Sanic("App")

    if os.environ.get("TESTING"):
        Session(app, interface=InMemorySessionInterface())
    else:
        Session(app, interface=MemcacheSessionInterface(aiomcache.Client("127.0.0.1", 11211)))

With `sanic-testing <https://github.com/sanic-org/sanic-testing>`_, send the cookie from one response in the next request:

.. code-block:: python

    import re

    async def test_counter():
        _, res = await app.asgi_client.get("/")
        sid = re.search(r"session=([^;]*)", res.headers["set-cookie"]).group(1)
        _, res = await app.asgi_client.get("/", headers={"cookie": f"session={sid}"})
        assert res.text == "2"

The session cookie is ``Secure`` by default; the test client accepts it, and so do browsers on ``http://localhost``.

This project's own tests are in ``tests/``: run ``pip install -e .[dev]`` and ``pytest`` (the memcached tests run when memcached listens on 127.0.0.1:11211, and are skipped otherwise).
