from enum import Enum


SCHEMA_VERSION = 1
SCHEMA_VERSION_FILE = "schema_version.txt"


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    ARCHITECT = "architect"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    DEBUGGER = "debugger"
    RESEARCHER = "researcher"
    BENCHMARK_ANALYST = "benchmark_analyst"
    # v2: thin cross-cutting merge-gate role (§7). Only the supervisor may
    # merge into the per-run integration branch.
    SUPERVISOR = "supervisor"


class OrchestratorState(str, Enum):
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    COMPOSING_TEAM = "COMPOSING_TEAM"
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    TESTING = "TESTING"
    DEBUGGING = "DEBUGGING"
    REPLANNING = "REPLANNING"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


STATE_TRANSITIONS: dict[OrchestratorState, list[OrchestratorState]] = {
    OrchestratorState.IDLE: [OrchestratorState.ANALYZING, OrchestratorState.CANCELLED],
    # PLANNING kept reachable directly for objective-only plans without a
    # plan document; plan-doc input goes through COMPOSING_TEAM first.
    OrchestratorState.ANALYZING: [
        OrchestratorState.COMPOSING_TEAM,
        OrchestratorState.PLANNING,
        OrchestratorState.FAILED,
        OrchestratorState.CANCELLED,
    ],
    OrchestratorState.COMPOSING_TEAM: [
        OrchestratorState.PLANNING,
        OrchestratorState.AWAITING_USER_INPUT,
        OrchestratorState.FAILED,
        OrchestratorState.CANCELLED,
    ],
    OrchestratorState.PLANNING: [OrchestratorState.AWAITING_PLAN_APPROVAL, OrchestratorState.FAILED, OrchestratorState.CANCELLED],
    OrchestratorState.AWAITING_PLAN_APPROVAL: [OrchestratorState.EXECUTING, OrchestratorState.REPLANNING, OrchestratorState.CANCELLED],
    OrchestratorState.EXECUTING: [OrchestratorState.REVIEWING, OrchestratorState.REPLANNING, OrchestratorState.FAILED, OrchestratorState.CANCELLED],
    OrchestratorState.REVIEWING: [OrchestratorState.TESTING, OrchestratorState.EXECUTING, OrchestratorState.AWAITING_USER_INPUT, OrchestratorState.FAILED, OrchestratorState.CANCELLED],
    OrchestratorState.TESTING: [OrchestratorState.VALIDATING, OrchestratorState.DEBUGGING, OrchestratorState.CANCELLED],
    OrchestratorState.DEBUGGING: [OrchestratorState.EXECUTING, OrchestratorState.REPLANNING, OrchestratorState.AWAITING_USER_INPUT, OrchestratorState.CANCELLED],
    OrchestratorState.REPLANNING: [OrchestratorState.PLANNING, OrchestratorState.AWAITING_USER_INPUT, OrchestratorState.FAILED, OrchestratorState.CANCELLED],
    OrchestratorState.AWAITING_USER_INPUT: [OrchestratorState.ANALYZING, OrchestratorState.EXECUTING, OrchestratorState.REPLANNING, OrchestratorState.CANCELLED],
    OrchestratorState.VALIDATING: [OrchestratorState.COMPLETED, OrchestratorState.EXECUTING, OrchestratorState.REPLANNING, OrchestratorState.FAILED, OrchestratorState.CANCELLED],
    OrchestratorState.COMPLETED: [],
    OrchestratorState.FAILED: [],
    OrchestratorState.CANCELLED: [],
}


class WorkUnitStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    # v2 (§7): supervisor merge gate hit a conflict against the integration
    # branch. Routed to debugger for a resolution attempt by default.
    MERGE_CONFLICT = "MERGE_CONFLICT"


class ReviewVerdict(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    BLOCK = "BLOCK"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ApprovalMode(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    SUPERVISED = "SUPERVISED"
    STRICT = "STRICT"


class EventType(str, Enum):
    RUN_CREATED = "RUN_CREATED"
    ROSTER_CREATED = "ROSTER_CREATED"
    ROSTER_APPROVED = "ROSTER_APPROVED"
    ROSTER_ADJUSTED = "ROSTER_ADJUSTED"
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_APPROVED = "PLAN_APPROVED"
    MODEL_SELECTED = "MODEL_SELECTED"
    WORK_UNIT_READY = "WORK_UNIT_READY"
    WORK_UNIT_STARTED = "WORK_UNIT_STARTED"
    WORKER_QUESTION = "WORKER_QUESTION"
    ORCHESTRATOR_ANSWER = "ORCHESTRATOR_ANSWER"
    USER_ESCALATION = "USER_ESCALATION"
    WORK_UNIT_COMPLETED = "WORK_UNIT_COMPLETED"
    WU_MERGED = "WU_MERGED"
    MERGE_CONFLICT_EVENT = "MERGE_CONFLICT"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_FAILED = "REVIEW_FAILED"
    TEST_STARTED = "TEST_STARTED"
    TEST_FAILED = "TEST_FAILED"
    DEBUG_STARTED = "DEBUG_STARTED"
    REPLAN_STARTED = "REPLAN_STARTED"
    VALIDATION_STARTED = "VALIDATION_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"


class ClaimType(str, Enum):
    CLAIM = "CLAIM"
    EVIDENCE = "EVIDENCE"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"
