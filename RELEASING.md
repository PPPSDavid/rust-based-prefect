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

## Documentation site (GitHub Pages)

After enabling **GitHub Pages** from **GitHub Actions** in the repository settings:

1. Set `site_url` in `mkdocs.yml` to `https://<org>.github.io/<repo>/` (trailing slash recommended).
2. Optionally set `repo_url` to the same repository for the “view source” link in the theme.

The **Documentation** workflow builds on every push to `main` that touches docs or `COMPATIBILITY.md`.
