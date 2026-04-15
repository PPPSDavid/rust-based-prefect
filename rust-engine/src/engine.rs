use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum RunState {
    Scheduled,
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FlowRun {
    pub id: Uuid,
    pub name: String,
    pub state: RunState,
    pub version: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskRun {
    pub id: Uuid,
    pub flow_run_id: Uuid,
    pub task_key: String,
    pub state: RunState,
    pub version: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventRecord {
    pub event_id: Uuid,
    pub timestamp: DateTime<Utc>,
    pub run_id: Uuid,
    pub from_state: RunState,
    pub to_state: RunState,
    pub transition_token: Uuid,
    pub transition_kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetStateRequest {
    pub run_id: Uuid,
    pub to_state: RunState,
    pub expected_version: Option<u64>,
    pub transition_token: Uuid,
    pub transition_kind: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TransitionStatus {
    Applied,
    Duplicate,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetStateResponse {
    pub status: TransitionStatus,
    pub current_state: RunState,
    pub version: u64,
}

#[derive(Debug, Error)]
pub enum EngineError {
    #[error("flow run not found: {0}")]
    MissingRun(Uuid),
    #[error("invalid transition from {from:?} to {to:?}")]
    InvalidTransition { from: RunState, to: RunState },
    #[error("optimistic concurrency check failed: expected {expected}, got {actual}")]
    VersionConflict { expected: u64, actual: u64 },
}

#[derive(Default)]
pub struct Engine {
    flow_runs: HashMap<Uuid, FlowRun>,
    task_runs: HashMap<Uuid, TaskRun>,
    event_log: Vec<EventRecord>,
    applied_tokens: HashSet<Uuid>,
}

impl Engine {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn create_flow_run(&mut self, name: impl Into<String>) -> FlowRun {
        let run = FlowRun {
            id: Uuid::new_v4(),
            name: name.into(),
            state: RunState::Scheduled,
            version: 0,
        };
        self.flow_runs.insert(run.id, run.clone());
        run
    }

    pub fn create_task_run(&mut self, flow_run_id: Uuid, task_key: impl Into<String>) -> TaskRun {
        let task = TaskRun {
            id: Uuid::new_v4(),
            flow_run_id,
            task_key: task_key.into(),
            state: RunState::Scheduled,
            version: 0,
        };
        self.task_runs.insert(task.id, task.clone());
        task
    }

    pub fn set_flow_state(&mut self, req: SetStateRequest) -> Result<SetStateResponse, EngineError> {
        if self.applied_tokens.contains(&req.transition_token) {
            let run = self
                .flow_runs
                .get(&req.run_id)
                .ok_or(EngineError::MissingRun(req.run_id))?;
            return Ok(SetStateResponse {
                status: TransitionStatus::Duplicate,
                current_state: run.state,
                version: run.version,
            });
        }

        let run = self
            .flow_runs
            .get_mut(&req.run_id)
            .ok_or(EngineError::MissingRun(req.run_id))?;

        if let Some(expected) = req.expected_version {
            if expected != run.version {
                return Err(EngineError::VersionConflict {
                    expected,
                    actual: run.version,
                });
            }
        }

        validate_transition(run.state, req.to_state)?;

        let event = EventRecord {
            event_id: Uuid::new_v4(),
            timestamp: Utc::now(),
            run_id: run.id,
            from_state: run.state,
            to_state: req.to_state,
            transition_token: req.transition_token,
            transition_kind: req.transition_kind,
        };

        run.state = req.to_state;
        run.version += 1;
        self.event_log.push(event);
        self.applied_tokens.insert(req.transition_token);

        Ok(SetStateResponse {
            status: TransitionStatus::Applied,
            current_state: run.state,
            version: run.version,
        })
    }

    pub fn get_flow_run(&self, run_id: Uuid) -> Option<&FlowRun> {
        self.flow_runs.get(&run_id)
    }

    pub fn event_log(&self) -> &[EventRecord] {
        &self.event_log
    }
}

fn validate_transition(from: RunState, to: RunState) -> Result<(), EngineError> {
    if from == to {
        return Err(EngineError::InvalidTransition { from, to });
    }

    let allowed = match from {
        RunState::Scheduled => {
            matches!(to, RunState::Pending | RunState::Cancelled)
        }
        RunState::Pending => matches!(to, RunState::Running | RunState::Cancelled),
        RunState::Running => matches!(to, RunState::Completed | RunState::Failed | RunState::Cancelled),
        RunState::Completed | RunState::Failed | RunState::Cancelled => false,
    };

    if allowed {
        Ok(())
    } else {
        Err(EngineError::InvalidTransition { from, to })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_tokens_are_idempotent() {
        let mut engine = Engine::new();
        let run = engine.create_flow_run("flow");
        let token = Uuid::new_v4();

        let first = engine
            .set_flow_state(SetStateRequest {
                run_id: run.id,
                to_state: RunState::Pending,
                expected_version: Some(0),
                transition_token: token,
                transition_kind: "propose".to_string(),
            })
            .unwrap();
        assert_eq!(first.status, TransitionStatus::Applied);

        let second = engine
            .set_flow_state(SetStateRequest {
                run_id: run.id,
                to_state: RunState::Pending,
                expected_version: Some(0),
                transition_token: token,
                transition_kind: "propose".to_string(),
            })
            .unwrap();
        assert_eq!(second.status, TransitionStatus::Duplicate);
        assert_eq!(engine.event_log().len(), 1);
    }
}
