# Project IronFlow

**IronFlow** is a **Rust-first** orchestration system with **Prefect-style** Python authoring (`prefect_compat`): you write `@flow` / `@task` flows in Python; the **control plane and durable history** live in the Rust engine. An optional web UI helps you inspect runs.

If you are coming from Prefect 3.x, start with **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)**. For supported features and limits, see **[Compatibility](compatibility.md)**.

## Get started

1. **[Install IronFlow](INSTALL.md)** — clone the repo, set up Python, build `rust-engine` with Cargo (current supported path; no PyPI wheel yet).
2. **[Quick start (demo flow)](QUICKSTART_DEMO.md)** — run a tiny `@flow` and see expected terminal output (no server required).
3. **[Self-hosted server](SELF_HOSTED_SERVER.md)** — start the optional API (`ironflow_server.py`), workers, deployments, and how scheduling compares to Prefect.
4. **Understand the stack** — Read **[Architecture](architecture.md)** for how Python calls into the Rust kernel.
5. **Performance expectations** — See **[Performance (vs Prefect)](PERFORMANCE_OVERVIEW.md)** for what “faster” means here (control-plane throughput; not always faster end-to-end jobs).

The root **README** in the repository duplicates install commands with scripts, tests, and optional UI — use it when you need the full command reference.

## Optional

- **Try the web UI** — After the API is running, use **[Optional: verify the web UI](ui_e2e_visual_check.md)** as a quick visual check.

## Learn more

- **Prefect (upstream)** — [Prefect 3 — get started](https://docs.prefect.io/v3/get-started) · [prefecthq/prefect](https://github.com/prefecthq/prefect)
