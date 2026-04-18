# Project IronFlow

**IronFlow** is a **Rust-first** orchestration system with **Prefect-style** Python authoring (`prefect_compat`): you write `@flow` / `@task` flows in Python; the **control plane and durable history** live in the Rust engine. An optional HTTP API and web UI help you inspect runs.

Choose a path:

| Goal | Where to start |
| --- | --- |
| Install and run a demo flow | **[Get started → Installation](INSTALL.md)** · **[Quick start (demo flow)](QUICKSTART_DEMO.md)** |
| Self-hosted API, workers, deployments | **[Self-hosted server](SELF_HOSTED_SERVER.md)** |
| Understand flows, tasks, runners, and states | **[Concepts overview](concepts/index.md)** |
| Do something specific (setup, server, porting) | **[How-to guides](how-to/index.md)** |
| Supported features vs Prefect | **[Compatibility matrix](compatibility.md)** |
| Performance expectations | **[Performance (vs Prefect)](PERFORMANCE_OVERVIEW.md)** |

Step-by-step onboarding:

1. **[Install IronFlow](INSTALL.md)** — clone the repo, set up Python, build `rust-engine` with Cargo (current supported path; no PyPI wheel yet).
2. **[Quick start (demo flow)](QUICKSTART_DEMO.md)** — run a tiny `@flow` and see expected terminal output (no server required).
3. **[Self-hosted server](SELF_HOSTED_SERVER.md)** — optional API (`ironflow_server.py`), workers, deployments, and how scheduling compares to Prefect.
4. **Understand the stack** — read **[Architecture](architecture.md)** or **[Concepts overview](concepts/index.md)** for how Python calls into the Rust kernel.
5. **Performance expectations** — see **[Performance (vs Prefect)](PERFORMANCE_OVERVIEW.md)** for what “faster” means here (control-plane throughput; not always faster end-to-end jobs).

The repository **README** at the repo root has the full command reference (tests, benchmarks, optional UI).

## Prefect (upstream)

IronFlow echoes Prefect 3.x patterns but implements a **subset** with different internals. For the upstream mental model (flows, tasks, deployments in Prefect’s world), use the official docs:

- [Prefect 3 — Get started](https://docs.prefect.io/v3/get-started)
- [Prefect 3 — Concepts](https://docs.prefect.io/v3/concepts)
- [Prefect 3 — How-to guides](https://docs.prefect.io/v3/how-to-guides)

Read Prefect for general orchestration ideas; read **[Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md)** and **[Compatibility](compatibility.md)** for what this repository actually implements.
