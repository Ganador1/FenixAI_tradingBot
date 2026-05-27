# src/core/orchestrator/validation.py
"""
Response validation system for Fenix Trading Bot agents.

Validates LLM responses against agent-specific rules to ensure
proper JSON structure and field values.
"""

from typing import Any

# Response validation per agent type
AGENT_VALIDATION_RULES: dict[str, dict[str, Any]] = {
    "technical_analyst": {
        "required_fields": ["signal", "confidence", "rationale"],
        "valid_signals": ["BUY", "SELL", "HOLD"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence"],
        "field_types": {
            "signal": str,
            "confidence": (int, float),
            "rationale": str,
            "indicator_validations": dict,
        },
    },
    "sentiment_analyst": {
        "required_fields": ["overall_sentiment", "confidence_score"],
        "valid_sentiments": ["POSITIVE", "NEGATIVE", "NEUTRAL"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence_score"],
        "field_types": {
            "overall_sentiment": str,
            "confidence_score": (int, float),
        },
    },
    "visual_analyst": {
        "required_fields": ["action", "confidence", "reason"],
        "valid_actions": ["BUY", "SELL", "HOLD"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence"],
        "field_types": {
            "action": str,
            "confidence": (int, float),
            "reason": str,
            "chart_path": (str, type(None)),
        },
    },
    "qabba_analyst": {
        "required_fields": ["signal", "confidence", "rationale"],
        "valid_signals": ["BUY", "SELL", "HOLD"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence"],
        "field_types": {
            "signal": str,
            "confidence": (int, float),
            "rationale": str,
            "qabba_scores": (dict, type(None)),
            "dynamic_levels": (dict, type(None)),
        },
    },
    "decision_agent": {
        "required_fields": ["final_decision", "confidence_in_decision", "combined_reasoning"],
        "valid_decisions": ["BUY", "SELL", "HOLD"],
        "valid_confidence": ["HIGH", "MEDIUM", "LOW"],
        "field_types": {
            "final_decision": str,
            "confidence_in_decision": str,
            "combined_reasoning": str,
            "key_conflicting_signals": list,
            "risk_assessment": dict,
            "_directional_score": ((int, float), type(None)),
            "_directional_score_source": (str, type(None)),
            "_directional_agent_votes": list,
        },
    },
    "risk_manager": {
        "required_fields": ["verdict", "risk_score"],
        "valid_verdicts": ["APPROVE", "APPROVE_REDUCED", "VETO", "DELAY"],
        "risk_score_range": (0, 10),
        "numeric_fields": ["risk_score"],
        "field_types": {
            "verdict": str,
            "risk_score": (int, float),
            "reasoning": str,
        },
    },
    "web3_intel": {
        "required_fields": ["signal", "confidence", "rationale"],
        "valid_signals": ["BUY", "SELL", "HOLD"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence"],
        "field_types": {
            "signal": str,
            "confidence": (int, float),
            "rationale": str,
            "smart_money_bias": str,
            "social_hype_level": str,
            "web3_risk_flags": list,
        },
    },
}


class ResponseValidationError(Exception):
    """Exception raised when a response fails validation."""

    def __init__(self, errors: list[str], raw_response: str = ""):
        self.errors = errors
        self.raw_response = raw_response
        super().__init__(f"Validation failed: {'; '.join(errors)}")


def validate_agent_response(agent_type: str, response: dict[str, Any]) -> list[str]:
    """
    Validates a response against agent rules.

    Returns:
        List of found errors (empty if valid)
    """
    errors: list[str] = []

    rules = AGENT_VALIDATION_RULES.get(agent_type)
    if not rules:
        return [f"No validation rules defined for agent type: {agent_type}"]

    # 1. Verify required fields
    for field in rules.get("required_fields", []):
        if field not in response:
            errors.append(f"Missing required field: '{field}'")
        elif response[field] is None:
            errors.append(f"Required field '{field}' is null")

    # 2. Validate allowed signals/actions
    signal_field = None
    if "valid_signals" in rules:
        signal_field = "signal"
    elif "valid_actions" in rules:
        signal_field = "action"
    elif "valid_decisions" in rules:
        signal_field = "final_decision"
    elif "valid_verdicts" in rules:
        signal_field = "verdict"
    elif "valid_sentiments" in rules:
        signal_field = "overall_sentiment"

    if signal_field and signal_field in response:
        valid_values = (
            rules.get("valid_signals")
            or rules.get("valid_actions")
            or rules.get("valid_decisions")
            or rules.get("valid_verdicts")
            or rules.get("valid_sentiments")
        )
        if valid_values:
            value = str(response[signal_field]).upper().strip()
            if value not in [v.upper() for v in valid_values]:
                errors.append(
                    f"Invalid '{signal_field}': '{response[signal_field]}'. "
                    f"Must be one of: {valid_values}"
                )

    # 3. Validate confidence
    if "confidence_in_decision" in response:
        confidence_field = "confidence_in_decision"
        valid_conf = rules.get("valid_confidence")
        conf_val = response.get(confidence_field)
        conf_norm = str(conf_val).upper().strip() if conf_val is not None else ""
        if valid_conf and conf_norm not in [v.upper() for v in valid_conf]:
            errors.append(
                f"Invalid '{confidence_field}': '{response[confidence_field]}'. "
                f"Must be one of: {valid_conf}"
            )

    # 4. Validate numeric ranges
    if "confidence_range" in rules:
        for field in ["confidence", "confidence_score", "convergence_score"]:
            if field in response:
                try:
                    val = float(response[field])
                    min_v, max_v = rules["confidence_range"]
                    if not (min_v <= val <= max_v):
                        errors.append(f"'{field}' value {val} out of range [{min_v}, {max_v}]")
                except (TypeError, ValueError):
                    errors.append(f"'{field}' must be numeric, got: {response[field]}")

    if "risk_score_range" in rules and "risk_score" in response:
        try:
            val = float(response["risk_score"])
            min_v, max_v = rules["risk_score_range"]
            if not (min_v <= val <= max_v):
                errors.append(f"'risk_score' value {val} out of range [{min_v}, {max_v}]")
        except (TypeError, ValueError):
            errors.append(f"'risk_score' must be numeric, got: {response['risk_score']}")

    # 5. Validate field types
    for field, expected_type in rules.get("field_types", {}).items():
        if field in response and response[field] is not None:
            value = response[field]
            if expected_type == bool and not isinstance(value, bool):
                # Accept strings "true"/"false" for booleans
                if isinstance(value, str):
                    if value.lower() not in ("true", "false"):
                        errors.append(f"'{field}' must be boolean, got: {value}")
                else:
                    errors.append(f"'{field}' must be boolean, got: {type(value).__name__}")
            elif expected_type == str and not isinstance(value, str):
                errors.append(f"'{field}' must be string, got: {type(value).__name__}")
            elif expected_type == (int, float):
                if not isinstance(value, (int, float)):
                    try:
                        float(value)  # Try to convert
                    except (TypeError, ValueError):
                        errors.append(f"'{field}' must be numeric, got: {type(value).__name__}")

    return errors


def build_validation_feedback(agent_type: str, errors: list[str], attempt: int) -> str:
    """
    Builds a feedback message for the LLM when validation fails.

    Args:
        agent_type: Agent type
        errors: List of errors found
        attempt: Current attempt number

    Returns:
        Feedback message to include in retry prompt
    """
    rules = AGENT_VALIDATION_RULES.get(agent_type, {})

    feedback_parts = [
        f"⚠️ VALIDATION FAILED (Attempt {attempt}/3)",
        "",
        "ERRORS FOUND:",
    ]

    for i, error in enumerate(errors, 1):
        feedback_parts.append(f"  {i}. {error}")

    feedback_parts.extend(
        [
            "",
            "CRITICAL OUTPUT REQUIREMENTS:",
            "- Your ENTIRE response must be a single JSON object - NOTHING ELSE",
            "- Do NOT use markdown code blocks (no ```json or ```)",
            "- Do NOT include any text before or after the JSON",
            "- Do NOT add explanations or comments outside the JSON",
            "- The JSON must start with { and end with }",
            "- All required fields must be present",
            "- All numeric values must be valid numbers (not null, not strings)",
        ]
    )

    # Adding specific agent reminders
    if "valid_signals" in rules:
        feedback_parts.append(f"- Signal field must be exactly one of: {rules['valid_signals']}")
    if "valid_actions" in rules:
        feedback_parts.append(f"- Action field must be exactly one of: {rules['valid_actions']}")
    if "valid_decisions" in rules:
        feedback_parts.append(
            f"- Final decision field must be exactly one of: {rules['valid_decisions']}"
        )
    if "valid_verdicts" in rules:
        feedback_parts.append(f"- Verdict field must be exactly one of: {rules['valid_verdicts']}")
    if "valid_confidence" in rules:
        feedback_parts.append(
            f"- Confidence field must be exactly one of: {rules['valid_confidence']}"
        )
    if "confidence_range" in rules:
        feedback_parts.append(
            f"- Confidence score must be between {rules['confidence_range'][0]} and {rules['confidence_range'][1]}"
        )
    if "valid_sentiments" in rules:
        feedback_parts.append(
            f"- Sentiment field must be exactly one of: {rules['valid_sentiments']}"
        )

    feedback_parts.extend(
        [
            "",
            "EXAMPLE OF CORRECT OUTPUT:",
            '{"field1": "value1", "field2": 123}',
            "",
            "INCORRECT OUTPUTS (DO NOT DO THIS):",
            '- ```json {"field1": "value1"} ```',
            '- Here is my analysis: {"field1": "value1"}',
            '- {"field1": "value1"} (with extra text after)',
            "",
            "Retry with ONLY the corrected JSON object.",
        ]
    )

    return "\n".join(feedback_parts)
