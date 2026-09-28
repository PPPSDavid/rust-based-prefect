# How to create and update deployments

This guide focuses on deployment lifecycle operations in IronFlow's self-hosted API:

- create deployments
- update deployments
- trigger deployment runs
- enable interval, cron, or RRule schedules

It assumes the API is already running (see [How to run the server and UI](server-and-ui.md)).

## 1. List existing deployments

```bash
curl -s http://127.0.0.1:8000/api/deployments | python -m json.tool
```

## 2. Create a deployment

Use `POST /api/deployments` to register a deployment for a known flow.

```bash
curl -s -X POST http://127.0.0.1:8000/api/deployments \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "my-simple",
    "flow_name": "simple_flow",
    "default_parameters": {"n": 4},
    "paused": false
  }' | python -m json.tool
```

Schedule fields are also accepted on create:

- `schedule_enabled` (bool)
- `schedule_interval_seconds` (positive integer)
- `schedule_cron` (cron expression)
- `schedule_rrule` (Rust-preferred subset: `FREQ=MINUTELY|HOURLY|DAILY|WEEKLY`, optional positive `INTERVAL`, optional `UNTIL`; no `COUNT`)
- `schedule_next_run_at` (RFC3339 timestamp)

## 3. Update a deployment

Use `PATCH /api/deployments/{id}` to change deployment settings, including schedules.

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "default_parameters": {"n": 8},
    "paused": false
  }' | python -m json.tool
```

### Schedule update example (cron)

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "schedule_enabled": true,
    "schedule_cron": "*/10 * * * *"
  }' | python -m json.tool
```

### Schedule update example (interval)

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "schedule_enabled": true,
    "schedule_interval_seconds": 300
  }' | python -m json.tool
```

### Schedule update example (RRule)

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "schedule_enabled": true,
    "schedule_rrule": "FREQ=HOURLY;INTERVAL=2"
  }' | python -m json.tool
```

Interval, cron, and RRule are mutually exclusive in deployment state. Setting one schedule type clears the others.

## 4. Trigger a deployment run manually

```bash
curl -s -X POST http://127.0.0.1:8000/api/deployments/DEPLOYMENT_ID/run \
  -H 'Content-Type: application/json' \
  -d '{"parameters": {"n": 2}}' | python -m json.tool
```

## 5. Observe run progress

```bash
curl -s http://127.0.0.1:8000/api/deployment-runs | python -m json.tool
curl -s http://127.0.0.1:8000/api/flow-runs | python -m json.tool
```

With the default embedded worker enabled, deployment runs progress from `SCHEDULED` to execution in the same process.

## View and edit a schedule in the UI

Open **Deployments**. Each row shows the schedule in words and the next run. A deployment with no schedule says **Manual** and links to **Add schedule**.

Open a deployment. The Schedule section shows the same words and the next run. **Add schedule** or **Edit schedule** changes an interval, a cron expression, or the supported RRule subset (`MINUTELY`, `HOURLY`, `DAILY`, `WEEKLY`, optional `INTERVAL`, optional `UNTIL`) and previews that wording before you save.

Cron uses the Rust scheduler's fields: seconds, minutes, hours, day of month, month, day of week, and an optional year. Sunday is `1`. Example: `0 */10 * * * *`. A five-field expression such as `*/10 * * * *` is saved with seconds set to `0`.

If the page says the cron will not run, this server is not ticking cron. The Python scheduler (and Postgres schedule fallback) still runs interval and RRule schedules. The cron expression is stored, and it starts only when the Rust scheduler is active.

## Notes

- Cron and RRule scheduling are Rust-preferred when the native engine is available with DB binding.
- Without that Rust path, cron schedules may require `schedule_next_run_at` to be provided explicitly; simple RRule schedules use the Python fallback.
- RRule support is intentionally limited to simple frequency/interval rules (`MINUTELY`, `HOURLY`, `DAILY`, `WEEKLY`) plus optional `UNTIL`.
- IronFlow currently provides a local subset of deployment/worker behavior, not full Prefect Cloud parity.

See also: [Self-hosted server](../SELF_HOSTED_SERVER.md), [Compatibility matrix](../compatibility.md), and [Prefect concepts -> IronFlow](../PREFECT_IRONFLOW_MAPPING.md).
