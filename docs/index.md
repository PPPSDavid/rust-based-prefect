# Project IronFlow

**IronFlow** is a **Rust-first** orchestration system with **Prefect-style** Python authoring (`prefect_compat`): you write `@flow` / `@task` flows in Python; the **control plane and durable history** live in the Rust engine. An optional web UI helps you inspect runs.

If you are coming from Prefect 3.x, start with **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)**. For supported features and limits, see **[Compatibility](compatibility.md)**.

## Get started

1. **Install from a release** — Clone the [repository](https://github.com/PPPSDavid/rust-based-prefect) and check out a [release tag](https://github.com/PPPSDavid/rust-based-prefect/releases), then follow the **Quickstart** in the root **README** (conda or `pip` + `cargo build` for the Rust engine). That README is the single source for commands and paths.
2. **Understand the stack** — Read **[Architecture](architecture.md)** for how Python calls into the Rust kernel.
3. **Performance expectations** — See **[Performance (vs Prefect)](PERFORMANCE_OVERVIEW.md)** for what “faster” means here (control-plane throughput; not always faster end-to-end jobs).

## Optional

- **Try the web UI** — After the API is running, use **[Optional: verify the web UI](ui_e2e_visual_check.md)** as a quick visual check.

## Learn more

- **Prefect (upstream)** — [Prefect 3 — get started](https://docs.prefect.io/v3/get-started) · [prefecthq/prefect](https://github.com/prefecthq/prefect)
