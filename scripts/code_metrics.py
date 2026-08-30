#!/usr/bin/env python3
"""File-LOC ratchet for IronFlow production sources.

Neither ruff nor clippy enforce *file* size well. This script compares the
current tree against a checked-in baseline:

- New files (not in the baseline) must be <= new_file_cap (800).
- Existing files not on the >hard_cap allowlist must stay <= hard_cap (1000).
- Allowlisted files (historically over hard_cap) must not grow past their
  recorded LOC. Splits must lower the recorded value (or drop the path).

Total-repo LOC is informational only and is not a merge gate.

Usage:
  python scripts/code_metrics.py              # check (CI)
  python scripts/code_metrics.py --write-baseline
  python scripts/code_metrics.py --json       # dump current {path: loc}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "scripts" / "code_metrics.toml"

TEST_NAME_MARKERS = (".test.", "_test.", ".spec.")
TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})


def _parse_toml(text: str) -> dict[str, Any]:
    """Minimal TOML reader for this config (stdlib only; no nested tables beyond [[roots]])."""
    data: dict[str, Any] = {"roots": []}
    current_root: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "[[roots]]":
            current_root = {}
            data["roots"].append(current_root)
            continue
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            value: Any = [
                item.strip().strip('"').strip("'")
                for item in inner.split(",")
                if item.strip()
            ]
        elif rest.startswith('"') and rest.endswith('"'):
            value = rest[1:-1]
        else:
            value = int(rest) if rest.isdigit() else rest.strip('"')
        if current_root is not None and key in {"path", "suffixes"}:
            current_root[key] = value
        else:
            current_root = None
            data[key] = value
    return data


def _is_test_path(path: Path) -> bool:
    name = path.name
    if any(marker in name for marker in TEST_NAME_MARKERS):
        return True
    return any(part in TEST_DIR_NAMES for part in path.parts)


def _count_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def collect(config: dict[str, Any]) -> dict[str, int]:
    files: dict[str, int] = {}
    for root_spec in config["roots"]:
        root = ROOT / str(root_spec["path"])
        suffixes = tuple(root_spec["suffixes"])
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            rel = path.relative_to(ROOT)
            if _is_test_path(rel):
                continue
            files[rel.as_posix()] = _count_lines(path)
    return files


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"files": {}, "allowlist": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def build_baseline_payload(files: dict[str, int], hard_cap: int) -> dict[str, Any]:
    allowlist = {path: loc for path, loc in files.items() if loc > hard_cap}
    return {
        "files": dict(sorted(files.items())),
        "allowlist": dict(sorted(allowlist.items())),
        "hard_cap": hard_cap,
        "new_file_cap": 800,
    }


def check(
    files: dict[str, int],
    baseline: dict[str, Any],
    *,
    new_file_cap: int,
    hard_cap: int,
) -> list[str]:
    errors: list[str] = []
    known = {str(k): int(v) for k, v in (baseline.get("files") or {}).items()}
    allowlist = {str(k): int(v) for k, v in (baseline.get("allowlist") or {}).items()}

    for path, loc in sorted(files.items()):
        if path in allowlist:
            recorded = allowlist[path]
            if loc > recorded:
                errors.append(
                    f"{path}: allowlisted file grew {recorded} -> {loc} lines "
                    f"(splits must lower the recorded loc)"
                )
            continue
        if path not in known:
            if loc > new_file_cap:
                errors.append(
                    f"{path}: new file has {loc} lines; new production files "
                    f"must be <= {new_file_cap}"
                )
            continue
        if loc > hard_cap:
            errors.append(
                f"{path}: {loc} lines exceeds hard cap {hard_cap} and is not allowlisted"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Rewrite the baseline JSON from the current tree (maintainer-only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print current {path: loc} mapping as JSON.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to code_metrics.toml",
    )
    args = parser.parse_args(argv)

    config = _parse_toml(args.config.read_text(encoding="utf-8"))
    new_file_cap = int(config.get("new_file_cap", 800))
    hard_cap = int(config.get("hard_cap", 1000))
    baseline_rel = str(config.get("baseline", "scripts/metrics/baseline.json"))
    baseline_path = ROOT / baseline_rel

    files = collect(config)
    if args.json:
        json.dump(files, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if args.write_baseline:
        payload = build_baseline_payload(files, hard_cap)
        payload["new_file_cap"] = new_file_cap
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        allow_n = len(payload["allowlist"])
        print(
            f"wrote {baseline_path.relative_to(ROOT)} "
            f"({len(files)} files, {allow_n} allowlisted over {hard_cap})"
        )
        return 0

    baseline = load_baseline(baseline_path)
    if not baseline.get("files"):
        print(
            f"missing baseline at {baseline_path}; run with --write-baseline",
            file=sys.stderr,
        )
        return 1

    errors = check(files, baseline, new_file_cap=new_file_cap, hard_cap=hard_cap)
    over_hard = sorted((p, n) for p, n in files.items() if n > hard_cap)
    print(
        f"code_metrics: {len(files)} production files, "
        f"{len(over_hard)} over {hard_cap}-line hard cap"
    )
    for path, loc in over_hard:
        print(f"  {loc:5d}  {path}")
    if errors:
        print("code_metrics FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
