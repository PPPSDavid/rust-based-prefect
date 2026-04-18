# Concepts overview

IronFlow splits work into a **Rust orchestration kernel** (`rust-engine`) and **Prefect-shaped Python authoring** (`prefect_compat`). The kernel owns durable flow/task state and valid transitions; Python runs your `@flow` / `@task` code and calls into the control plane.

Use these pages to learn the IronFlow model end-to-end:

- **[Flows](flows.md)** — `@flow`, flow runs, and transition hooks.
- **[Tasks](tasks.md)** — `@task`, `submit`, `map`, futures.
- **[Runners](runners.md)** — task runners and concurrent `map`.
- **[States and transitions](states-and-transitions.md)** — `RunState` values and allowed edges (enforced in Rust).
- **[Prefect → IronFlow](../PREFECT_IRONFLOW_MAPPING.md)** — table mapping Prefect mental models to this project.
- **[Architecture](../architecture.md)** — how Python calls the engine and where persistence hooks live.

For supported features and limits, see **[Compatibility matrix](../compatibility.md)**. For Prefect’s own tutorials, see the upstream [Prefect 3 concepts](https://docs.prefect.io/v3/concepts) (IronFlow implements a **subset**; compatibility is not universal).
