#!/usr/bin/env python3
"""Fail when requires-python is wider than tested/published CPython GIL wheels.

Expected GIL minors = non-EOL, final CPython cycles that satisfy
``python-shim`` ``requires-python``. Actual minors are parsed from CI
matrices, CIBW_BUILD, publish workflows, and PyPI classifiers.

Free-threaded tags (``cp314t`` / ``3.14t``) are ignored: they are an
alternate ABI, not a new minor. Pass ``--require-freethreaded 3.14`` to
assert a TestPyPI/CI ``3.14t`` locus exists.

Network: https://endoflife.date/api/python.json. If unreachable, fall back
to ``scripts/python_support_snapshot.json`` (warn; do not skip). Refresh
the snapshot in the same change that adds a new CPython minor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "scripts" / "python_support_snapshot.json"
EOL_URL = "https://endoflife.date/api/python.json"

REQUIRES_RE = re.compile(
    r"^requires-python\s*=\s*\"([^\"]+)\"", re.MULTILINE | re.IGNORECASE
)
CLAUSE_RE = re.compile(r"^(>=|<=|>|<|==)\s*(\d+)\.(\d+)(?:\.\d+)?$")
LIST_MATRIX_RE = re.compile(r"python-version:\s*\[([^\]]+)\]")
SCALAR_VERSION_RE = re.compile(r'python-version:\s*"(\d+\.\d+)(t)?"')
CIBW_RE = re.compile(r'CIBW_BUILD:\s*"([^"]+)"')
CIBW_TAG_RE = re.compile(r"cp(\d+)t?")
CLASSIFIER_RE = re.compile(
    r"Programming Language :: Python :: (3\.\d+)",
)
FT_LOCUS_RE = re.compile(r"(?:python-version:\s*\"3\.(\d+)t\"|cp3(\d+)t)")


def _today() -> date:
    return datetime.now(UTC).date()


def parse_requires_python(text: str) -> str:
    match = REQUIRES_RE.search(text)
    if match is None:
        raise ValueError("requires-python not found")
    return match.group(1).strip()


def _parse_clause(raw: str) -> tuple[str, tuple[int, int]]:
    clause = raw.strip()
    match = CLAUSE_RE.match(clause)
    if match is None:
        raise ValueError(f"unsupported requires-python clause: {clause!r}")
    op, major, minor = match.group(1), int(match.group(2)), int(match.group(3))
    return op, (major, minor)


def version_matches_spec(minor: str, spec: str) -> bool:
    parts = minor.split(".")
    ver = (int(parts[0]), int(parts[1]))
    for raw in spec.split(","):
        op, bound = _parse_clause(raw)
        if op == ">=" and not ver >= bound:
            return False
        if op == ">" and not ver > bound:
            return False
        if op == "<=" and not ver <= bound:
            return False
        if op == "<" and not ver < bound:
            return False
        if op == "==" and ver != bound:
            return False
    return True


def _is_prerelease(latest: str) -> bool:
    return bool(re.search(r"(?:a|b|rc)\d*$", latest.strip(), re.IGNORECASE))


def _eol_passed(eol: object, today: date) -> bool:
    if isinstance(eol, bool):
        return eol
    if not isinstance(eol, str) or not eol:
        return False
    try:
        eol_date = date.fromisoformat(eol[:10])
    except ValueError:
        return False
    return eol_date <= today


def expected_minors_from_cycles(
    cycles: list[dict[str, object]],
    spec: str,
    today: date | None = None,
) -> list[str]:
    today = today or _today()
    found: list[str] = []
    for row in cycles:
        cycle = str(row.get("cycle") or "")
        if not re.fullmatch(r"3\.\d+", cycle):
            continue
        latest = str(row.get("latest") or "")
        if _is_prerelease(latest):
            continue
        if _eol_passed(row.get("eol"), today):
            continue
        if not version_matches_spec(cycle, spec):
            continue
        found.append(cycle)
    return sorted(found, key=lambda v: tuple(int(p) for p in v.split(".")))


def load_snapshot(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cycles = payload.get("cycles")
    if not isinstance(cycles, list):
        raise ValueError(f"snapshot missing cycles: {path}")
    return [row for row in cycles if isinstance(row, dict)]


def fetch_eol_cycles(
    url: str = EOL_URL, timeout: float = 15.0
) -> list[dict[str, object]]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "ironflow-python-support-matrix"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    if not isinstance(payload, list):
        raise ValueError("endoflife.date payload is not a list")
    return [row for row in payload if isinstance(row, dict)]


def parse_python_version_lists(text: str) -> list[set[str]]:
    out: list[set[str]] = []
    for match in LIST_MATRIX_RE.finditer(text):
        items = re.findall(r"(\d+\.\d+)(t)?", match.group(1))
        gil = {ver for ver, tflag in items if not tflag}
        if gil:
            out.append(gil)
    return out


def parse_publish_gil_scalars(text: str) -> set[str]:
    found: set[str] = set()
    for match in SCALAR_VERSION_RE.finditer(text):
        if match.group(2):
            continue
        found.add(match.group(1))
    return found


def parse_cibw_gil_minors(text: str) -> list[set[str]]:
    out: list[set[str]] = []
    for match in CIBW_RE.finditer(text):
        tags: set[str] = set()
        for raw in match.group(1).split():
            if "t" in raw.split("-")[0]:
                continue
            tag = CIBW_TAG_RE.search(raw)
            if tag is None:
                continue
            digits = tag.group(1)
            if len(digits) < 3:
                continue
            tags.add(f"3.{digits[1:]}" if digits.startswith("3") else digits)
        if tags:
            out.append(tags)
    return out


def parse_classifiers(text: str) -> set[str]:
    return set(CLASSIFIER_RE.findall(text))


def has_freethreaded_locus(text: str, minor: str) -> bool:
    series = minor.split(".")[-1]
    for match in FT_LOCUS_RE.finditer(text):
        found = match.group(1) or match.group(2)
        if found == series:
            return True
    return False


def _pyproject_requires(path: Path) -> str:
    return parse_requires_python(path.read_text(encoding="utf-8"))


def check_repo(
    root: Path,
    *,
    expected: list[str],
    require_freethreaded: str | None = None,
) -> list[str]:
    errors: list[str] = []
    expected_set = set(expected)
    shim = root / "python-shim" / "pyproject.toml"
    static = root / "static-planner" / "pyproject.toml"
    workspace = root / "pyproject.toml"
    ci = root / ".github" / "workflows" / "ci.yml"
    pypi = root / ".github" / "workflows" / "publish-pypi.yml"
    testpypi = root / ".github" / "workflows" / "publish-testpypi.yml"

    shim_spec = _pyproject_requires(shim)
    for label, path in (
        ("static-planner/pyproject.toml", static),
        ("pyproject.toml", workspace),
    ):
        other = _pyproject_requires(path)
        if other != shim_spec:
            errors.append(
                f"{label} requires-python={other!r} does not match "
                f"python-shim ({shim_spec!r})"
            )

    classifiers = parse_classifiers(shim.read_text(encoding="utf-8"))
    missing_cls = sorted(expected_set - classifiers)
    if missing_cls:
        errors.append(
            "python-shim/pyproject.toml classifiers missing "
            + ", ".join(f"Programming Language :: Python :: {v}" for v in missing_cls)
        )

    ci_text = ci.read_text(encoding="utf-8")
    for idx, matrix in enumerate(parse_python_version_lists(ci_text), start=1):
        if matrix != expected_set:
            errors.append(
                f".github/workflows/ci.yml python-version list #{idx} is "
                f"{sorted(matrix)} but expected {expected} "
                "(python-rust / wheel-linux / wheel-windows / wheel-macos)"
            )

    for workflow, text in (
        (".github/workflows/ci.yml", ci_text),
        (".github/workflows/publish-pypi.yml", pypi.read_text(encoding="utf-8")),
        (
            ".github/workflows/publish-testpypi.yml",
            testpypi.read_text(encoding="utf-8"),
        ),
    ):
        for idx, tags in enumerate(parse_cibw_gil_minors(text), start=1):
            if tags != expected_set:
                errors.append(
                    f"{workflow} CIBW_BUILD #{idx} is {sorted(tags)} but expected {expected}"
                )

    for label, path in (
        ("publish-pypi.yml", pypi),
        ("publish-testpypi.yml", testpypi),
    ):
        scalars = parse_publish_gil_scalars(path.read_text(encoding="utf-8"))
        if scalars != expected_set:
            errors.append(
                f".github/workflows/{label} python-version scalars are "
                f"{sorted(scalars)} but expected {expected}"
            )

    if require_freethreaded:
        combined = "\n".join(
            (
                ci_text,
                pypi.read_text(encoding="utf-8"),
                testpypi.read_text(encoding="utf-8"),
            )
        )
        if not has_freethreaded_locus(combined, require_freethreaded):
            errors.append(
                f"missing free-threaded CI/TestPyPI locus for CPython "
                f"{require_freethreaded}t (cp3{require_freethreaded.split('.')[-1]}t)"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip endoflife.date; use the committed snapshot only.",
    )
    parser.add_argument(
        "--require-freethreaded",
        default="",
        help="Optional minor (e.g. 3.14) that must have a 3.XXt / cpXXt locus.",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    snapshot_path = args.snapshot.resolve()
    if not snapshot_path.is_file():
        snapshot_path = (root / args.snapshot).resolve()

    shim_spec = _pyproject_requires(root / "python-shim" / "pyproject.toml")
    snapshot_cycles = load_snapshot(snapshot_path)
    snapshot_expected = expected_minors_from_cycles(snapshot_cycles, shim_spec)

    expected = snapshot_expected
    used_network = False
    if not args.offline:
        try:
            remote = fetch_eol_cycles()
            expected = expected_minors_from_cycles(remote, shim_spec)
            used_network = True
            if expected != snapshot_expected:
                print(
                    "scripts/python_support_snapshot.json is stale: "
                    f"snapshot {snapshot_expected} vs endoflife.date {expected}. "
                    "Refresh the snapshot in the same change that updates wheels.",
                    file=sys.stderr,
                )
                return 1
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            print(
                f"warning: endoflife.date unreachable ({exc!s}); "
                f"using snapshot {snapshot_path}",
                file=sys.stderr,
            )

    if not expected:
        print(
            "no expected CPython minors (check requires-python / snapshot)",
            file=sys.stderr,
        )
        return 1

    source = "endoflife.date" if used_network else "snapshot (offline)"
    print(f"requires-python={shim_spec!r} expected GIL CPython ({source}): {expected}")

    ft = args.require_freethreaded.strip() or None
    errors = check_repo(root, expected=expected, require_freethreaded=ft)
    if errors:
        print("Python support matrix drift:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "Add the missing CPython minor to CI wheel/test matrices, "
            "CIBW_BUILD, publish workflows, and PyPI classifiers.",
            file=sys.stderr,
        )
        return 1
    print("Python support matrix matches requires-python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
