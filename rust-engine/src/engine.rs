use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
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
    /// Flow run id for UI correlation (flow transitions and task transitions).
    pub run_id: Uuid,
    #[serde(default)]
    pub task_run_id: Option<Uuid>,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetTaskStateRequest {
    pub task_run_id: Uuid,
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
    #[error("task run not found: {0}")]
    MissingTask(Uuid),
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

    pub fn register_flow_run(&mut self, run: FlowRun) {
        self.flow_runs.insert(run.id, run);
    }

    pub fn register_task_run(&mut self, task: TaskRun) {
        self.task_runs.insert(task.id, task);
    }

    /// Replay / migration helper: set authoritative state without FSM checks or token accounting.
    pub fn apply_flow_checkpoint(&mut self, run_id: Uuid, state: RunState, version: u64) -> Result<(), EngineError> {
        let run = self
            .flow_runs
            .get_mut(&run_id)
            .ok_or(EngineError::MissingRun(run_id))?;
        run.state = state;
        run.version = version;
        Ok(())
    }

    /// Replay / migration helper for task rows persisted without transition tokens.
    pub fn apply_task_checkpoint(
        &mut self,
        task_run_id: Uuid,
        state: RunState,
        version: u64,
    ) -> Result<(), EngineError> {
        let task = self
            .task_runs
            .get_mut(&task_run_id)
            .ok_or(EngineError::MissingTask(task_run_id))?;
        task.state = state;
        task.version = version;
        Ok(())
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
            task_run_id: None,
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

    pub fn set_task_state(&mut self, req: SetTaskStateRequest) -> Result<SetStateResponse, EngineError> {
        if self.applied_tokens.contains(&req.transition_token) {
            let task = self
                .task_runs
                .get(&req.task_run_id)
                .ok_or(EngineError::MissingTask(req.task_run_id))?;
            return Ok(SetStateResponse {
                status: TransitionStatus::Duplicate,
                current_state: task.state,
                version: task.version,
            });
        }

        let task = self
            .task_runs
            .get_mut(&req.task_run_id)
            .ok_or(EngineError::MissingTask(req.task_run_id))?;

        if let Some(expected) = req.expected_version {
            if expected != task.version {
                return Err(EngineError::VersionConflict {
                    expected,
                    actual: task.version,
                });
            }
        }

        validate_transition(task.state, req.to_state)?;

        let flow_run_id = task.flow_run_id;
        let task_id = task.id;
        let from_state = task.state;

        let event = EventRecord {
            event_id: Uuid::new_v4(),
            timestamp: Utc::now(),
            run_id: flow_run_id,
            task_run_id: Some(task_id),
            from_state,
            to_state: req.to_state,
            transition_token: req.transition_token,
            transition_kind: req.transition_kind,
        };

        task.state = req.to_state;
        task.version += 1;
        self.event_log.push(event);
        self.applied_tokens.insert(req.transition_token);

        Ok(SetStateResponse {
            status: TransitionStatus::Applied,
            current_state: task.state,
            version: task.version,
        })
    }

    pub fn get_flow_run(&self, run_id: Uuid) -> Option<&FlowRun> {
        self.flow_runs.get(&run_id)
    }

    pub fn event_log(&self) -> &[EventRecord] {
        &self.event_log
    }

    pub fn get_task_run(&self, task_run_id: Uuid) -> Option<&TaskRun> {
        self.task_runs.get(&task_run_id)
    }
}

pub fn validate_transition(from: RunState, to: RunState) -> Result<(), EngineError> {
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

    #[test]
    fn task_duplicate_tokens_are_idempotent() {
        let mut engine = Engine::new();
        let flow = engine.create_flow_run("f");
        let task = engine.create_task_run(flow.id, "t1");
        let token = Uuid::new_v4();

        let first = engine
            .set_task_state(SetTaskStateRequest {
                task_run_id: task.id,
                to_state: RunState::Pending,
                expected_version: Some(0),
                transition_token: token,
                transition_kind: "task_pending".to_string(),
            })
            .unwrap();
        assert_eq!(first.status, TransitionStatus::Applied);

        let second = engine
            .set_task_state(SetTaskStateRequest {
                task_run_id: task.id,
                to_state: RunState::Pending,
                expected_version: Some(0),
                transition_token: token,
                transition_kind: "task_pending".to_string(),
            })
            .unwrap();
        assert_eq!(second.status, TransitionStatus::Duplicate);
        assert_eq!(engine.event_log().len(), 1);
    }

    #[test]
    fn rejects_invalid_task_transition() {
        let mut engine = Engine::new();
        let flow = engine.create_flow_run("f");
        let task = engine.create_task_run(flow.id, "t1");

        let err = engine.set_task_state(SetTaskStateRequest {
            task_run_id: task.id,
            to_state: RunState::Completed,
            expected_version: Some(0),
            transition_token: Uuid::new_v4(),
            transition_kind: "bad".to_string(),
        });
        assert!(matches!(err, Err(EngineError::InvalidTransition { .. })));
    }
}
