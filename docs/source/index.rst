sanic_session
=========================================

.. toctree::
   :maxdepth: 1

   self
   using_the_interfaces
   api
   configuration
   testing

sanic_session is an extension for Sanic that integrates server-backed sessions with a dict-like API.

Install it with pip: :code:`pip install "sanic-session[aiomcache] @ git+https://github.com/jskenney/sanic-session@v0.9.0"`

Sessions can be stored in:

* memcached (:code:`MemcacheSessionInterface`, through :code:`aiomcache`)
* the server process (:code:`InMemorySessionInterface`, for testing and development)

See :ref:`using_the_interfaces` for instructions on using each.

A simple example uses the in-memory session interface:

.. code-block:: python

    from sanic import Sanic
    from sanic.response import text
    from sanic_session import Session, InMemorySessionInterface

    app = Sanic("ExampleApp")
    Session(app, interface=InMemorySessionInterface())

    @app.route("/")
    async def index(request):
        # interact with the session like a normal dict
        request.ctx.session["foo"] = request.ctx.session.get("foo", 0) + 1
        return text(str(request.ctx.session["foo"]))
