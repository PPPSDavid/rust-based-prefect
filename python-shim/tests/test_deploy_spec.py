from __future__ import annotations

from prefect_compat.deploy import DeploymentSpec, FlowOxideManifest
from prefect_compat.deploy.spec import parse_entrypoint
from prefect_compat.deploy.yaml_loader import load_manifest


def test_parse_entrypoint():
    module, func = parse_entrypoint("flows/etl.py:my_etl")
    assert module == "flows.etl"
    assert func == "my_etl"


def test_deployment_spec_from_entrypoint():
    spec = DeploymentSpec.from_entrypoint(
        name="prod",
        entrypoint="flows/etl.py:my_etl",
        parameters={"n": 3},
        work_pool_name="default-process-pool",
        schedule_cron="0 * * * *",
    )
    assert spec.flow_name == "my_etl"
    assert spec.entrypoint == "flows.etl:my_etl"
    assert spec.schedule_enabled is True


def test_deployment_spec_to_api_body():
    spec = DeploymentSpec.from_entrypoint(
        name="prod",
        entrypoint="flows/etl.py:my_etl",
        parameters={"n": 3},
        work_pool_name="default-process-pool",
        schedule_cron="0 * * * *",
    )
    body = spec.to_api_body(work_pool_id="pool-123")
    assert body["name"] == "prod"
    assert body["flow_name"] == "my_etl"
    assert body["entrypoint"] == "flows.etl:my_etl"
    assert body["default_parameters"] == {"n": 3}
    assert body["schedule_cron"] == "0 * * * *"
    assert body["schedule_enabled"] is True
    assert body["work_pool_id"] == "pool-123"


def test_manifest_load_minimal(tmp_path):
    manifest_path = tmp_path / "flowoxide.yaml"
    manifest_path.write_text(
        """\
flowoxide-version: "1"
deployments:
  - name: prod
    entrypoint: flows/etl.py:my_etl
    parameters:
      n: 3
    work_pool:
      name: default-process-pool
    schedule:
      cron: "0 * * * *"
""",
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert isinstance(manifest, FlowOxideManifest)
    assert manifest.flowoxide_version == "1"
    assert len(manifest.deployments) == 1

    spec = manifest.deployments[0]
    assert spec.name == "prod"
    assert spec.flow_name == "my_etl"
    assert spec.entrypoint == "flows.etl:my_etl"
    assert spec.default_parameters == {"n": 3}
    assert spec.work_pool_name == "default-process-pool"
    assert spec.schedule_cron == "0 * * * *"
    assert spec.schedule_enabled is True
