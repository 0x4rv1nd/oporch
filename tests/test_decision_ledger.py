from datetime import datetime, timezone

from oporch.decision_ledger import DecisionLedger
from oporch.models import OrchestratorDecision


class TestDecisionLedger:
    def setup_method(self):
        self.ledger = DecisionLedger()
        self.ledger.clear()

    def test_empty_ledger(self):
        assert self.ledger.count() == 0
        assert self.ledger.all() == []

    def test_append_decision(self):
        d = OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="Which database?",
            decision="SQLite",
            basis=["PRD section 4"],
            confidence=0.9,
        )
        self.ledger.append(d)
        assert self.ledger.count() == 1

    def test_search_finds_match(self):
        self.ledger.append(OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="Which database?",
            decision="SQLite",
        ))
        results = self.ledger.search("database")
        assert len(results) == 1

    def test_search_no_match(self):
        self.ledger.append(OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="Which database?",
            decision="SQLite",
        ))
        results = self.ledger.search("postgres")
        assert len(results) == 0

    def test_find_by_question(self):
        self.ledger.append(OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="Which database?",
            decision="SQLite",
        ))
        found = self.ledger.find_by_question("Which database?")
        assert found is not None
        assert found.decision == "SQLite"

    def test_find_by_question_not_found(self):
        found = self.ledger.find_by_question("Nonexistent")
        assert found is None

    def test_next_id_increments(self):
        assert self.ledger.next_id() == "DEC-0001"
        self.ledger.append(OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="q1",
            decision="d1",
        ))
        assert self.ledger.next_id() == "DEC-0002"

    def test_clear(self):
        self.ledger.append(OrchestratorDecision(
            decision_id="DEC-0001",
            timestamp=datetime.now(timezone.utc),
            run_id="run-001",
            milestone_id="M0",
            question="q1",
            decision="d1",
        ))
        self.ledger.clear()
        assert self.ledger.count() == 0

    def test_multiple_decisions(self):
        for i in range(5):
            self.ledger.append(OrchestratorDecision(
                decision_id=f"DEC-{i+1:04d}",
                timestamp=datetime.now(timezone.utc),
                run_id="run-001",
                milestone_id="M0",
                question=f"q{i}",
                decision=f"d{i}",
            ))
        assert self.ledger.count() == 5
        assert len(self.ledger.all()) == 5
