# Quick start: PyPI install (no clone)

Run a minimal `@flow` / `@task` example after **`pip install ironflow-prefect-compat`** — no repository clone, no `PYTHONPATH`, and no API server.

**Prerequisites:** Python **3.11–3.14** (see [Installation](INSTALL.md) for wheel platform notes).

## 1. Install

```bash
python -m pip install --upgrade pip
python -m pip install ironflow-prefect-compat
```

Verify the Rust engine loaded (expect `True` on supported wheels):

```bash
python -c "from prefect_compat.rust_bridge import native_library_available; print(native_library_available())"
```

If you see `False`, see [Troubleshooting](reference/troubleshooting.md).

## 2. Save and run a tiny flow

Create `hello_ironflow.py` anywhere on your machine:

```python
from prefect_compat import InMemoryControlPlane, flow, set_control_plane, task, wait


@task
def start(n: int) -> int:
    return n + 1


@task
def process(n: int) -> int:
    return n * 2


@task
def aggregate(values: list[int]) -> int:
    return sum(values)


@flow
def example_flow(total: int = 10) -> int:
    first = start.submit(total)
    mapped = process.map([first.result(), first.result() + 1], wait_for=[first])
    wait(mapped)
    return aggregate.submit([f.result() for f in mapped], wait_for=mapped).result()


def main() -> None:
    plane = InMemoryControlPlane()
    set_control_plane(plane)
    result = example_flow(5)
    print(f"ironflow_result={result}")
    print(f"ironflow_events={len(plane.events())}")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python hello_ironflow.py
```

Typical output:

```text
ironflow_result=26
ironflow_events=15
```

| Line | Meaning |
| --- | --- |
| `ironflow_result=26` | Flow return value from `submit`, `map`, and `aggregate`. |
| `ironflow_events=15` | Append-only control-plane events for this run (stable for the same code/version). |

## 3. What this proves

- **`prefect_compat`** imports work from a wheel install.
- Orchestration runs **in-process** (no server, no ports).
- The control plane records structured lifecycle events.

## 4. Next steps

| Goal | Page |
| --- | --- |
| Start the API and deploy with the CLI | [Quickstart: first deployment](quickstart-first-deployment.md) |
| Run the repo demo with more examples | [Quick start (demo flow)](QUICKSTART_DEMO.md) (requires clone) |
| Map Prefect concepts | [Prefect → IronFlow](PREFECT_IRONFLOW_MAPPING.md) |
| Supported vs unsupported features | [Compatibility matrix](compatibility.md) |
