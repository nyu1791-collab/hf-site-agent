# Live probe plan

`scripts/live_probe_plan.py` produces the redacted preflight required before
any manual Provider or Modal validation. It never opens a network connection,
reads a secret, starts a compute job, changes a registry, or enables routing.

Each plan includes the provider, unresolved model source, bounded request and
token estimate, timeout, retry count, approval token, and estimated cost. Cost
is intentionally `UNKNOWN` until current provider/account evidence is supplied;
therefore `auto_execution_allowed` and every `LIVE_PROBE_*` flag remain false.

Example:

```sh
python scripts/live_probe_plan.py \
  --capabilities \
  --missions \
  --max-candidates 1 \
  --max-missions 2 \
  --max-total-requests 24 \
  --output artifacts/live_probe_plan.json
```

The generated JSON is validated by
`schemas/live_probe_plan.schema.json`. A plan is not a Provider readiness
report and does not select a model. Exact model IDs must come from the current
Provider catalog during a separately approved manual probe.
