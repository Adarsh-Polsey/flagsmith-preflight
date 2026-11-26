# Plan: Signup CTA: Get early access vs Join the waitlist
Ask: test 'Get early access' against 'Join the waitlist' on the signup button
Hypothesis: Changing the signup button from "Join the waitlist" to "Get early access" will raise signups by at least 30% within 2 weeks.
Flag: signup_cta (multivariate) · control "Join the waitlist" · early_access "Get early access"
Success event: signup (metric "Signups", occurrence, higher is better)
Split: 50/50 · rollout 100%
Sample: ~1,800 users per variation for a +30% lift on a 10% baseline
Flagsmith changes: reuse flag signup_cta and its existing "Get early access" variation (id 36975); create metric "Signups" on event signup; create experiment "Signup CTA: Get early access vs Join the waitlist", split evenly, not started
Not changed yet: the code. `preflight check` runs next.
