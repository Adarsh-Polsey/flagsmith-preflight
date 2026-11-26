# Demo cleanup

The stage 7 diff the agent proposed after the winner shipped, applied on a yes. It removes the experiment read and the losing branch, keeps conversion tracking, and then `preflight archive` archived `signup_cta` (it refuses while code still reads the flag).

```diff
--- a/demo/app.py
+++ b/demo/app.py
@@ -11,8 +11,7 @@
 @app.get("/")
 def home():
     visitor = request.cookies.get("session") or uuid.uuid4().hex
-    cta = flagsmith.get_experiment_flag("signup_cta", identifier=visitor)
-    resp = make_response(render_template("index.html", cta=cta.value))
+    resp = make_response(render_template("index.html"))
     if "session" not in request.cookies:
         resp.set_cookie("session", visitor, max_age=60 * 60 * 24 * 365)
     return resp
--- a/demo/templates/index.html
+++ b/demo/templates/index.html
@@ -21,13 +21,9 @@
     <p>Notes that link themselves. Launching in January.</p>
     <form method="post" action="/signup">
       <input type="email" name="email" placeholder="you@example.com" aria-label="Email">
-      <button type="submit" id="cta">{{ cta }}</button>
+      <button type="submit" id="cta">Get early access</button>
     </form>
-    {% if cta == "Get early access" %}
     <small class="note">The first 500 people get in before launch.</small>
-    {% else %}
-    <small class="note">We'll email you once, at launch.</small>
-    {% endif %}
     <small>Demo app for Preflight. No emails are stored.</small>
   </main>
 </body>
```
