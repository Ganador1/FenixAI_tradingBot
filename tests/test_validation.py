from src.core.orchestrator.validation import validate_agent_response


def test_decision_confidence_validation_is_case_insensitive():
    errors = validate_agent_response(
        "decision_agent",
        {
            "final_decision": "BUY",
            "confidence_in_decision": "medium",
            "combined_reasoning": "ok",
        },
    )
    assert errors == []


def test_risk_score_range_is_enforced():
    too_high = validate_agent_response(
        "risk_manager",
        {"verdict": "APPROVE", "risk_score": 11},
    )
    assert any("out of range" in e for e in too_high)

    too_low = validate_agent_response(
        "risk_manager",
        {"verdict": "APPROVE", "risk_score": -1},
    )
    assert any("out of range" in e for e in too_low)

    ok = validate_agent_response(
        "risk_manager",
        {"verdict": "APPROVE", "risk_score": 5},
    )
    assert ok == []

