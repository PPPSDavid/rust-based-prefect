#!/usr/bin/env python3
"""Cross-platform launcher for the code-review-graph MCP server.

Cursor Cloud / Linux / macOS: ``python3 -m code_review_graph serve``.
Windows desktop (optional): if ``CRG_MCP_USE_CONDA=1`` or the named conda
env exists, launch via ``conda run`` so GPU + ``[embeddings]`` still work
(see ``crg_mcp_serve.ps1`` for the historical PowerShell path).

Env knobs:
  CRG_MCP_USE_CONDA=1     Opt into conda launch (Windows desktop embeddings)
  CRG_MCP_CONDA_ENV       Required when USE_CONDA=1 (no silent personal default)
  CRG_APPLY_ST_CACHE_PATCH  Apply optional SentenceTransformer cache patch
  CRG_EMBEDDING_MODEL     Passed through when using embeddings
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _maybe_apply_st_cache_patch() -> None:
    """Load the optional ST cache patch when embeddings are available."""
    if os.environ.get("CRG_APPLY_ST_CACHE_PATCH", "").strip() != "1":
        return
    patch = Path(__file__).resolve().parent / "crg_st_model_cache.py"
    if not patch.is_file():
        return
    # Import as a side-effect module (applies patch on import when env is set).
    sys.path.insert(0, str(patch.parent))
    try:
        import crg_st_model_cache  # noqa: F401
    except Exception:
        return


def _find_conda() -> str | None:
    conda = shutil.which("conda") or shutil.which("conda.exe")
    if conda:
        return conda
    for key in ("CONDA_EXE",):
        val = os.environ.get(key)
        if val and Path(val).is_file():
            return val
    home = Path.home()
    candidates = [
        home / "miniconda3" / "Scripts" / "conda.exe",
        home / "miniconda3" / "bin" / "conda",
        home / "anaconda3" / "Scripts" / "conda.exe",
        home / "anaconda3" / "bin" / "conda",
        home / "mambaforge" / "Scripts" / "conda.exe",
        home / "mambaforge" / "bin" / "conda",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _conda_env_exists(conda: str, env_name: str) -> bool:
    try:
        proc = subprocess.run(
            [conda, "env", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0 or not proc.stdout:
        return False
    try:
        import json

        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    envs = data.get("envs") or []
    needle = os.sep + env_name
    return any(str(p).rstrip("\\/").endswith(needle) or str(p).endswith(env_name) for p in envs)


def _should_use_conda() -> tuple[bool, str | None, str]:
    """Conda path is opt-in only — never invent a personal env name."""
    force = os.environ.get("CRG_MCP_USE_CONDA", "").strip() == "1"
    env_name = (os.environ.get("CRG_MCP_CONDA_ENV") or "").strip()
    if not force:
        return False, None, env_name
    if not env_name:
        sys.stderr.write(
            "CRG_MCP_USE_CONDA=1 requires CRG_MCP_CONDA_ENV=<conda-env-name>\n"
            "Falling back to python -m code_review_graph serve.\n"
        )
        return False, None, env_name
    conda = _find_conda()
    if not conda:
        sys.stderr.write("CRG_MCP_USE_CONDA=1 but conda was not found; using module serve.\n")
        return False, None, env_name
    if not _conda_env_exists(conda, env_name):
        sys.stderr.write(
            f"CRG_MCP_USE_CONDA=1 but conda env {env_name!r} was not found; using module serve.\n"
        )
        return False, conda, env_name
    return True, conda, env_name


def _exec_conda_serve(conda: str, env_name: str) -> int:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    cmd = [
        conda,
        "run",
        "-n",
        env_name,
        "--no-capture-output",
        "python",
        "-m",
        "code_review_graph",
        "serve",
    ]
    return subprocess.call(cmd, cwd=str(_repo_root()))


def _exec_module_serve() -> int:
    _maybe_apply_st_cache_patch()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # Prefer in-process so MCP stdio stays on this process.
    try:
        from code_review_graph.main import main as serve_main
    except ImportError as exc:
        sys.stderr.write(
            "code-review-graph is not installed for this interpreter.\n"
            "Install: python3 -m pip install --user 'code-review-graph>=2.3.6,<3'\n"
            "Or run:  bash scripts/setup_code_review_graph.sh\n"
            f"Detail: {exc}\n"
        )
        return 1
    serve_main()
    return 0


def main() -> int:
    use_conda, conda, env_name = _should_use_conda()
    if use_conda and conda:
        return _exec_conda_serve(conda, env_name)
    return _exec_module_serve()


if __name__ == "__main__":
    raise SystemExit(main())
