#!/usr/bin/env python3
"""Generate llms.txt at site root for AI/agent discovery (Prefect-style sitemap)."""

from __future__ import annotations

from mkdocs_gen_files import open as gen_open

SITE_BASE = "https://pppsdavid.github.io/rust-based-prefect"
REPO_BASE = "https://github.com/PPPSDavid/rust-based-prefect/blob/main/docs"

PAGES: list[tuple[str, str, str]] = [
    ("Home", "", "Layered doc home and onboarding paths."),
    (
        "Installation (PyPI · pip / uv)",
        "INSTALL/",
        "Primary install path from PyPI wheels.",
    ),
    (
        "Quick start: PyPI (no clone)",
        "QUICKSTART_PYPI/",
        "Minimal in-process flow after pip install.",
    ),
    (
        "Quick start (demo flow)",
        "QUICKSTART_DEMO/",
        "Repo demo with bundled example script.",
    ),
    (
        "Quickstart: first deployment",
        "quickstart-first-deployment/",
        "Install → API → ironflow deploy → trigger run.",
    ),
    (
        "Self-hosted server",
        "SELF_HOSTED_SERVER/",
        "API, workers, deployments, scheduling.",
    ),
    (
        "Verify the web UI",
        "ui_e2e_visual_check/",
        "Visual checklist for the optional Vite UI.",
    ),
    ("Concepts overview", "concepts/", "Flows, tasks, runners, states, DAG."),
    ("Flows", "concepts/flows/", "@flow decorator and flow runs."),
    ("Tasks", "concepts/tasks/", "@task, submit, map, futures."),
    ("Runners", "concepts/runners/", "Task runners and concurrent map."),
    ("DAG and forecast", "concepts/dag-and-forecast/", "Static planner and run DAG."),
    (
        "States and transitions",
        "concepts/states-and-transitions/",
        "RunState and Rust FSM edges.",
    ),
    (
        "Prefect → IronFlow",
        "PREFECT_IRONFLOW_MAPPING/",
        "Concept mapping from Prefect 3.",
    ),
    ("Architecture", "architecture/", "Python shim, Rust engine, persistence."),
    ("How-to overview", "how-to/", "Task-focused guides index."),
    ("How to set up IronFlow", "how-to/setup/", "Clone, env, cargo build, env vars."),
    (
        "How to run the server and UI",
        "how-to/server-and-ui/",
        "ironflow_server.py and manual uvicorn.",
    ),
    (
        "How to deploy with CLI",
        "how-to/deploy-with-cli/",
        "ironflow.yaml and ironflow CLI.",
    ),
    (
        "How to create deployments",
        "how-to/deployments/",
        "HTTP API deployment recipes.",
    ),
    (
        "How to compose flows with subflows",
        "how-to/subflows/",
        "Inline and deployment-backed subflows.",
    ),
    (
        "How to port from Prefect",
        "how-to/port-from-prefect/",
        "Import swap and subset limits.",
    ),
    (
        "Compatibility matrix",
        "compatibility/",
        "Supported vs unsupported Prefect semantics.",
    ),
    ("Environment variables", "reference/env-vars/", "All IRONFLOW_* variables."),
    ("REST API overview", "reference/api/", "HTTP route index."),
    (
        "Troubleshooting",
        "reference/troubleshooting/",
        "Common install and server issues.",
    ),
    (
        "Performance (vs Prefect)",
        "PERFORMANCE_OVERVIEW/",
        "Control-plane throughput expectations.",
    ),
]

with gen_open("llms.txt", "w") as f:
    f.write("# Project IronFlow documentation index\n")
    f.write("# Fetch this file for a sitemap of published pages.\n")
    f.write(f"# Site: {SITE_BASE}\n")
    f.write("# Markdown source: append paths to the GitHub docs/ tree below.\n\n")
    for title, path, desc in PAGES:
        url = SITE_BASE if not path else f"{SITE_BASE}/{path}"
        md_path = "index.md" if not path else f"{path.rstrip('/')}.md"
        # Map published URLs back to repo markdown filenames
        md_map = {
            "INSTALL/": "INSTALL.md",
            "QUICKSTART_PYPI/": "QUICKSTART_PYPI.md",
            "QUICKSTART_DEMO/": "QUICKSTART_DEMO.md",
            "quickstart-first-deployment/": "quickstart-first-deployment.md",
            "SELF_HOSTED_SERVER/": "SELF_HOSTED_SERVER.md",
            "ui_e2e_visual_check/": "ui_e2e_visual_check.md",
            "concepts/": "concepts/index.md",
            "concepts/flows/": "concepts/flows.md",
            "concepts/tasks/": "concepts/tasks.md",
            "concepts/runners/": "concepts/runners.md",
            "concepts/dag-and-forecast/": "concepts/dag-and-forecast.md",
            "concepts/states-and-transitions/": "concepts/states-and-transitions.md",
            "PREFECT_IRONFLOW_MAPPING/": "PREFECT_IRONFLOW_MAPPING.md",
            "architecture/": "architecture.md",
            "how-to/": "how-to/index.md",
            "how-to/setup/": "how-to/setup.md",
            "how-to/server-and-ui/": "how-to/server-and-ui.md",
            "how-to/deploy-with-cli/": "how-to/deploy-with-cli.md",
            "how-to/deployments/": "how-to/deployments.md",
            "how-to/subflows/": "how-to/subflows.md",
            "how-to/port-from-prefect/": "how-to/port-from-prefect.md",
            "compatibility/": "compatibility.md",
            "reference/env-vars/": "reference/env-vars.md",
            "reference/api/": "reference/api.md",
            "reference/troubleshooting/": "reference/troubleshooting.md",
            "PERFORMANCE_OVERVIEW/": "PERFORMANCE_OVERVIEW.md",
        }
        md_file = md_map.get(path, "index.md")
        raw_md = f"{REPO_BASE}/{md_file}"
        f.write(f"- {title}: {url}\n")
        f.write(f"  {desc}\n")
        f.write(f"  markdown: {raw_md}\n\n")
