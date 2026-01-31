"""
LangGraph Orchestrator for Fenix Trading Bot.

Implements an agent graph using LangGraph.
Features:
- Specialized agents: technical, sentiment, visual, QABBA, final decision
- Multi-provider LLM support
- Dynamic agent weighting
- Integrated risk system
- Multi-timeframe context
- Integration with ReasoningBank for persistence

V3.0 FEATURES - Retry and Validation System:
- Structured JSON response validation per agent rules
- Retry system with exponential backoff (up to 3 retries)
- Automatic feedback to LLM when validation fails
- Retry statistics and success rate per agent
- All agents include metadata: _attempts, _validation_errors

Validation rules per agent:
- technical_analyst: signal ∈ {BUY,SELL,HOLD}, confidence ∈ {HIGH,MEDIUM,LOW}
- sentiment_analyst: overall_sentiment ∈ {POSITIVE,NEGATIVE,NEUTRAL}, confidence_score ∈ [0,1]
- visual_analyst: action ∈ {BUY,SELL,HOLD}, trend_direction ∈ {bullish,bearish,neutral}
- qabba_analyst: signal ∈ {BUY_QABBA,SELL_QABBA,HOLD_QABBA}, order_flow_bias ∈ {buying,selling,neutral}
- decision_agent: final_decision ∈ {BUY,SELL,HOLD}, confidence ∈ {HIGH,MEDIUM,LOW}
- risk_manager: verdict ∈ {APPROVE,APPROVE_REDUCED,VETO,DELAY}, risk_score ∈ [0,10]
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Any, TypedDict, Annotated
from dataclasses import dataclass, field

# LangGraph imports
try:
    from langgraph.graph import END, START, StateGraph
    from langgraph.checkpoint.memory import MemorySaver
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None
    START = None

# LangChain imports
try:
    from langchain_core.messages import HumanMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

# Local imports
from src.prompts.agent_prompts import format_prompt
from config.llm_provider_config import LLMProvidersConfig, AgentProviderConfig
from src.inference.reasoning_judge import ReasoningLLMJudge, ReasoningJudgePayload  # Integration
from src.config.judge_config import get_judge_model_config   # Integration
from src.system.tracing import get_tracer

# ReasoningBank integration
try:
    from src.memory.reasoning_bank import get_reasoning_bank
    REASONING_BANK_AVAILABLE = True
except ImportError:
    REASONING_BANK_AVAILABLE = False
    get_reasoning_bank = None

# Dashboard integration
try:
    from src.dashboard.trading_dashboard import get_dashboard
    DASHBOARD_AVAILABLE = True
except ImportError:
    DASHBOARD_AVAILABLE = False
    get_dashboard = None

logger = logging.getLogger(__name__)


# ============================================================================
# RESPONSE VALIDATION SYSTEM
# ============================================================================

class ResponseValidationError(Exception):
    """Exception raised when a response fails validation."""
    def __init__(self, errors: list[str], raw_response: str = ""):
        self.errors = errors
        self.raw_response = raw_response
        super().__init__(f"Validation failed: {'; '.join(errors)}")


# Response validation per agent type
AGENT_VALIDATION_RULES: dict[str, dict[str, Any]] = {
    "technical_analyst": {
        "required_fields": ["signal", "confidence_level", "reasoning"],
        "valid_signals": ["BUY", "SELL", "HOLD"],
        "valid_confidence": ["HIGH", "MEDIUM", "LOW"],
        "numeric_fields": ["support_level", "resistance_level", "risk_reward_ratio"],
        "field_types": {
            "signal": str,
            "confidence_level": str,
            "reasoning": str,
            "support_level": (int, float),
            "resistance_level": (int, float),
            "risk_reward_ratio": (int, float),
        }
    },
    "sentiment_analyst": {
        "required_fields": ["overall_sentiment", "confidence_score"],
        "valid_sentiments": ["POSITIVE", "NEGATIVE", "NEUTRAL"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence_score"],
        "field_types": {
            "overall_sentiment": str,
            "confidence_score": (int, float),
        }
    },
    "visual_analyst": {
        "required_fields": ["action", "confidence", "trend_direction"],
        "valid_actions": ["BUY", "SELL", "HOLD"],
        "valid_trends": ["bullish", "bearish", "neutral"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["confidence"],
        "field_types": {
            "action": str,
            "confidence": (int, float),
            "trend_direction": str,
            "pattern_identified": str,
        }
    },
    "qabba_analyst": {
        "required_fields": ["signal", "qabba_confidence", "order_flow_bias", "absorption_detected"],
        "valid_signals": ["BUY_QABBA", "SELL_QABBA", "HOLD_QABBA"],
        "valid_flows": ["buying", "selling", "neutral"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["qabba_confidence"],
        "field_types": {
            "signal": str,
            "qabba_confidence": (int, float),
            "order_flow_bias": str,
            "absorption_detected": bool,
        }
    },
    "decision_agent": {
        "required_fields": ["final_decision", "confidence_in_decision", "convergence_score"],
        "valid_decisions": ["BUY", "SELL", "HOLD"],
        "valid_confidence": ["HIGH", "MEDIUM", "LOW"],
        "confidence_range": (0.0, 1.0),
        "numeric_fields": ["convergence_score"],
        "field_types": {
            "final_decision": str,
            "confidence_in_decision": str,
            "convergence_score": (int, float),
            "combined_reasoning": str,
        }
    },
    "risk_manager": {
        "required_fields": ["verdict", "risk_score"],
        "valid_verdicts": ["APPROVE", "APPROVE_REDUCED", "VETO", "DELAY"],
        "risk_range": (0.0, 10.0),
        "numeric_fields": ["risk_score"],
        "field_types": {
            "verdict": str,
            "risk_score": (int, float),
            "reason": str,
        }
    },
}


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
        valid_values = rules.get("valid_signals") or rules.get("valid_actions") or \
                      rules.get("valid_decisions") or rules.get("valid_verdicts") or \
                      rules.get("valid_sentiments")
        if valid_values:
            value = str(response[signal_field]).upper().strip()
            if value not in [v.upper() for v in valid_values]:
                errors.append(
                    f"Invalid '{signal_field}': '{response[signal_field]}'. "
                    f"Must be one of: {valid_values}"
                )

    # 3. Validate confidence
    confidence_field = None
    if "confidence_level" in response:
        confidence_field = "confidence_level"
        valid_conf = rules.get("valid_confidence")
        if valid_conf and response[confidence_field] not in valid_conf:
            errors.append(
                f"Invalid '{confidence_field}': '{response[confidence_field]}'. "
                f"Must be one of: {valid_conf}"
            )
    elif "confidence_in_decision" in response:
        confidence_field = "confidence_in_decision"
        valid_conf = rules.get("valid_confidence")
        if valid_conf and response[confidence_field] not in valid_conf:
            errors.append(
                f"Invalid '{confidence_field}': '{response[confidence_field]}'. "
                f"Must be one of: {valid_conf}"
            )

    # 4. Validate numeric ranges
    if "confidence_range" in rules:
        for field in ["confidence", "confidence_score", "qabba_confidence", "convergence_score"]:
            if field in response:
                try:
                    val = float(response[field])
                    min_v, max_v = rules["confidence_range"]
                    if not (min_v <= val <= max_v):
                        errors.append(
                            f"'{field}' value {val} out of range [{min_v}, {max_v}]"
                        )
                except (TypeError, ValueError):
                    errors.append(f"'{field}' must be numeric, got: {response[field]}")

    if "risk_range" in rules and "risk_score" in response:
        try:
            val = float(response["risk_score"])
            min_v, max_v = rules["risk_range"]
            if not (min_v <= val <= max_v):
                errors.append(
                    f"'risk_score' value {val} out of range [{min_v}, {max_v}]"
                )
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

    # 6. Validate additional specific fields
    if agent_type == "qabba_analyst" and "order_flow_bias" in response:
        valid_flows = rules.get("valid_flows", [])
        if response["order_flow_bias"] not in valid_flows:
            errors.append(
                f"Invalid 'order_flow_bias': '{response['order_flow_bias']}'. "
                f"Must be one of: {valid_flows}"
            )

    if agent_type == "visual_analyst" and "trend_direction" in response:
        valid_trends = rules.get("valid_trends", [])
        if response["trend_direction"] not in valid_trends:
            errors.append(
                f"Invalid 'trend_direction': '{response['trend_direction']}'. "
                f"Must be one of: {valid_trends}"
            )

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
        "The following errors were found in your response:",
    ]

    for i, error in enumerate(errors, 1):
        feedback_parts.append(f"  {i}. {error}")

    feedback_parts.extend([
        "",
        "CORRECTION INSTRUCTIONS:",
        "- Ensure your response is VALID JSON only (no markdown, no code blocks)",
        "- Fix all errors listed above",
    ])

    # Adding specific agent reminders
    if "valid_signals" in rules:
        feedback_parts.append(f"- Signal must be exactly one of: {rules['valid_signals']}")
    if "valid_actions" in rules:
        feedback_parts.append(f"- Action must be exactly one of: {rules['valid_actions']}")
    if "valid_decisions" in rules:
        feedback_parts.append(f"- Decision must be exactly one of: {rules['valid_decisions']}")
    if "valid_verdicts" in rules:
        feedback_parts.append(f"- Verdict must be exactly one of: {rules['valid_verdicts']}")
    if "valid_confidence" in rules:
        feedback_parts.append(f"- Confidence must be exactly one of: {rules['valid_confidence']}")
    if "confidence_range" in rules:
        feedback_parts.append(f"- Confidence score must be between {rules['confidence_range'][0]} and {rules['confidence_range'][1]}")
    if "valid_sentiments" in rules:
        feedback_parts.append(f"- Sentiment must be exactly one of: {rules['valid_sentiments']}")

    feedback_parts.extend([
        "",
        "Retry with a corrected JSON response following ALL rules.",
    ])

    return "\n".join(feedback_parts)


# ============================================================================
# RETRY SYSTEM WITH EXPONENTIAL BACKOFF
# ============================================================================

@dataclass
class RetryStats:
    """Retry statistics per agent."""
    agent_type: str
    total_attempts: int = 0
    successful_first_try: int = 0
    retries_needed: int = 0
    failures: int = 0
    validation_errors: dict[str, int] = field(default_factory=dict)

    def record_attempt(self, success: bool, retry_count: int, validation_errors: list[str] = None):
        """Records an attempt."""
        self.total_attempts += 1
        if success:
            if retry_count == 0:
                self.successful_first_try += 1
            else:
                self.retries_needed += 1
        else:
            self.failures += 1

        # Count validation errors
        if validation_errors:
            for error in validation_errors:
                error_type = error.split(":")[0] if ":" in error else "unknown"
                self.validation_errors[error_type] = self.validation_errors.get(error_type, 0) + 1

    @property
    def success_rate(self) -> float:
        """Success rate (0.0 - 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return (self.successful_first_try + self.retries_needed) / self.total_attempts

    @property
    def retry_rate(self) -> float:
        """Retry rate needed (0.0 - 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return self.retries_needed / self.total_attempts


# Global retry statistics
_retry_stats: dict[str, RetryStats] = {}


def get_retry_stats(agent_type: str | None = None) -> RetryStats | dict[str, RetryStats]:
    """Gets retry statistics."""
    if agent_type:
        return _retry_stats.get(agent_type, RetryStats(agent_type))
    return _retry_stats.copy()


def reset_retry_stats():
    """Resets retry statistics."""
    global _retry_stats
    _retry_stats = {}


async def invoke_with_retry_and_validation(
    llm: Any,
    messages: list,
    agent_type: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    required_keys: list[str] | None = None,
) -> tuple[dict[str, Any], int, list[str]]:
    """
    Invokes the LLM with retry and validation system.

    Args:
        llm: LLM Instance
        messages: List of messages for the LLM
        agent_type: Agent type (for validation)
        max_retries: Maximum number of retries
        base_delay: Base delay for exponential backoff
        required_keys: Additional required fields

    Returns:
        Tuple of (parsed_response, attempts_made, errors_list)
    """
    if agent_type not in _retry_stats:
        _retry_stats[agent_type] = RetryStats(agent_type)

    stats = _retry_stats[agent_type]
    last_errors: list[str] = []

    for attempt in range(max_retries + 1):
        try:
            # Add exponential delay after first attempt
            if attempt > 0:
                delay = base_delay * (2 ** (attempt - 1))  # 1s, 2s, 4s
                logger.info(f"⏳ Retry {attempt}/{max_retries} for {agent_type}: waiting {delay}s...")
                await asyncio.sleep(delay)

            # Invoke LLM
            response = await llm.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)

            # Extract JSON
            parsed = _extract_json_from_content(content, required_keys=required_keys)

            if parsed is None:
                last_errors = ["Failed to extract valid JSON from response"]
                if attempt < max_retries:
                    # Add parsing error feedback
                    feedback = f"""
⚠️ JSON PARSE ERROR (Attempt {attempt + 1}/{max_retries + 1})

Your response could not be parsed as valid JSON.

RAW RESPONSE PREVIEW:
{content[:500]}...

CORRECTION INSTRUCTIONS:
- Ensure your response is VALID JSON only
- No markdown code blocks (no ```json)
- No extra text before or after JSON
- Check for syntax errors (missing quotes, commas, brackets)

Retry with valid JSON.
"""
                    messages = messages + [{"role": "user", "content": feedback}]
                    continue
                else:
                    stats.record_attempt(False, attempt, last_errors)
                    return {
                        "error": "Failed to parse JSON after all retries",
                        "raw_response": content[:1000],
                        "parse_error": True
                    }, attempt + 1, last_errors

            # Validar respuesta
            validation_errors = validate_agent_response(agent_type, parsed)

            if not validation_errors:
                # Success!
                stats.record_attempt(True, attempt)
                logger.info(f"✅ {agent_type}: Valid response on attempt {attempt + 1}")
                return parsed, attempt + 1, []

            # Validation failed
            last_errors = validation_errors
            logger.warning(f"⚠️ {agent_type}: Validation failed on attempt {attempt + 1}: {validation_errors}")

            if attempt < max_retries:
                # Build feedback and retry
                feedback = build_validation_feedback(agent_type, validation_errors, attempt + 1)
                messages = messages + [{"role": "user", "content": feedback}]
            else:
                # Retries exhausted
                stats.record_attempt(False, attempt, validation_errors)
                logger.error(f"❌ {agent_type}: All {max_retries + 1} attempts failed")

                # Return best possible response with error metadata
                parsed["_validation_errors"] = validation_errors
                parsed["_validation_failed"] = True
                parsed["_attempts"] = attempt + 1
                return parsed, attempt + 1, validation_errors

        except Exception as e:
            last_errors = [f"Exception during invocation: {str(e)}"]
            logger.error(f"❌ {agent_type}: Exception on attempt {attempt + 1}: {e}")

            if attempt >= max_retries:
                stats.record_attempt(False, attempt, last_errors)
                return {
                    "error": str(e),
                    "exception": True,
                    "attempts": attempt + 1
                }, attempt + 1, last_errors

    # Should never reach here, but for safety
    stats.record_attempt(False, max_retries, last_errors)
    return {"error": "Unexpected retry loop exit"}, max_retries + 1, last_errors


def log_retry_stats():
    """Log retry statistics."""
    logger.info("=" * 60)
    logger.info("RETRY SYSTEM STATISTICS")
    logger.info("=" * 60)

    total_attempts = sum(s.total_attempts for s in _retry_stats.values())
    if total_attempts == 0:
        logger.info("No attempts recorded yet.")
        return

    for agent_type, stats in _retry_stats.items():
        if stats.total_attempts == 0:
            continue

        logger.info(f"\n{agent_type.upper()}:")
        logger.info(f"  Total attempts: {stats.total_attempts}")
        logger.info(f"  Success rate: {stats.success_rate:.1%}")
        logger.info(f"  First-try success: {stats.successful_first_try}")
        logger.info(f"  Retries needed: {stats.retries_needed}")
        logger.info(f"  Failures: {stats.failures}")

        if stats.validation_errors:
            logger.info(f"  Most common validation errors:")
            sorted_errors = sorted(stats.validation_errors.items(), key=lambda x: x[1], reverse=True)
            for error_type, count in sorted_errors[:3]:
                logger.info(f"    - {error_type}: {count} occurrences")

    logger.info("=" * 60)


# ============================================================================
# LOGGING HELPER
# ============================================================================

def save_legacy_agent_log(
    agent_name: str,
    prompt: list[dict[str, str]] | list[Any],
    response_content: str,
    parsed_json: dict | None,
):
    """
    Saves detailed legacy-style logs (input/output/prompt/raw) to src/logs/llm_responses.
    Useful for debugging and detailed analysis.
    """
    try:
        # Base directory - use absolute path based on script location
        project_root = Path(__file__).parent.parent.parent
        base_dir = project_root / "src" / "logs" / "llm_responses" / agent_name
        base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📝 Saving agent log to: {base_dir}")
        
        # Unique timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"{timestamp}"
        
        # Save prompt (raw list of messages)
        prompt_path = base_dir / f"{base_name}_prompt.txt"
        with open(prompt_path, "w", encoding="utf-8") as f:
            for i, msg in enumerate(prompt):
                content = getattr(msg, "content", str(msg))
                role = getattr(msg, "type", "unknown")
                f.write(f"--- Message {i} ({role}) ---\n{content}\n\n")
        
        # Save structured input (if possible to extract from prompt)
        # This is harder to reconstruct generically, but we save the full prompt
        
        # Save raw response
        raw_path = base_dir / f"{base_name}_raw_response.txt"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response_content)
            
        # Save output JSON
        if parsed_json:
            output_path = base_dir / f"{base_name}_output.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(parsed_json, f, indent=2, ensure_ascii=False)
                
    except Exception as e:
        logger.warning(f"Error saving legacy log for {agent_name}: {e}")


def _extract_json_from_content(
    content: str,
    required_keys: list[str] | None = None,
) -> dict | None:
    """Extracts the last valid JSON from LLM content."""
    if not content:
        return None

    text = content.strip()
    if "...done thinking" in text:
        parts = text.split("...done thinking")
        if len(parts) > 1:
            text = parts[-1].strip().lstrip(".").strip()

    # Try direct parse first
    def _sanitize_json(candidate: str) -> str:
        """Escapes newlines within strings to improve parsing."""
        result = []
        in_string = False
        escape = False
        for ch in candidate:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == "\\":
                result.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch in ("\n", "\r"):
                result.append("\\n")
                continue
            result.append(ch)
        return "".join(result)

    # Try direct parse first
    try:
        parsed = json.loads(text)
        if not required_keys or all(key in parsed for key in required_keys):
            return parsed
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_sanitize_json(text))
            if not required_keys or all(key in parsed for key in required_keys):
                return parsed
        except json.JSONDecodeError:
            pass

    # Try fenced JSON blocks
    for pattern in (r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            try:
                parsed = json.loads(candidate)
                if not required_keys or all(key in parsed for key in required_keys):
                    return parsed
            except json.JSONDecodeError:
                try:
                    parsed = json.loads(_sanitize_json(candidate))
                    if not required_keys or all(key in parsed for key in required_keys):
                        return parsed
                except json.JSONDecodeError:
                    continue

    # Find all balanced JSON objects and prefer the last valid one
    def _find_json_objects(source: str) -> list[str]:
        objects: list[str] = []
        depth = 0
        start = None
        for i, char in enumerate(source):
            if char == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(source[start:i + 1])
                    start = None
        return objects

    candidates = _find_json_objects(text)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
            if not required_keys or all(key in parsed for key in required_keys):
                return parsed
        except json.JSONDecodeError:
            try:
                parsed = json.loads(_sanitize_json(candidate))
                if not required_keys or all(key in parsed for key in required_keys):
                    return parsed
            except json.JSONDecodeError:
                continue

    return None

# ============================================================================
# REASONING BANK HELPER
# ============================================================================

def get_agent_context_from_bank(
    reasoning_bank: Any | None,
    agent_name: str,
    current_prompt: str,
    limit: int = 3
) -> str:
    """
    Gets relevant historical context from ReasoningBank.
    
    Searches for similar past decisions to inform the current agent.
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return ""
    
    try:
        # Search for relevant entries
        relevant = reasoning_bank.get_relevant_context(
            agent_name=agent_name,
            current_prompt=current_prompt,
            limit=limit,
        )
        
        if not relevant:
            return ""
        
        context_parts = ["### Historical Context (similar past decisions):"]
        for entry in relevant:
            success_status = ""
            if entry.success is not None:
                success_status = " ✓" if entry.success else " ✗"
            
            context_parts.append(
                f"- [{entry.action}{success_status}] Conf: {entry.confidence:.0%} | "
                f"{entry.reasoning[:100]}..."
            )
        
        return "\n".join(context_parts)
    except Exception as e:
        logger.warning("Error getting context from ReasoningBank: %s", e)
        return ""


def store_agent_decision(
    reasoning_bank: Any | None,
    agent_name: str,
    prompt: str,
    result: dict,
    raw_response: str,
    backend: str,
    latency_ms: float,
) -> str | None:
    """
    Stores the agent decision in ReasoningBank.
    
    Returns:
        prompt_digest for later tracking
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return None
    
    try:
        entry = reasoning_bank.store_entry(
            agent_name=agent_name,
            prompt=prompt,
            normalized_result=result,
            raw_response=raw_response,
            backend=backend,
            latency_ms=latency_ms,
            metadata={
                "source": "langgraph_orchestrator",
                "timestamp": datetime.now().isoformat(),
            }
        )
        return entry.prompt_digest
    except Exception as e:
        logger.warning("Error storing in ReasoningBank: %s", e)
        return None


# ============================================================================
# STATE DEFINITION
# ============================================================================

def merge_dicts(a: dict, b: dict) -> dict:
    """Merges two dictionaries (for execution_times)."""
    return {**a, **b}


def append_lists(a: list, b: list) -> list:
    """Concatenates two lists (for errors and messages)."""
    return a + b


class FenixAgentState(TypedDict, total=False):
    """Shared state between all graph agents."""
    # Identifiers
    symbol: str
    timeframe: str
    timestamp: str
    
    # Market Data
    kline_data: dict[str, list]
    current_price: float
    current_volume: float
    
    # Technical Indicators
    indicators: dict[str, Any]
    mtf_context: dict[str, Any]
    
    # Microstructure
    obi: float
    cvd: float
    spread: float
    orderbook_depth: dict[str, float]
    
    # Generated Chart
    chart_image_b64: str | None
    chart_indicators_summary: dict[str, Any]
    
    # News data for sentiment agent
    news_data: list[dict[str, Any]]
    # Social data & metrics (Twitter/Reddit/fear_greed)
    social_data: dict[str, Any]
    fear_greed_value: str | None
    
    # Agent Results (each writes to its own field)
    technical_report: dict[str, Any]
    sentiment_report: dict[str, Any]
    visual_report: dict[str, Any]
    qabba_report: dict[str, Any]
    
    # Decision and Risk
    decision_report: dict[str, Any]
    risk_assessment: dict[str, Any]
    final_trade_decision: dict[str, Any]
    
    # Metadata - Using Annotated to allow multiple writes
    messages: Annotated[list[Any], append_lists]
    errors: Annotated[list[str], append_lists]
    execution_times: Annotated[dict[str, float], merge_dicts]


# ============================================================================
# LLM FACTORY
# ============================================================================

class LLMFactory:
    """LLM Factory supporting multiple providers."""
    
    def __init__(self, config: LLMProvidersConfig | None = None):
        # If no explicit config is passed, attempt to use the LLMProviderLoader
        if config is None:
            try:
                from src.config.llm_provider_loader import get_provider_loader
                loader = get_provider_loader()
                config = loader.get_config() or LLMProvidersConfig()
                logger.info(f"LLMFactory: using config from provider loader (profile={loader.active_profile})")
            except Exception as e:
                logger.warning(f"LLMFactory: could not initialize loader config, falling back to default. Error: {e}")

        self.config = config or LLMProvidersConfig()
        self._llm_cache: dict[str, Any] = {}
    
    def get_llm_for_agent(self, agent_type: str) -> Any:
        """Gets the configured LLM for an agent type."""
        if agent_type in self._llm_cache:
            return self._llm_cache[agent_type]
        
        agent_config = self.config.get_agent_config(agent_type)
        logger.info(f"🏭 LLMFactory: Creating LLM for {agent_type} with provider={agent_config.provider_type}, model={agent_config.model_name}")
        llm = self._create_llm(agent_config)
        self._llm_cache[agent_type] = llm
        return llm
    
    def _create_llm(self, config: AgentProviderConfig) -> Any:
        """Creates an LLM instance based on configuration."""
        provider = config.provider_type
        model = config.model_name
        temperature = config.temperature
        api_key = config.api_key.get_secret_value() if config.api_key else None
        api_base = config.api_base
        
        try:
            if provider == "openai":
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=config.max_tokens,
                    timeout=config.timeout,
                )
            
            elif provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=config.max_tokens,
                )
            
            elif provider == "groq":
                from langchain_groq import ChatGroq
                return ChatGroq(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=config.max_tokens,
                )
            
            elif provider in ("ollama_local", "ollama_cloud"):
                from langchain_ollama import ChatOllama
                return ChatOllama(
                    model=model,
                    temperature=temperature,
                    base_url=api_base or "http://localhost:11434",
                    num_predict=config.max_tokens,
                )
            
            elif provider == "huggingface_inference":
                from langchain_huggingface import ChatHuggingFace
                from langchain_huggingface import HuggingFaceEndpoint
                endpoint = HuggingFaceEndpoint(
                    repo_id=model,
                    huggingfacehub_api_token=api_key,
                    max_new_tokens=config.max_tokens,
                    temperature=temperature,
                )
                return ChatHuggingFace(llm=endpoint)
            
            else:
                logger.warning(f"Provider {provider} not supported, using local Ollama")
                from langchain_ollama import ChatOllama
                return ChatOllama(model="qwen2.5:7b", temperature=0.1)
                
        except ImportError as e:
            # If the provider package is not installed (e.g., langchain_groq), attempt fallback immediately
            logger.warning("Provider import failed: %s - attempting fallback if configured", str(e))
            # Attempt fallback if configured
            if config.fallback_provider_type and config.fallback_model_name:
                fallback_config = AgentProviderConfig(
                    provider_type=config.fallback_provider_type,
                    model_name=config.fallback_model_name,
                    temperature=config.temperature,
                )
                logger.info("Attempting fallback from ImportError: %s/%s", config.fallback_provider_type, config.fallback_model_name)
                return self._create_llm(fallback_config)
            # If no fallback, fall through to generic handler below which may return a stub
            # (generic exception handler will run)
            raise
        except Exception as e:
            logger.error(f"Error creando LLM para {provider}/{model}: {e}")
            # Attempt fallback if configured
            if config.fallback_provider_type and config.fallback_model_name:
                fallback_config = AgentProviderConfig(
                    provider_type=config.fallback_provider_type,
                    model_name=config.fallback_model_name,
                    temperature=config.temperature,
                )
                logger.info(f"Attempting fallback: {config.fallback_provider_type}/{config.fallback_model_name}")
                return self._create_llm(fallback_config)
            # If fallback fails, return a StubLLM to avoid breaking the graph
            allow_stub = os.getenv("LLM_ALLOW_NOOP_STUB", "1") == "1"
            if allow_stub:
                logger.warning("Returning NoopStub LLM to allow graph initialization in dev/test")

                class NoopStub:
                    def __init__(self, name="noop"):
                        self.name = name

                    def invoke(self, messages):
                        # Return a harmless default JSON content matching expected schema
                        return type("R", (), {
                            "content": '{"action": "HOLD", "confidence": 0.0, "reason": "LLM unavailable (stub)"}'
                        })

                    def generate(self, prompt, **kwargs):
                        return type("R", (), {
                            "success": True,
                            "content": '{"action": "HOLD", "confidence": 0.0, "reason": "LLM unavailable (stub)"}',
                            "model": "noop",
                            "provider": "noop",
                            "latency_ms": 0,
                        })

                return NoopStub(name=f"noop_{provider}")
            raise


# ============================================================================
# AGENT NODES
# ============================================================================

def create_technical_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the technical agent node with retry and validation system."""
    async def technical_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            # Prepare indicators as JSON
            indicators = state.get("indicators", {})
            mtf_context = state.get("mtf_context", {})

            # Format prompt
            messages = format_prompt(
                "technical_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                timeframe=state.get("timeframe", "15m"),
                indicators_json=json.dumps(indicators, indent=2, default=str),
                htf_context=json.dumps(mtf_context.get("htf", {}), default=str),
                ltf_context=json.dumps(mtf_context.get("ltf", {}), default=str),
                current_price=str(state.get("current_price", "N/A")),
                current_volume=str(state.get("current_volume", "N/A")),
            )

            if not messages:
                raise ValueError("Could not format technical prompt")

            # Convertir a formato de mensajes para el sistema de reintentos
            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Invocar con reintentos y validación
            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="technical_analyst",
                max_retries=3,
                base_delay=1.0,
                required_keys=["signal"],
            )

            # Legacy logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("technical_enhanced", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store result in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    prompt_digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="technical_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=raw_response,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    if prompt_digest:
                        report["_reasoning_digest"] = prompt_digest
            except Exception as e:
                logger.debug(f"Technical ReasoningBank store failed: {e}")

            return {
                "technical_report": report,
                "messages": state.get("messages", []) + [{"role": "assistant", "content": raw_response}],
                "execution_times": {
                    **state.get("execution_times", {}),
                    "technical": elapsed
                },
            }

        except Exception as e:
            logger.error(f"Error in technical agent: {e}")
            return {
                "technical_report": {"signal": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"Technical: {e}"],
            }

    async def traced_technical_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("technical_agent"):
            return await technical_node(state)

    return traced_technical_node


def create_sentiment_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the sentiment agent node with retry and validation system."""
    async def sentiment_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            # Build news summary from state news_data
            news_list = state.get("news_data", [])
            if news_list:
                news_items = [f"- [{n.get('source', 'N/A')}] {n.get('title', 'Untitled')}: {n.get('summary', '')[:100]}..."
                              for n in news_list[:5]]
                news_summary = "\n".join(news_items)
            else:
                news_summary = "No recent news available"

            social_data_json = json.dumps(state.get("social_data", {}), ensure_ascii=False, indent=2)
            fg_value = str(state.get("fear_greed_value", "N/A"))

            twitter_posts = state.get("social_data", {}).get("twitter", {}) or {}
            reddit_posts = state.get("social_data", {}).get("reddit", {}) or {}
            twitter_count = sum(len(v) for v in twitter_posts.values()) if isinstance(twitter_posts, dict) else 0
            reddit_count = sum(len(v) for v in reddit_posts.values()) if isinstance(reddit_posts, dict) else 0

            messages = format_prompt(
                "sentiment_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                news_summary=news_summary,
                social_data=social_data_json,
                fear_greed_value=fg_value,
                additional_context=(f"News were obtained from sources like CoinDesk and Cointelegraph. "
                                    f"Total available articles: {len(news_list)}. "
                                    f"Social: Twitter={twitter_count}, Reddit={reddit_count}, Fear&Greed={fg_value}"),
            )

            if not messages:
                raise ValueError("Could not format sentiment prompt")

            # Convertir a formato de mensajes para el sistema de reintentos
            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Invocar con reintentos y validación
            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="sentiment_analyst",
                max_retries=3,
                base_delay=1.0,
                required_keys=["overall_sentiment"],
            )

            # Legacy logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("sentiment", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Persist sentiment analysis in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="sentiment_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=raw_response,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    if digest:
                        report["_reasoning_digest"] = digest
            except Exception as e:
                logger.debug(f"Sentiment ReasoningBank store failed: {e}")

            return {
                "sentiment_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "sentiment": elapsed
                },
            }

        except Exception as e:
            logger.error(f"Error in sentiment agent: {e}")
            return {
                "sentiment_report": {"overall_sentiment": "NEUTRAL", "error": str(e)},
                "errors": state.get("errors", []) + [f"Sentiment: {e}"],
            }

    async def traced_sentiment_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("sentiment_agent"):
            return await sentiment_node(state)
    return traced_sentiment_node


def create_visual_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the visual agent node with retry and validation system."""
    async def visual_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            chart_b64 = state.get("chart_image_b64")

            logger.info(f"🖼️ Visual Agent: LLM type: {type(llm)}, model: {getattr(llm, 'model', 'unknown')}, base_url: {getattr(llm, 'base_url', 'unknown')}")
            logger.info(f"🖼️ Visual Agent: chart_image_b64 present = {chart_b64 is not None}, length = {len(chart_b64) if chart_b64 else 0}")

            if not chart_b64:
                # No image, basic analysis
                logger.warning("🖼️ Visual Agent: No chart image in state")
                return {
                    "visual_report": {
                        "action": "HOLD",
                        "confidence": 0.5,
                        "reason": "No chart image available",
                        "visual_analysis": "No image provided for visual analysis"
                    },
                }

            # Prepare message with image
            image_prompt = format_prompt(
                "visual_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                timeframe=state.get("timeframe", "15m"),
                candle_count=50,
                visible_indicators="EMA 9/21, Bollinger Bands, SuperTrend",
                current_price=str(state.get("current_price", "N/A")),
                price_range="N/A",
            )

            if not image_prompt:
                raise ValueError("Could not format visual prompt")

            logger.info(f"🖼️ Visual Agent: Sending image ({len(chart_b64)} chars) to vision model...")

            # Create message with image for vision models
            vision_content = [
                {"type": "text", "text": image_prompt[1]["content"]},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{chart_b64}"}
                },
            ]

            # For the visual agent, we use retry system with LangChain messages
            # Note: Validation is done manually afterwards as vision models may have special format
            llm_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(content=vision_content),
            ]

            max_retries = 3
            base_delay = 1.0
            last_errors = []
            report = None

            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.info(f"⏳ Visual Agent retry {attempt}/{max_retries}: waiting {delay}s...")
                        import time
                        await asyncio.sleep(delay)

                    response = await llm.ainvoke(llm_messages)
                    content = response.content

                    logger.info(f"🖼️ Visual Agent: Response received, length = {len(content) if content else 0}")

                    # Parse JSON response
                    report = _extract_json_from_content(content, required_keys=["action"])

                    if report is None:
                        last_errors = ["Failed to extract valid JSON from response"]
                        if attempt < max_retries:
                            feedback = f"""
⚠️ JSON PARSE ERROR (Attempt {attempt + 1}/{max_retries + 1})

Your response could not be parsed as valid JSON.

RAW RESPONSE PREVIEW:
{content[:500]}...

CORRECTION INSTRUCTIONS:
- Ensure your response is VALID JSON only (no markdown code blocks)
- No extra text before or after JSON
- Check for syntax errors

Retry with valid JSON.
"""
                            llm_messages.append(HumanMessage(content=feedback))
                            continue
                        else:
                            report = {
                                "action": "HOLD",
                                "confidence": 0.5,
                                "visual_analysis": content[:1000] if content else "Parse error",
                                "_validation_failed": True,
                                "_validation_errors": last_errors,
                            }
                            break

                    # Validate response
                    validation_errors = validate_agent_response("visual_analyst", report)

                    if not validation_errors:
                        logger.info(f"✅ Visual Agent: Valid response on attempt {attempt + 1}")
                        break

                    last_errors = validation_errors
                    logger.warning(f"⚠️ Visual Agent: Validation failed on attempt {attempt + 1}: {validation_errors}")

                    if attempt < max_retries:
                        feedback = build_validation_feedback("visual_analyst", validation_errors, attempt + 1)
                        llm_messages.append(HumanMessage(content=feedback))
                    else:
                        report["_validation_failed"] = True
                        report["_validation_errors"] = validation_errors

                except Exception as e:
                    last_errors = [f"Exception: {str(e)}"]
                    logger.error(f"❌ Visual Agent: Exception on attempt {attempt + 1}: {e}")
                    if attempt >= max_retries:
                        report = {
                            "action": "HOLD",
                            "confidence": 0.5,
                            "error": str(e),
                            "_exception": True,
                        }

            # Ensure minimum fields
            if report is None:
                report = {"action": "HOLD", "confidence": 0.5, "error": "All attempts failed"}

            if "visual_analysis" not in report:
                report["visual_analysis"] = "Visual analysis completed"

            report["_attempts"] = attempt + 1

            # DEBUG LOGGING
            with open("debug_visual_raw.log", "a") as f:
                f.write(f"\n--- {datetime.now()} ---\n{json.dumps(report, indent=2)}\n----------------\n")

            logger.info(f"🖼️ Visual Agent: Parsed JSON with action={report.get('action')}")

            # Legacy logging - avoid logging base64 image to text file
            log_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(content=f"[IMAGE CONTENT HIDDEN] \nPrompt: {image_prompt[1]['content']}"),
            ]
            save_legacy_agent_log("visual", log_messages, json.dumps(report), report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_elapsed_seconds"] = elapsed

            # Persist visual analysis in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = image_prompt[1]["content"][:500] if image_prompt and len(image_prompt) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="visual_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=json.dumps(report),
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    if digest:
                        report["_reasoning_digest"] = digest
            except Exception as e:
                logger.debug(f"Could not store visual result: {e}")

            logger.info(f"🖼️ Visual Agent: Completed in {elapsed:.2f}s")

            return {
                "visual_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "visual": elapsed
                },
            }

        except Exception as e:
            logger.error(f"Error in visual agent: {e}")
            return {
                "visual_report": {
                    "action": "HOLD",
                    "error": str(e),
                    "visual_analysis": f"Error in visual analysis: {str(e)}"
                },
                "errors": state.get("errors", []) + [f"Visual: {e}"],
            }

    async def traced_visual_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("visual_agent"):
            return await visual_node(state)
    return traced_visual_node


def create_qabba_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the QABBA agent node (microstructure) with retry and validation system."""
    async def qabba_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            messages = format_prompt(
                "qabba_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                obi_value=str(state.get("obi", 1.0)),
                cvd_value=str(state.get("cvd", 0)),
                spread_value=str(state.get("spread", 0.01)),
                bid_depth=str(state.get("orderbook_depth", {}).get("bid_depth", "N/A")),
                ask_depth=str(state.get("orderbook_depth", {}).get("ask_depth", "N/A")),
                total_liquidity=str(state.get("orderbook_depth", {}).get("total", "N/A")),
                recent_trades="[]",
                current_price=str(state.get("current_price", "N/A")),
                technical_context=json.dumps(state.get("indicators", {}), default=str),
            )

            if not messages:
                raise ValueError("Could not format QABBA prompt")

            # Convertir a formato de mensajes para el sistema de reintentos
            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Invocar con reintentos y validación
            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="qabba_analyst",
                max_retries=3,
                base_delay=1.0,
                required_keys=["signal"],
            )

            # Legacy logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("qabba_enhanced", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store QABBA report in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="qabba_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=raw_response,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    if digest:
                        report["_reasoning_digest"] = digest
            except Exception as e:
                logger.debug(f"QABBA store failed: {e}")

            return {
                "qabba_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "qabba": elapsed
                },
            }

        except Exception as e:
            logger.error(f"Error in QABBA agent: {e}")
            return {
                "qabba_report": {"signal": "HOLD_QABBA", "error": str(e)},
                "errors": state.get("errors", []) + [f"QABBA: {e}"],
            }

    async def traced_qabba_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("qabba_agent"):
            return await qabba_node(state)
    return traced_qabba_node


def create_decision_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the final decision agent node with retry and validation system."""
    async def decision_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            messages = format_prompt(
                "decision_agent",
                symbol=state.get("symbol", "BTCUSDT"),
                technical_analysis=json.dumps(state.get("technical_report", {}), indent=2, default=str),
                sentiment_analysis=json.dumps(state.get("sentiment_report", {}), indent=2, default=str),
                visual_analysis=json.dumps(state.get("visual_report", {}), indent=2, default=str),
                qabba_analysis=json.dumps(state.get("qabba_report", {}), indent=2, default=str),
                market_metrics=json.dumps(state.get("indicators", {}), default=str),
                active_positions="[]",
            )

            if not messages:
                raise ValueError("Could not format decision prompt")

            # Convertir a formato de mensajes para el sistema de reintentos
            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Invocar con reintentos y validación
            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="decision_agent",
                max_retries=3,
                base_delay=1.0,
                required_keys=["final_decision"],
            )

            # Legacy logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("decision_agent", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store decision in Reasoning Bank
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    entry_digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="decision_agent",
                        prompt=messages[1]["content"][:500],  # User prompt (truncated)
                        result=report,
                        raw_response=raw_response,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )

                    # --- JUDGE INTEGRATION (FIXED) ---
                    if entry_digest and not errors:
                        try:
                            logger.info("⚖️ Calling Reasoning Judge...")
                            judge_config = get_judge_model_config()
                            judge = ReasoningLLMJudge(config=judge_config)

                            # Construct payload from local variables
                            payload = ReasoningJudgePayload(
                                agent_name="decision_agent",
                                prompt=messages[1]["content"],
                                normalized_result=report,
                                raw_response=raw_response,
                                backend=judge_config.provider,
                                metadata={"source": "langgraph_orchestrator"},
                                latency_ms=elapsed * 1000
                            )

                            verdict = judge.evaluate(payload)

                            if verdict:
                                logger.info(f"⚖️ Judge Verdict: {verdict.verdict} (Score: {verdict.score})")
                                reasoning_bank.attach_judge_feedback(
                                    agent_name="decision_agent",
                                    prompt_digest=entry_digest,
                                    judge_payload=verdict.as_entry_payload()
                                )
                            else:
                                logger.warning("⚠️ Judge returned no verdict")

                        except Exception as judge_err:
                            logger.error(f"⚠️ Judge evaluation failed: {judge_err}")
                    # -------------------------

                except Exception as store_err:
                    logger.debug(f"Could not store decision: {store_err}")

            return {
                "decision_report": report,
                "final_trade_decision": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "decision": elapsed
                },
            }

        except Exception as e:
            logger.error("Error in decision agent: %s", e)
            return {
                "decision_report": {"final_decision": "HOLD", "error": str(e)},
                "final_trade_decision": {"final_decision": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"Decision: {e}"],
            }

    async def traced_decision_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("decision_agent"):
            return await decision_node(state)
    return traced_decision_node


def create_risk_agent_node(llm: Any, reasoning_bank: Any = None):
    """
    Creates the risk agent node with retry and validation system.

    This agent evaluates the final decision and can veto it if the risk
    is too high.
    """
    async def risk_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            # Get proposed decision
            decision = state.get("final_trade_decision", {})
            proposed_action = decision.get("final_decision", decision.get("action", "HOLD"))

            # If HOLD, no risk to evaluate
            if proposed_action == "HOLD":
                return {
                    "risk_assessment": {
                        "verdict": "APPROVE",
                        "reason": "No action proposed",
                        "adjusted_position_size": 0,
                    },
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "risk": 0.01,
                    },
                }

            # Get historical context if ReasoningBank available
            historical_context = ""
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    success_rate = reasoning_bank.get_success_rate("decision_agent", lookback=20)
                    historical_context = f"""
### Recent Decision History:
- Win Rate: {success_rate.get('win_rate', 0):.1%}
- Total trades: {success_rate.get('total', 0)}
- Current streak: {success_rate.get('streak', 0)} {'wins' if success_rate.get('last_was_win') else 'losses'}
"""
                except Exception:
                    pass

            messages = format_prompt(
                "risk_manager",
                decision=proposed_action,
                confidence=str(decision.get("confidence_in_decision", "MEDIUM")),
                balance="10000",  # TODO: obtener del estado
                open_positions="0",
                daily_pnl="0",
                current_drawdown="0%",
                atr=str(state.get("indicators", {}).get("atr", "N/A")),
                volatility="MEDIUM",
                liquidity="HIGH",
                max_risk_per_trade="2",
                max_total_exposure="5",
            )

            if not messages:
                raise ValueError("Could not format risk prompt")

            # Add historical context to prompt
            if historical_context:
                messages[1]["content"] += f"\n\n{historical_context}"

            # Convertir a formato de mensajes para el sistema de reintentos
            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Invocar con reintentos y validación
            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="risk_manager",
                max_retries=3,
                base_delay=1.0,
                required_keys=["verdict"],
            )

            # Legacy logging
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("risk_manager", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store in ReasoningBank
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                prompt_summary = f"Risk eval: {proposed_action} @ {state.get('current_price')}"
                store_agent_decision(
                    reasoning_bank=reasoning_bank,
                    agent_name="risk_manager",
                    prompt=prompt_summary,
                    result=report,
                    raw_response=raw_response,
                    backend="langgraph",
                    latency_ms=elapsed * 1000,
                )

            return {
                "risk_assessment": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "risk": elapsed,
                },
            }

        except Exception as e:
            logger.error("Error in risk agent: %s", e)
            return {
                "risk_assessment": {"verdict": "APPROVE", "error": str(e)},
                "errors": state.get("errors", []) + [f"Risk: {e}"],
            }

    async def traced_risk_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("risk_manager"):
            return await risk_node(state)
    return traced_risk_node


# ============================================================================
# GRAPH BUILDER
# ============================================================================

class FenixTradingGraph:
    """
    Fenix multi-agent trading graph using LangGraph.
    
    Enhanced flow:
    START -> [Technical, Sentiment, QABBA] (paralelo) -> Visual -> Decision -> Risk -> END
    
    With ReasoningBank integration for persistence and historical context.
    """
    
    def __init__(
        self,
        llm_config: LLMProvidersConfig | None = None,
        enable_visual: bool = True,
        enable_sentiment: bool = True,
        enable_risk: bool = True,
        reasoning_bank: Any = None,
    ):
        if not LANGGRAPH_AVAILABLE:
            raise ImportError("LangGraph is not installed. Run: pip install langgraph")
        
        self.llm_factory = LLMFactory(llm_config)
        self.enable_visual = enable_visual
        self.enable_sentiment = enable_sentiment
        self.enable_risk = enable_risk
        
        # Initialize ReasoningBank if available
        if reasoning_bank is not None:
            self.reasoning_bank = reasoning_bank
        elif REASONING_BANK_AVAILABLE and get_reasoning_bank is not None:
            try:
                self.reasoning_bank = get_reasoning_bank()
            except Exception as e:
                logger.warning("Could not initialize ReasoningBank: %s", e)
                self.reasoning_bank = None
        else:
            self.reasoning_bank = None
        
        self.graph = self._build_graph()
    
    def _build_graph(self) -> Any:
        """Builds the StateGraph with all agents."""
        # Obtener LLMs para cada agente
        technical_llm = self.llm_factory.get_llm_for_agent("technical")
        qabba_llm = self.llm_factory.get_llm_for_agent("qabba")
        decision_llm = self.llm_factory.get_llm_for_agent("decision")
        
        # Create nodes
        technical_node = create_technical_agent_node(technical_llm, self.reasoning_bank)
        qabba_node = create_qabba_agent_node(qabba_llm, self.reasoning_bank)
        decision_node = create_decision_agent_node(decision_llm, self.reasoning_bank)
        
        # Build graph
        graph = StateGraph(FenixAgentState)
        
        # Add main nodes (always active)
        graph.add_node("Technical Agent", technical_node)
        graph.add_node("QABBA Agent", qabba_node)
        graph.add_node("Decision Agent", decision_node)
        
        # Risk Agent (after Decision)
        if self.enable_risk:
            risk_llm = self.llm_factory.get_llm_for_agent("risk_manager")
            risk_node = create_risk_agent_node(risk_llm, self.reasoning_bank)
            graph.add_node("Risk Agent", risk_node)
        
        # Add optional nodes
        if self.enable_sentiment:
            sentiment_llm = self.llm_factory.get_llm_for_agent("sentiment")
            sentiment_node = create_sentiment_agent_node(sentiment_llm, self.reasoning_bank)
            graph.add_node("Sentiment Agent", sentiment_node)
        
        if self.enable_visual:
            visual_llm = self.llm_factory.get_llm_for_agent("visual")
            visual_node = create_visual_agent_node(visual_llm, self.reasoning_bank)
            graph.add_node("Visual Agent", visual_node)
        
        # Definir flujo
        # Phase 1: Parallel Analysis (Technical, QABBA, Sentiment)
        graph.add_edge(START, "Technical Agent")
        graph.add_edge(START, "QABBA Agent")
        
        if self.enable_sentiment:
            graph.add_edge(START, "Sentiment Agent")
            graph.add_edge("Sentiment Agent", "Decision Agent")
        
        # Technical and QABBA go to Visual or Decision
        if self.enable_visual:
            graph.add_edge("Technical Agent", "Visual Agent")
            graph.add_edge("QABBA Agent", "Visual Agent")
            graph.add_edge("Visual Agent", "Decision Agent")
        else:
            graph.add_edge("Technical Agent", "Decision Agent")
            graph.add_edge("QABBA Agent", "Decision Agent")
        
        # Flow from Decision to Risk (if enabled) or END
        if self.enable_risk:
            graph.add_edge("Decision Agent", "Risk Agent")
            graph.add_edge("Risk Agent", END)
        else:
            graph.add_edge("Decision Agent", END)
        
        # Compile without checkpointer to avoid memory leaks from state accumulation
        # memory = MemorySaver()
        return graph.compile()
    
    async def invoke(
        self,
        symbol: str,
        timeframe: str,
        indicators: dict[str, Any],
        current_price: float,
        current_volume: float,
        obi: float = 1.0,
        cvd: float = 0.0,
        spread: float = 0.01,
        orderbook_depth: dict[str, float] | None = None,
        mtf_context: dict[str, Any] | None = None,
        chart_image_b64: str | None = None,
        news_data: list[dict[str, Any]] | None = None,
        social_data: dict[str, Any] | None = None,
        fear_greed_value: str | None = None,
        thread_id: str = "default",
    ) -> FenixAgentState:
        """
        Executes the full trading graph.
        
        Args:
            symbol: Pair symbol (e.g., "BTCUSDT")
            timeframe: Timeframe (e.g., "15m")
            indicators: Dictionary of technical indicators
            current_price: Current price
            current_volume: Current volume
            obi: Order Book Imbalance
            cvd: Cumulative Volume Delta
            spread: Spread bid-ask
            orderbook_depth: Order book depth
            mtf_context: Multi-timeframe context
            chart_image_b64: Chart image in base64
            thread_id: Thread ID for persistence
            social_data: Dictionary with Twitter/Reddit posts or other social data
            fear_greed_value: Fear & Greed Index value (string)
        
        Returns:
            Final state with all decisions
        """
        initial_state: FenixAgentState = {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": datetime.now().isoformat(),
            "indicators": indicators,
            "current_price": current_price,
            "current_volume": current_volume,
            "obi": obi,
            "cvd": cvd,
            "spread": spread,
            "orderbook_depth": orderbook_depth or {},
            "mtf_context": mtf_context or {},
            "chart_image_b64": chart_image_b64,
            "news_data": news_data or [],
            "social_data": social_data or {},
            "fear_greed_value": fear_greed_value or "N/A",
            "messages": [],
            "errors": [],
            "execution_times": {},
        }

        # Execution config (no persistence)
        config = {"configurable": {}}

        # Measure total time and capture in dashboard
        import time
        start_time = time.time()

        result = await self.graph.ainvoke(initial_state, config)

        total_latency_ms = (time.time() - start_time) * 1000

        # Update dashboard if available
        if DASHBOARD_AVAILABLE:
            self._update_dashboard(result, total_latency_ms)

        return result

    def _update_dashboard(
        self, result: FenixAgentState, total_latency_ms: float
    ) -> None:
        """Updates the dashboard with pipeline results."""
        try:
            dashboard = get_dashboard()

            # Update states of agents
            exec_times = result.get("execution_times", {})
            for agent_name, latency in exec_times.items():
                status = "completed"
                if agent_name in str(result.get("errors", [])):
                    status = "error"
                dashboard.update_agent_status(
                    agent_name=agent_name,
                    status=status,
                    latency_ms=latency * 1000 if latency < 100 else latency,
                )

            # Extract final signal
            final_signal = None
            decision = result.get("final_trade_decision") or result.get(
                "decision_report"
            )
            if isinstance(decision, dict):
                final_signal = decision.get("final_decision") or decision.get("signal")

            # Record pipeline execution
            success = not result.get("errors")
            dashboard.record_pipeline_run(
                success=success,
                latency_ms=total_latency_ms,
                final_signal=final_signal,
                state=result,
            )
        except Exception as e:
            logger.warning("Error updating dashboard: %s", e)
    
    async def ainvoke(
        self,
        symbol: str,
        timeframe: str,
        indicators: dict[str, Any],
        current_price: float,
        current_volume: float,
        **kwargs,
    ) -> FenixAgentState:
        """Asynchronous version of invoke."""
        return await self.invoke(
            symbol=symbol,
            timeframe=timeframe,
            indicators=indicators,
            current_price=current_price,
            current_volume=current_volume,
            **kwargs,
        )
    
    def get_graph_visualization(self) -> str | None:
        """Returns an ASCII visualization of the graph."""
        try:
            return self.graph.get_graph().draw_ascii()
        except Exception:
            return None


# ============================================================================
# SINGLETON Y FACTORY
# ============================================================================

_trading_graph: FenixTradingGraph | None = None


def get_trading_graph(
    llm_config: LLMProvidersConfig | None = None,
    force_new: bool = False,
) -> FenixTradingGraph:
    """Gets or creates the singleton trading graph."""
    global _trading_graph
    
    if _trading_graph is None or force_new:
        _trading_graph = FenixTradingGraph(llm_config=llm_config)
    
    return _trading_graph


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Basic test
    print("=== Fenix LangGraph Orchestrator ===")
    
    if not LANGGRAPH_AVAILABLE:
        print("❌ LangGraph is not installed")
        print("   Run: pip install langgraph langchain-core")
        exit(1)
    
    # Create test configuration (local Ollama)
    from config.llm_provider_config import EXAMPLE_ALL_LOCAL
    
    print("✅ Creating trading graph...")
    trading_graph = FenixTradingGraph(
        llm_config=EXAMPLE_ALL_LOCAL,
        enable_visual=False,  # Disable for test without image
        enable_sentiment=True,
    )
    
    # Visualizar grafo
    viz = trading_graph.get_graph_visualization()
    if viz:
        print("\n=== Graph Structure ===")
        print(viz)
    
    # Execute with test data
    print("\n=== Executing test analysis ===")
    
    test_indicators = {
        "rsi": 45.5,
        "macd_line": 120.5,
        "macd_signal": 115.2,
        "supertrend_signal": "BULLISH",
        "ema_9": 67500,
        "ema_21": 67300,
        "adx": 28.5,
    }
    
    result = trading_graph.invoke(
        symbol="BTCUSDT",
        timeframe="15m",
        indicators=test_indicators,
        current_price=67500.0,
        current_volume=1234567.0,
        obi=1.15,
        cvd=50000.0,
        spread=0.5,
    )
    
    print("\n=== Final Result ===")
    print(f"Decision: {result.get('final_trade_decision', {}).get('final_decision', 'N/A')}")
    print(f"Confidence: {result.get('final_trade_decision', {}).get('confidence_in_decision', 'N/A')}")
    print(f"\nExecution times: {result.get('execution_times', {})}")

    # Show retry info per agent
    print("\n=== Retry System ===")
    for agent_name in ["technical_report", "sentiment_report", "qabba_report", "decision_report", "risk_assessment"]:
        report = result.get(agent_name, {})
        if report:
            attempts = report.get("_attempts", 1)
            errors = report.get("_validation_errors", [])
            if attempts > 1 or errors:
                print(f"  {agent_name}: {attempts} attempt(s)")
                if errors:
                    print(f"    Errors: {errors}")
            else:
                print(f"  {agent_name}: ✓ Success on first attempt")

    if result.get("errors"):
        print(f"\n⚠️ Errors: {result['errors']}")

    # Show global retry statistics
    print("\n=== Validation Statistics ===")
    log_retry_stats()
