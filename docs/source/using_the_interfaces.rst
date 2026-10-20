.. _using_the_interfaces:

Using the interfaces
=====================

Install the memcached driver with the ``aiomcache`` extra::

    pip install "sanic-session[aiomcache] @ git+https://github.com/jskenney/sanic-session@v0.9.0"

Memcache
-----------------

Pass an :code:`aiomcache.Client` into the session interface:

.. code-block:: python

    import aiomcache

    from sanic import Sanic
    from sanic.response import text
    from sanic_session import Session, MemcacheSessionInterface

    app = Sanic("MemcacheApp")
    client = aiomcache.Client("127.0.0.1", 11211)
    Session(app, interface=MemcacheSessionInterface(client))


    @app.route("/")
    async def index(request):
        request.ctx.session["foo"] = request.ctx.session.get("foo", 0) + 1
        return text(str(request.ctx.session["foo"]))

In-Memory
-----------------

:code:`InMemorySessionInterface` keeps sessions in the server process: they are not shared between workers and are lost on restart, so use it for testing and development. Expired sessions are removed automatically.

.. code-block:: python

    from sanic import Sanic
    from sanic.response import text
    from sanic_session import Session, InMemorySessionInterface

    app = Sanic("InMemoryApp")
    Session(app, interface=InMemorySessionInterface())

Factory initialization
----------------------

The session can also be attached to an app later:

.. code-block:: python

    session = Session()

    def create_app():
        app = Sanic("FactoryApp")
        session.init_app(app, interface=InMemorySessionInterface())
        return app

Changing the session id
-----------------------

When a user logs on, move their session to a new id so an id seen before logon stops working:

.. code-block:: python

    @app.post("/login")
    async def login(request):
        ...  # check the credentials
        await request.app.ctx.extensions["session"].interface.regenerate(request)
        request.ctx.session["user"] = username
        return text("ok")
