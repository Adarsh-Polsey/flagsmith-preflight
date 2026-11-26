# Demo fixes

The three code fixes for the demo app, as the agent proposes them in stage 3. The fourth fix is in Flagsmith: `preflight set-key <variation id> early_access`.

```diff
--- a/demo/app.py
+++ b/demo/app.py
@@ -8,11 +8,16 @@
 flagsmith = Flagsmith(environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"], enable_events=True)
 
 
+def visitor_id():
+    return request.cookies.get("visitor_id") or str(uuid.uuid4())
+
+
 @app.get("/")
 def home():
-    visitor = str(uuid.uuid4())
+    visitor = visitor_id()
     cta = flagsmith.get_experiment_flag("signup_cta", identifier=visitor)
     resp = make_response(render_template("index.html", cta=cta.value))
+    resp.set_cookie("visitor_id", visitor, max_age=60 * 60 * 24 * 365)
     if "session" not in request.cookies:
         resp.set_cookie("session", uuid.uuid4().hex)
     return resp
@@ -20,7 +25,7 @@
 
 @app.post("/signup")
 def signup():
-    flagsmith.track_event("Signup", identifier=request.cookies.get("session"))
+    flagsmith.track_event("signup", identifier=visitor_id())
     return render_template("thanks.html")
 
 
```
