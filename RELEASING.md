# Releasing

IronFlow uses a **single version** shared by:

- `VERSION` (authoritative string)
- `rust-engine/Cargo.toml` → `[package].version`
- `python-shim/pyproject.toml` and `static-planner/pyproject.toml` → `[project].version`
- `frontend/package.json` → `version`

## Before tagging

1. Run validation from `AGENTS.md` (pytest, `cargo test`, optional `perf_matrix` lite run).
2. Update `CHANGELOG.md`: move items from **Unreleased** into a dated section for the new version.
3. Bump `VERSION` and keep the four artifacts above aligned (or run `python scripts/check_version_sync.py`).
4. Commit with a conventional message, for example: `chore: release 0.3.0`.

## Tag and GitHub Release

Use **annotated** tags with a `v` prefix matching `VERSION`:

```bash
git tag -a v0.3.0 -m "Release v0.3.0"
git push origin v0.3.0
```

The `release` workflow checks that `vX.Y.Z` (without `v`) equals `VERSION` and creates a GitHub Release with auto-generated notes.

If you fork, replace repository URLs in `CHANGELOG.md` with your GitHub coordinates.

## PyPI (production)

The **`ironflow-prefect-compat`** package is published to **https://pypi.org** via GitHub Actions (**workflow_dispatch**).

1. **One-time:** On **PyPI**, open the project → **Manage** → **Publishing** → add a **trusted publisher** for this repository and workflow **`.github/workflows/publish-pypi.yml`** (same pattern as [TestPyPI trusted publishers](https://docs.pypi.org/trusted-publishers/), but against **pypi.org**). TestPyPI and PyPI trusted publishers are configured **separately**.
2. **Before uploading:** Bump **`VERSION`** and sync versions across `VERSION`, `rust-engine/Cargo.toml`, **`python-shim/pyproject.toml`**, `static-planner/pyproject.toml`, and `frontend/package.json` (`python scripts/check_version_sync.py`). Run validation from **`AGENTS.md`**. Optionally publish to **TestPyPI** first using **`Publish to TestPyPI`**.
3. **Upload:** **Actions** → **Publish to PyPI** → **Run workflow**. Use **`dry_run`** to build wheels and download artifacts without uploading.
4. **Install:** `python -m pip install ironflow-prefect-compat` (see **`docs/INSTALL.md`**).

The **TestPyPI** workflow (`.github/workflows/publish-testpypi.yml`) is unchanged and remains the recommended validation index before production uploads.

## Container images (server)

The **`ironflow-server`** image is a **runtime wrapper** around the same PyPI wheel — not a separate artifact line.

| Channel | Artifact | When to use |
| --- | --- | --- |
| PyPI | `ironflow-prefect-compat` | Libraries, CLI, custom processes |
| GHCR (proposed) | `ghcr.io/pppsdavid/ironflow-server:<VERSION>` | Ready-to-run API (`uvicorn` + defaults) |

**Release order:** publish the PyPI wheel first, then build/push the server image with the matching `IRONFLOW_VERSION` build arg (see **`deploy/docker/README.md`**). CI smoke: `.github/workflows/docker-server-smoke.yml`. Automated GHCR push workflow to follow.

Attach `docker pull ghcr.io/pppsdavid/ironflow-server:vX.Y.Z` to GitHub Release notes when images are published.

## Using a release (downstream)

Consumers should take artifacts from [**GitHub Releases**](https://github.com/PPPSDavid/rust-based-prefect/releases), not from unlabeled `main` snapshots, when they need a reproducible version.

1. **Full stack:** clone the repository and `git checkout vX.Y.Z`, then use **`uv sync --group dev`** (preferred) or `environment.yml` / `requirements-ci.txt`, and run from the repo root — including **`rust-engine`** (`cargo build`), benchmarks, `scripts/`, and optional UI as in **`CONTRIBUTING.md`**.
2. **Python packages only:** install from **PyPI** when published (`pip install ironflow-prefect-compat`), or from git, for example:
   - `pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@vX.Y.Z#subdirectory=python-shim"`
   - optional: `#subdirectory=static-planner` for `ironflow-static-planner`.
3. **Documentation:** the public MkDocs site tracks **`main`**. To read docs that match a specific tag exactly, browse the repo on GitHub at that tag, or checkout the tag and run `mkdocs serve` per the README.

## Documentation site (GitHub Pages)

After enabling **GitHub Pages** from **GitHub Actions** in the repository settings:

1. Set `site_url` in `mkdocs.yml` to `https://<org>.github.io/<repo>/` (trailing slash recommended).
2. Optionally set `repo_url` to the same repository for the “view source” link in the theme.

The **Documentation** workflow builds on every push to `main` that touches docs or `COMPATIBILITY.md`.
