import os
import uuid

from flask import Flask, make_response, render_template, request
from flagsmith import Flagsmith

app = Flask(__name__)
flagsmith = Flagsmith(environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"], enable_events=True)


@app.get("/")
def home():
    visitor = str(uuid.uuid4())
    cta = flagsmith.get_experiment_flag("signup_cta", identifier=visitor)
    resp = make_response(render_template("index.html", cta=cta.value))
    if "session" not in request.cookies:
        resp.set_cookie("session", uuid.uuid4().hex)
    return resp


@app.post("/signup")
def signup():
    flagsmith.track_event("Signup", identifier=request.cookies.get("session"))
    return render_template("thanks.html")


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5055)), threaded=True)
