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
4. Commit with a conventional message, for example: `chore: release 0.2.0`.

## Tag and GitHub Release

Use **annotated** tags with a `v` prefix matching `VERSION`:

```bash
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

The `release` workflow checks that `vX.Y.Z` (without `v`) equals `VERSION` and creates a GitHub Release with auto-generated notes.

If you fork, replace repository URLs in `CHANGELOG.md` with your GitHub coordinates.

## Using a release (downstream)

Consumers should take artifacts from [**GitHub Releases**](https://github.com/PPPSDavid/rust-based-prefect/releases), not from unlabeled `main` snapshots, when they need a reproducible version.

1. **Full stack:** clone the repository and `git checkout vX.Y.Z`, then use `environment.yml` / `requirements-ci.txt` and run from the repo root (Rust, benchmarks, `scripts/`, optional UI) as in the root `README.md`.
2. **Python packages only:** install with pip from git, for example:
   - `pip install "git+https://github.com/PPPSDavid/rust-based-prefect.git@vX.Y.Z#subdirectory=python-shim"`
   - optional: `#subdirectory=static-planner` for `ironflow-static-planner`.
3. **Documentation:** the public MkDocs site tracks **`main`**. To read docs that match a specific tag exactly, browse the repo on GitHub at that tag, or checkout the tag and run `mkdocs serve` per the README.

## Documentation site (GitHub Pages)

After enabling **GitHub Pages** from **GitHub Actions** in the repository settings:

1. Set `site_url` in `mkdocs.yml` to `https://<org>.github.io/<repo>/` (trailing slash recommended).
2. Optionally set `repo_url` to the same repository for the “view source” link in the theme.

The **Documentation** workflow builds on every push to `main` that touches docs or `COMPATIBILITY.md`.
