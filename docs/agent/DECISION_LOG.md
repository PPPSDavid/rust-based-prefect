# Decision log (short ADRs)

Append when a significant architecture / compatibility / perf choice is made
(especially after the three-expert protocol in `AGENTS.md`). Keep entries short.

## Template

```md
### YYYY-MM-DD — <title>
- Context:
- Decision:
- Alternatives rejected:
- Consequences / follow-ups:
```

## Entries

### 2026-07-12 — Cloud CRG without embeddings by default
- Context: Agents need a reliable knowledge graph on Cursor Cloud; some desktop setups use conda + local embedding models.
- Decision: Core `code-review-graph` on Cloud (`requirements-agent.txt`); embeddings optional. Structural MCP tools are the primary value; NL search quality is a separate opt-in.
- Alternatives rejected: Always install `[embeddings]` (torch weight, slow boot); require conda on Cloud.
- Consequences: `embeddings_count=0` is expected; document NL-search gaps; desktop embeddings via explicit `CRG_MCP_USE_CONDA` + `CRG_MCP_CONDA_ENV`.
