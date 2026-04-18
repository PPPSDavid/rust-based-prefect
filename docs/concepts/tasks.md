# Tasks

A **task** is a Python callable decorated with **`@task`** from **`prefect_compat`**. Each scheduled execution becomes a **task run** with its own state machine in the Rust engine, tied to the parent **flow run**.

## Basics

- **`task.submit(*args, wait_for=...)`** — schedule work and get a **future**; use **`future.result()`** or **`wait([...])`** to block inside the flow function.
- **`task.map(values, wait_for=...)`** — fan out over inputs; returns a list of futures. Combine with **`wait(mapped)`** before downstream **`submit`** calls.
- Imports and patterns match the subset described in **[Compatibility matrix](../compatibility.md)** and the **[Quick start (demo flow)](../QUICKSTART_DEMO.md)** example.

## Transition hooks

Tasks accept the same **`transition_hooks`** mechanism as flows: **`TransitionHookSpec`** + **`on_transition`**, with optional **`from_state`** / **`to_state`** filters. See **[Flows](flows.md)** and the compatibility matrix for behavior and differences from Prefect’s hook names.
