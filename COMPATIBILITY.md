# Compatibility Matrix

This document tracks compatibility targets against Prefect OSS.

## Baseline

- Upstream project: `prefecthq/prefect` (self-hosted OSS context)
- Baseline major/minor: `3.x`
- Initial validation target: `3.0.0`

## Phase 1 runtime compatibility (current MVP target)

- Supported:
  - `@flow` and `@task` decorated functions (compatibility shim).
  - `task.submit()` dependency chains.
  - `task.map()` with moderate fan-out.
  - retries / timeouts / cancellation intent propagation.
  - concurrency limit tags (control-plane enforced).
- Not yet supported:
  - full API parity for every Prefect state rule edge case.
  - advanced cloud/tenant features.
  - all blocks and integrations.

## Phase 2 static planning compatibility

- Supported subset (planned):
  - analyzable `submit/map` dependency chains.
  - bounded loops with static upper bounds.
  - explicit task dependencies and resource hints.
- Fallback:
  - non-analyzable dynamic sections run via runtime path and are represented as opaque subgraphs.

## Notes

- This is an independent project, not an official Prefect release.
- Compatibility is workload-driven and expanded incrementally.
