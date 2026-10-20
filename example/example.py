from sanic import Sanic
from sanic.response import text

from sanic_session import InMemorySessionInterface, Session

app = Sanic(name="ExampleApp")
session = Session(app, interface=InMemorySessionInterface())


@app.route("/")
async def index(request):
    # interact with the session like a normal dict
    if not request.ctx.session.get("foo"):
        request.ctx.session["foo"] = 0

    request.ctx.session["foo"] += 1

    return text(str(request.ctx.session["foo"]))


if __name__ == "__main__":
    # The session cookie is Secure by default: browsers accept it on
    # http://localhost, use HTTPS anywhere else.
    app.run(port=9999, dev=True)
