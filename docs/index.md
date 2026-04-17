# Project IronFlow

**IronFlow** is a hybrid prototype: a **Rust** orchestration kernel with a **Prefect-style** Python authoring layer (`prefect_compat`). It targets developers who want deterministic control-plane behavior, local-first persistence, and optional HTTP/UI for observability.

## Where to go next

- **Prefect OSS (reference):** [Prefect 3 — get started](https://docs.prefect.io/v3/get-started) · [prefecthq/prefect on GitHub](https://github.com/prefecthq/prefect)
- **[Prefect concepts → IronFlow](PREFECT_IRONFLOW_MAPPING.md)** — map Prefect mental models to this codebase.
- **[Compatibility](compatibility.md)** — supported `@flow` / `@task` subset and boundaries (`COMPATIBILITY.md` in-repo).
- **[Architecture](architecture.md)** — runtime vs static planner paths.
- **Repository README** (repo root) — quickstart, environment setup, and server commands.

## Hosted docs note

This site is generated with [MkDocs](https://www.mkdocs.org/) Material from the `docs/` folder. After you fork, set `site_url` (and optionally `repo_url`) in `mkdocs.yml` so search and “edit” links resolve correctly on GitHub Pages.
