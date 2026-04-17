# Project IronFlow

**IronFlow** centers on a **Rust orchestration kernel** (`rust-engine`): the deterministic control plane, event history, and native FFI surface are implemented there first. **`prefect_compat`** (Python) provides Prefect-style `@flow` / `@task` authoring and runtime glue — including optional HTTP — on top of that kernel. The optional UI is observability only. Read [Architecture](architecture.md) for the full runtime path.

## Where to go next

- **Releases:** install or checkout a [numbered release](https://github.com/PPPSDavid/rust-based-prefect/releases) (for example `v0.1.1`) — see the root **README** section *Using a numbered release*. This site is built from **`main`**; for docs frozen to a tag, browse the repository on GitHub at that tag or run MkDocs from a local checkout.
- **Prefect OSS (reference):** [Prefect 3 — get started](https://docs.prefect.io/v3/get-started) · [prefecthq/prefect on GitHub](https://github.com/prefecthq/prefect)
- **[Prefect concepts → IronFlow](PREFECT_IRONFLOW_MAPPING.md)** — map Prefect mental models to this codebase.
- **[Compatibility](compatibility.md)** — supported `@flow` / `@task` subset and boundaries (`COMPATIBILITY.md` in-repo).
- **[Architecture](architecture.md)** — runtime vs static planner paths.
- **Repository README** (repo root) — quickstart, environment setup, and server commands.

## Hosted docs note

This site is generated with [MkDocs](https://www.mkdocs.org/) Material from the `docs/` folder. After you fork, set `site_url` (and optionally `repo_url`) in `mkdocs.yml` so search and “edit” links resolve correctly on GitHub Pages.
