---
name: preflight
description: Take a Flagsmith experiment from a one-line idea to production with a human yes before every change. Use when someone asks to A/B test, experiment on, or split-test something with Flagsmith, to check an experiment's wiring, to read or call an experiment's result, to ship a winner, or to clean up a finished experiment. Drives the `preflight` CLI.
compatibility: Needs the Flagsmith CLI v2 (logged in with `flagsmith login`), Python 3.10+ with `pip install git+https://github.com/Adarsh-Polsey/flagsmith-preflight`, and network access to Flagsmith.
metadata:
  version: "1.0"
---

# Preflight

You run a Flagsmith experiment through seven stages. The `preflight` CLI holds the gates; you write the proposals and fixes. You never change code or Flagsmith without a yes from the human for that exact change.

## The approval rule

Before every change (code edit, Flagsmith write, start, ship, archive):
1. Show exactly what will change: the diff, or the Flagsmith objects.
2. Ask one question that ends in "(yes/no)" and stop. Wait for the reply.
3. Only a reply that clearly means yes counts. Anything else: don't do it, say what you didn't do.
4. On a yes, pass it to the CLI as `--yes --note "<their reply, verbatim>"`. For code edits, first run `preflight log --stage <stage> --action "<what>" --note "<their reply>"`, then edit.

Never pass `--yes` on your own judgment, never batch several changes under one yes, and never edit `.preflight/approvals.jsonl` by hand.

## Stages

Stages 2, 4, 5 and 6 are gates in the CLI. Stages 1, 3 and 7 are your proposals.

### 1. Design (proposal)

From a one-line ask like "test 'Get early access' against 'Join the waitlist' on the signup button":
- Find where the thing is rendered and where success happens in the code. The current version is control.
- Pick one success event (lowercase snake_case, e.g. `signup`). If the code already sends a similar event, say so.
- If the app already sends an event that shouldn't move (e.g. `page_view`), propose it as a guardrail; otherwise say there's none.
- Ask for the baseline conversion rate and the smallest lift worth detecting if you don't know them; otherwise assume 10% and +30% and say it's an assumption.
- Run `preflight estimate --baseline <b> --lift <l>`.
- Check what exists: `flagsmith feature get <flag> --json` and `flagsmith api api/v1/environments/<env>/experiment-metrics/`.
- Write `.preflight/plan.md`:

```markdown
# Plan: <experiment name>
Ask: <the one line>
Hypothesis: <change> will raise <metric> by at least <lift> within <time>.
Flag: <flag> (multivariate) · control <value> · <key> <value>
Success event: <event> (metric "<name>", occurrence, higher is better)
Guardrail: <event> (should not decrease), or none
Split: 50/50 · rollout 100%
Sample: ~<n> users per variation for a <lift> lift on a <baseline> baseline
Flagsmith changes: <what will be created, what is reused>
Not changed yet: the code. `preflight check` runs next.
```

- Show the plan, ask "Create these in Flagsmith? (yes/no)". On yes:
  `preflight init --flag <flag> --baseline <b> --mde <l>` then
  `preflight create --flag <flag> --control "<value>" --variant "<key>=<value>" --event <event> --metric "<name>" --name "<experiment>" --hypothesis "<hypothesis>" [--guardrail-event <event> --guardrail-metric "<name>"] --yes --note "<reply>"`.
  It creates the experiment but never starts it.

### 2. Preflight (gate)

Run `preflight check`. Show its checklist as is. Use `preflight check --json` when you need to parse it.

### 3. Approve (proposal)

For each failure, one at a time:
- One line on why it voids the result (use the check's reason).
- The smallest fix. For code, show a unified diff. Don't refactor, rename or tidy anything else.
- Ask "Apply this fix? (yes/no)". On yes, log it and apply it.

Fixes by check:
- **stable identifier**: read a cookie, session or user id; for anonymous visitors, generate an id once and persist it (cookie or localStorage).
- **identified first**: pass the identifier with the flag read (Python, Node) or call `identify()` before `getExperimentFlag()` (browser).
- **event name**: send the metric's event exactly; it's case-sensitive.
- **same identifier**: send the conversion with the identifier the exposure used.
- **events enabled**: `enable_events=True` (Python) or `enableEvents: true` (JS).
- **events flushed**: `flagsmith.close()` before a script exits, `flush_events()` / `flushEvents()` at the end of a handler.
- **SDK version**: raise the minimum (Python `flagsmith>=6.3.0`, `@flagsmith/nodejs@^9.1.0`, `@flagsmith/flagsmith@^12.4.0`).
- **every variation keyed**: `preflight set-key <variant id> <key> --yes --note "<reply>"` (the id is in the check's reason).
- **warehouse connected**: tell the human what to click; you can't fix it.

After the fixes, run `preflight check` again. Repeat until it's green or only warnings remain; explain each warning.

### 4. Run (gate)

Ask "Start <experiment>? This locks <flag>. (yes/no)". On yes, `preflight start --yes --note "<reply>"`. It refuses while any check fails; if it does, go back to stage 3. If the human is at a terminal, they can run `preflight start` themselves and answer its `[y/N]`.

Optionally, `preflight smoke --url <app url> --convert <path> --visitors 100` sends test visitors and checks both variations get exposures, conversions arrive, and each visitor counts once.

### 5. Evaluate (gate)

Run `preflight readout`. Report its verdict in its own words. Never name a winner the readout didn't name: a high chance to win is not a result while the 95% interval crosses zero. Pass on the interval and the users-needed estimate. If it shows a sample ratio mismatch or a growing number of quarantined identities, say the result can't be trusted and look for the cause.

### 6. Ship (gate)

Only after a readout names a winner. Ask "End <experiment> and serve <winner> to everyone? (yes/no)". On yes, `preflight ship --yes --note "<reply>"`. It ends the experiment (results freeze, the flag unlocks) and serves the winner through Flagsmith, with no redeploy.

### 7. Clean up (proposal)

Once shipped, propose one diff that:
- replaces the experiment flag read with the winning value,
- deletes the losing branch (code and templates),
- deletes plumbing that only existed for the experiment (e.g. a visitor-id cookie), but keeps conversion tracking if anything else uses it.

Show the diff and say the Flagsmith side: archive `<flag>`. Ask "Apply the cleanup and archive <flag>? (yes/no)". On yes, log it, apply the diff, run the app's tests if it has any, then `preflight archive --yes --note "<reply>"`. Archive refuses while code still reads the flag.

## Finish

Show `preflight approvals`: every yes and no, with who, what and when.
