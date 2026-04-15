from __future__ import annotations

import json
from pathlib import Path

from static_planner import compile_and_forecast


def main() -> None:
    sample = """
a = ingest.submit("s3://bucket/path")
b = transform.submit(a)
c = enrich.map([b, b, b])
d = sink.submit(c)
"""
    output = compile_and_forecast(sample, flow_name="sample_forecast")
    out_path = Path("docs") / "sample_forecast.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
