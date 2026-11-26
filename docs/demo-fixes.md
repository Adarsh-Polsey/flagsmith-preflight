# Demo fixes

The fixes the agent proposed and applied in the recorded run, one yes each (see [demo-approvals.jsonl](demo-approvals.jsonl)). In Flagsmith: `preflight set-key 36975 early_access`. In code, its smallest fix reused the app's existing `session` cookie as the visitor id, which cleared both the stable-identifier and same-identifier checks, then lower-cased the event:

```diff
--- a/demo/app.py
+++ b/demo/app.py
@@ -10,17 +10,17 @@
 
 @app.get("/")
 def home():
-    visitor = str(uuid.uuid4())
+    visitor = request.cookies.get("session") or uuid.uuid4().hex
     cta = flagsmith.get_experiment_flag("signup_cta", identifier=visitor)
     resp = make_response(render_template("index.html", cta=cta.value))
     if "session" not in request.cookies:
-        resp.set_cookie("session", uuid.uuid4().hex)
+        resp.set_cookie("session", visitor, max_age=60 * 60 * 24 * 365)
     return resp
 
 
 @app.post("/signup")
 def signup():
-    flagsmith.track_event("Signup", identifier=request.cookies.get("session"))
+    flagsmith.track_event("signup", identifier=request.cookies.get("session"))
     return render_template("thanks.html")
 
 
```

If you fix it by hand, any stable id works, as long as the exposure and the conversion use the same one.
