from static_planner import compile_and_forecast, compile_flow_source


def test_compile_submit_and_map_chain():
    source = """
a = t1.submit(1)
b = t2.submit(a)
c = t3.map([1,2,3])
"""
    graph, diagnostics = compile_flow_source(source, flow_name="demo")
    manifest = graph.as_manifest()

    assert diagnostics.fallback_required is False
    assert len(manifest["nodes"]) == 3
    assert any(e["from"] == "n1" and e["to"] == "n2" for e in manifest["edges"])


def test_compile_bounded_loop():
    source = """
for i in range(3):
    x = task_a.submit(i)
"""
    output = compile_and_forecast(source, flow_name="loop_demo")
    assert output["forecast"]["task_count"] == 3
    assert output["diagnostics"]["fallback_required"] is False


def test_conditional_falls_back():
    source = """
if flag:
    x = task_a.submit(1)
"""
    output = compile_and_forecast(source, flow_name="if_demo")
    assert output["diagnostics"]["fallback_required"] is True
