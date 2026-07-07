from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .constants import (
    SCHEMA_VERSION,
    AgentRole,
    ClaimType,
    EventType,
    OrchestratorState,
    ReviewVerdict,
    Severity,
    WorkUnitStatus,
)


class RoleConfig(BaseModel):
    description: str
    model: str
    fallback: str | None = None
    max_workers: int = 1


class RolesConfig(BaseModel):
    roles: dict[str, RoleConfig]


class RetryPolicy(BaseModel):
    max_attempts: int = 3
    attempt_2_receives_review: bool = True
    attempt_3_uses_debugger: bool = True


class CompletionGate(BaseModel):
    require_review_approval: bool = True
    require_tests_pass: bool = True
    require_benchmark_evidence: bool = False
    max_critical_findings: int = 0
    max_high_findings: int = 0


class ContextPolicy(BaseModel):
    include_relevant_prd_sections: bool = True
    include_prior_decisions: bool = True
    include_dependency_outputs: bool = True


class PoliciesConfig(BaseModel):
    approval_mode: str = "SUPERVISED"
    retry: RetryPolicy = RetryPolicy()
    completion_gate: CompletionGate = CompletionGate()
    context: ContextPolicy = ContextPolicy()


class ModelInfo(BaseModel):
    provider: str
    model_id: str
    context_limit: int = 131072
    output_limit: int = 16384


class ModelsConfig(BaseModel):
    models: dict[str, ModelInfo]


class Evidence(BaseModel):
    command: str | None = None
    exit_code: int | None = None
    summary: str | None = None


class Claim(BaseModel):
    claim: str
    type: ClaimType = ClaimType.CLAIM
    evidence: Evidence | None = None


class ContextPack(BaseModel):
    work_unit_id: str | None = None
    relevant_prd_sections: list[str] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)
    architecture_constraints: list[str] = Field(default_factory=list)
    dependency_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    diff: str | None = None
    failure_evidence: str | None = None


class WorkUnit(BaseModel):
    id: str
    title: str
    objective: str
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    dependencies: list[str] = Field(default_factory=list)
    assigned_role: AgentRole = AgentRole.BUILDER
    assigned_model: str | None = None
    input_context: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    evidence: list[Claim] = Field(default_factory=list)
    output: str | None = None
    blockers: list[str] = Field(default_factory=list)
    files_likely_affected: list[str] = Field(default_factory=list)
    tests_required: list[str] = Field(default_factory=list)

    def is_ready(self, completed_ids: set[str]) -> bool:
        if self.status not in (WorkUnitStatus.PENDING, WorkUnitStatus.BLOCKED):
            return False
        return all(dep in completed_ids for dep in self.dependencies)


class PlanResult(BaseModel):
    milestone_id: str
    objective: str
    work_units: list[WorkUnit]
    assumptions: list[str] = Field(default_factory=list)


class WorkerQuestion(BaseModel):
    type: str = "QUESTION"
    question_id: str
    question: str
    why_needed: str
    blocking: bool = True
    options: list[str] = Field(default_factory=list)
    evidence_checked: list[str] = Field(default_factory=list)


class BuilderResult(BaseModel):
    work_unit_id: str
    files_changed: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    output: str | None = None


class ReviewFinding(BaseModel):
    severity: Severity
    file: str | None = None
    location: str | None = None
    issue: str
    evidence: str | None = None
    recommended_action: str | None = None


class ReviewResult(BaseModel):
    verdict: ReviewVerdict
    findings: list[ReviewFinding] = Field(default_factory=list)
    reviewed_files: list[str] = Field(default_factory=list)
    reviewed_diff: str | None = None
    risk_categories_checked: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    work_unit_id: str
    command: str | None = None
    exit_code: int | None = None
    summary: str | None = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    missing_tests: list[str] = Field(default_factory=list)
    edge_cases_tried: list[str] = Field(default_factory=list)


class DebugResult(BaseModel):
    failure_description: str
    reproduction_steps: list[str] = Field(default_factory=list)
    earliest_failure_stage: str | None = None
    root_cause: str | None = None
    verified_hypothesis: bool = False
    recommended_fix: str | None = None
    regression_risk: str | None = None


class ResearchResult(BaseModel):
    topic: str
    verified_facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    license_info: str | None = None
    maintenance_status: str | None = None
    integration_cost: str | None = None


class BenchmarkResult(BaseModel):
    metric_name: str
    before_value: float | None = None
    after_value: float | None = None
    detected_count: int | None = None
    ground_truth_count: int | None = None
    drift_detected: bool = False
    misleading_metrics: list[str] = Field(default_factory=list)


class OrchestratorDecision(BaseModel):
    decision_id: str
    timestamp: datetime
    run_id: str
    milestone_id: str
    question: str
    decision: str
    basis: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    escalated_to_user: bool = False


class OrchestratorEvent(BaseModel):
    timestamp: datetime
    run_id: str
    event: EventType
    work_unit_id: str | None = None
    agent_role: AgentRole | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MilestoneReport(BaseModel):
    objective: str
    status: str
    work_units: list[WorkUnit]
    files_changed: list[str] = Field(default_factory=list)
    tests_executed: list[TestResult] = Field(default_factory=list)
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    benchmark_results: list[BenchmarkResult] = Field(default_factory=list)
    decisions_made: list[OrchestratorDecision] = Field(default_factory=list)
    user_escalations: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    evidence: list[Claim] = Field(default_factory=list)
    recommendation: str | None = None


class RunState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    milestone_id: str
    objective: str
    state: OrchestratorState = OrchestratorState.IDLE
    created_at: datetime
    updated_at: datetime
    state_history: list[dict[str, Any]] = Field(default_factory=list)
    approval_mode: str = "SUPERVISED"


class CurrentRun(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str | None = None
    milestone_id: str | None = None
    state: OrchestratorState = OrchestratorState.IDLE
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentTask(BaseModel):
    objective: str
    work_unit_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    input_context: str | None = None
    max_attempts: int = 3
    raw_prompt: str | None = None


class AgentResult(BaseModel):
    role: AgentRole
    success: bool
    output: str = ""
    error: str | None = None
    claims: list[Claim] = Field(default_factory=list)


class AgentOutputResult(BaseModel):
    status: Literal["valid", "repaired", "failed"]
    raw_text: str
    parsed: dict[str, Any] | None = None
    error: str | None = None


class PlannerContextPack(BaseModel):
    milestone_id: str
    milestone_objective: str
    relevant_prd_sections: list[str] = Field(default_factory=list)
    repo_summary: str = ""
    architecture_constraints: list[str] = Field(default_factory=list)
    prior_decisions: list[OrchestratorDecision] = Field(default_factory=list)
    prior_milestone_summaries: list[str] = Field(default_factory=list)


class ConfigYaml(BaseModel):
    roles: RolesConfig | None = None
    policies: PoliciesConfig | None = None
    models: ModelsConfig | None = None
