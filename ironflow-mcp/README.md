# ironflow-mcp

Read-only [MCP](https://modelcontextprotocol.io/) server for IronFlow, built with
[FastMCP 4](https://gofastmcp.com/).

Agents can inspect flows, deployments, flow runs, logs, events, work pools,
concurrency limits, and DAGs. Mutations stay on the `ironflow` CLI
(see `docs/how-to/ai-assistants.md`).

## Run (stdio)

```bash
export IRONFLOW_API_URL=http://127.0.0.1:8000
# optional: export IRONFLOW_API_AUTH_STRING='user:pass'
ironflow-mcp
# or: python -m ironflow_mcp
```

Cursor (`.cursor/mcp.json` snippet):

```json
{
  "mcpServers": {
    "ironflow": {
      "command": "uv",
      "args": ["run", "--package", "ironflow-mcp", "ironflow-mcp"],
      "env": {
        "IRONFLOW_API_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

All tools are annotated `readOnlyHint=True`.
