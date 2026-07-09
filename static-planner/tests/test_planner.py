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


def test_compile_decorated_flow_function_body():
    source = """
@flow(task_runner=ThreadPoolTaskRunner())
def wide_flow(n: int) -> int:
    first = inc.submit(n)
    mapped_futs = dbl.map(range(n), wait_for=[first])
    wait(mapped_futs)
    return sum(f.result() for f in mapped_futs)
"""
    graph, diagnostics = compile_flow_source(source, flow_name="wide_flow")
    manifest = graph.as_manifest()

    assert diagnostics.fallback_required is False
    assert len(manifest["nodes"]) == 2
    assert any(n["task_name"] == "inc" and n["op_type"] == "submit" for n in manifest["nodes"])
    assert any(n["task_name"] == "dbl" and n["op_type"] == "map" for n in manifest["nodes"])
    map_node = next(n for n in manifest["nodes"] if n["op_type"] == "map")
    inc_node = next(n for n in manifest["nodes"] if n["task_name"] == "inc")
    assert inc_node["node_id"] in map_node["deps"]
    assert any(e["from"] == inc_node["node_id"] and e["to"] == map_node["node_id"] for e in manifest["edges"])


def test_compile_wait_for_dependency():
    source = """
def chained_flow(n: int) -> int:
    f = passthrough.submit(0)
    for _ in range(3):
        f = inc.submit(f, wait_for=[f])
    return f.result()
"""
    graph, diagnostics = compile_flow_source(source, flow_name="chained_flow")
    manifest = graph.as_manifest()

    assert diagnostics.fallback_required is False
    assert len(manifest["nodes"]) == 4
    assert manifest["nodes"][0]["task_name"] == "passthrough"
    assert manifest["nodes"][1]["deps"] == [manifest["nodes"][0]["node_id"]]


def test_compile_dynamic_loop_still_falls_back():
    source = """
def long_chain_flow(n: int) -> int:
    f = passthrough.submit(0)
    for _ in range(n):
        f = inc.submit(f, wait_for=[f])
    return f.result()
"""
    graph, diagnostics = compile_flow_source(source, flow_name="long_chain_flow")
    manifest = graph.as_manifest()

    assert diagnostics.fallback_required is True
    assert len(manifest["nodes"]) == 1
    assert manifest["nodes"][0]["task_name"] == "passthrough"


def test_conditional_falls_back():
    source = """
if flag:
    x = task_a.submit(1)
"""
    output = compile_and_forecast(source, flow_name="if_demo")
    assert output["diagnostics"]["fallback_required"] is True
