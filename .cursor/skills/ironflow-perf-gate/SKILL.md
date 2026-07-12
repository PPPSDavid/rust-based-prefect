---
name: ironflow-perf-gate
description: Run and interpret IronFlow perf_matrix.py gates. Use before claiming no regression, after engine/shim control-plane changes, or when comparing baseline vs candidate JSON.
---

# IronFlow — perf_matrix gate

Read root `AGENTS.md` section **Performance: perf_matrix.py** and `docs/perf_methodology.md`.

## Fast local gate

```bash
python3 benchmarks/perf_matrix.py run --preset lite --repetitions 1 --warmups 0 --jobs 2 \
  --out-json /tmp/perf_candidate.json --out-md /tmp/perf_candidate.md
```

Default out paths overwrite tracked `docs/perf_matrix_*.json/md` — prefer `/tmp` unless updating baselines on purpose.

## Compare

```bash
python3 benchmarks/perf_matrix.py compare --baseline <baseline.json> --candidate <candidate.json>
```

Exit codes: `0` ok, `2` regression, `3` mode mismatch, `1` bad input.

**Never** pass `docs/perf_comparison.json` (Prefect A/B array) into `compare`.
