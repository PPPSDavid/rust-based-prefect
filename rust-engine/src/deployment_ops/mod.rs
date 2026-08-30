//! Deployment queue, scheduling, and worker heartbeats — SQLite hot paths kept in Rust.
//! Called from `ironflow_control` when `bind_db` has attached a connection.

mod claim;
mod crud;
mod lifecycle;
mod rows;
mod schedule;
mod tick;

pub use claim::{
    claim_next_deployment_run, reclaim_expired_claims, trigger_deployment_run,
    trigger_deployment_run_tx, worker_heartbeat, DeploymentParentLink,
};
pub use crud::{create_deployment, update_deployment};
pub use lifecycle::{
    attach_flow_run_to_deployment_run, cancel_deployment_runs_for_parent_flow,
    deployment_maintenance, get_deployment_run, mark_deployment_run_finished,
    mark_deployment_run_started, reap_stale_workers,
};
pub use tick::tick_deployment_schedules;

#[cfg(test)]
pub(crate) use rows::DEFAULT_WORK_POOL_ID;
#[cfg(test)]
pub(crate) use schedule::{next_cron_occurrence, next_rrule_occurrence};

#[cfg(test)]
mod tests;
