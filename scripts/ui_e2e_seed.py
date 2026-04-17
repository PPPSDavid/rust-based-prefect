from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed flow/task/log/event data for end-to-end UI visual checks."
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--mapped", type=int, default=2, help="Number of mapped runs to create")
    parser.add_argument("--chained", type=int, default=2, help="Number of chained runs to create")
    parser.add_argument("--failing", type=int, default=1, help="Number of failing runs to create")
    parser.add_argument("--complexity", type=int, default=6, help="Task complexity input")
    args = parser.parse_args()

    health_url = f"{args.api_base}/health"
    run_url = f"{args.api_base}/benchmark/run"
    runs_url = f"{args.api_base}/api/flow-runs?limit=10"

    try:
        health = get_json(health_url)
    except urllib.error.URLError as exc:
        print(f"Unable to reach API at {health_url}: {exc}")
        return 1

    if health.get("status") != "ok":
        print(f"API health check failed: {health}")
        return 1

    created = 0
    for _ in range(args.mapped):
        post_json(run_url, {"flavor": "mapped", "complexity": args.complexity})
        created += 1
    for _ in range(args.chained):
        post_json(run_url, {"flavor": "chained", "complexity": args.complexity})
        created += 1
    for _ in range(args.failing):
        post_json(run_url, {"flavor": "failing", "complexity": args.complexity})
        created += 1

    runs = get_json(runs_url)
    print(f"Created {created} runs.")
    print(f"Visible run count in latest page: {len(runs.get('items', []))}")
    if runs.get("items"):
        latest = runs["items"][0]
        print("Latest run summary:")
        print(
            json.dumps(
                {
                    "id": latest.get("id"),
                    "name": latest.get("name"),
                    "state": latest.get("state"),
                    "updated_at": latest.get("updated_at"),
                },
                indent=2,
            )
        )
    print("Open UI at http://localhost:4173/runs and refresh to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
