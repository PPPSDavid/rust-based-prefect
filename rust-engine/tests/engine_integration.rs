use ironflow_engine::{Engine, RunState, SetStateRequest, TransitionStatus};
use uuid::Uuid;

#[test]
fn state_machine_happy_path() {
    let mut engine = Engine::new();
    let run = engine.create_flow_run("demo");

    let pending = engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Pending,
            expected_version: Some(0),
            transition_token: Uuid::new_v4(),
            transition_kind: "propose".to_string(),
        })
        .unwrap();
    assert_eq!(pending.status, TransitionStatus::Applied);

    let running = engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Running,
            expected_version: Some(1),
            transition_token: Uuid::new_v4(),
            transition_kind: "start".to_string(),
        })
        .unwrap();
    assert_eq!(running.status, TransitionStatus::Applied);

    let done = engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Completed,
            expected_version: Some(2),
            transition_token: Uuid::new_v4(),
            transition_kind: "complete".to_string(),
        })
        .unwrap();
    assert_eq!(done.current_state, RunState::Completed);
    assert_eq!(engine.event_log().len(), 3);
}

#[test]
fn rejects_invalid_terminal_transition() {
    let mut engine = Engine::new();
    let run = engine.create_flow_run("demo");

    engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Pending,
            expected_version: Some(0),
            transition_token: Uuid::new_v4(),
            transition_kind: "propose".to_string(),
        })
        .unwrap();
    engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Running,
            expected_version: Some(1),
            transition_token: Uuid::new_v4(),
            transition_kind: "start".to_string(),
        })
        .unwrap();
    engine
        .set_flow_state(SetStateRequest {
            run_id: run.id,
            to_state: RunState::Completed,
            expected_version: Some(2),
            transition_token: Uuid::new_v4(),
            transition_kind: "complete".to_string(),
        })
        .unwrap();

    let invalid = engine.set_flow_state(SetStateRequest {
        run_id: run.id,
        to_state: RunState::Running,
        expected_version: Some(3),
        transition_token: Uuid::new_v4(),
        transition_kind: "retry".to_string(),
    });

    assert!(invalid.is_err());
}
