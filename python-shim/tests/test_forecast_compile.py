from __future__ import annotations

import textwrap

from prefect_compat.decorators import flow, task
from prefect_compat.forecast_compile import _compile_forecast_for_flow


@task
def inc(x: int) -> int:
    return x + 1


@task
def dbl(x: int) -> int:
    return x * 2


@flow
def sample_wide_flow(n: int) -> int:
    first = inc.submit(n)
    mapped = dbl.map(range(n), wait_for=[first])
    return sum(f.result() for f in mapped)


def test_compile_forecast_reads_flow_function_body():
    info = _compile_forecast_for_flow(sample_wide_flow, "sample_wide_flow")
    manifest = info["manifest"]

    assert info["source"] == "forecast"
    assert info["fallback_required"] is False
    assert len(manifest["nodes"]) == 2
    assert info["forecast"]["task_count"] == 2


def test_compile_forecast_handles_decorated_source_string():
    source = textwrap.dedent(
        """
        @flow
        def demo(n: int) -> int:
            a = inc.submit(n)
            b = dbl.submit(a, wait_for=[a])
            return b.result()
        """
    )
    from static_planner import compile_and_forecast

    out = compile_and_forecast(source, flow_name="demo")
    assert len(out["manifest"]["nodes"]) == 2
    assert out["diagnostics"]["fallback_required"] is False
