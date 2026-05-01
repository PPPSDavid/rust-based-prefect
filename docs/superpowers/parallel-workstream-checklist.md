# Parallel Workstream Checklist

Use this checklist in the parent session before launching subagents.

## 1) Workstream Readiness

- [ ] Task is decomposed into independent workstreams.
- [ ] Dependencies between workstreams are explicit.
- [ ] Each workstream has one owner subagent.

## 2) Isolation

- [ ] One branch per workstream.
- [ ] One workspace/worktree per subagent.
- [ ] No parallel edits to hotspot files.

## 3) Contract Completeness

Each workstream includes:

- [ ] Goal
- [ ] Allowed paths
- [ ] Forbidden paths
- [ ] Acceptance criteria
- [ ] Validation commands
- [ ] Expected deliverables

## 4) Review Gates

- [ ] Subagent stayed in scope.
- [ ] Validation commands were run and reported.
- [ ] Tests/docs were updated where behavior changed.
- [ ] Risks/open questions are called out.

## 5) Integration

- [ ] Merge order follows dependencies.
- [ ] Integrate one branch at a time.
- [ ] Run final full validation after integration.
- [ ] Update roadmap/changelog or follow-up tasks.

## Copy-Paste Subagent Contract

```markdown
Task: <short title>

Goal:
- <target outcome>

Scope (allowed paths):
- <path 1>
- <path 2>

Forbidden paths:
- <path A>
- <path B>

Branch:
- <branch-name>

Acceptance criteria:
- <criterion 1>
- <criterion 2>

Validation to run:
- <command 1>
- <command 2>

Deliverables back:
- summary of changes
- modified files
- validation output
- risks/open questions
```
