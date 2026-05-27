# src/core/orchestrator/json_parser.py
"""
JSON extraction utilities for Fenix Trading Bot.

Robustly extracts JSON from LLM responses that may contain
additional text, markdown code blocks, or formatting issues.
"""

import json
import re


def extract_json_from_content(
    content: str,
    required_keys: list[str] | None = None,
) -> dict | None:
    """Extracts the last valid JSON from LLM content with improved robustness."""
    if not content:
        return None

    text = content.strip()

    # Remove thinking tags if present
    if "...done thinking" in text:
        parts = text.split("...done thinking")
        if len(parts) > 1:
            text = parts[-1].strip().lstrip(".").strip()

    # Remove <analysis> and <json> tags if present
    text = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    text = re.sub(r"<json>", "", text)
    text = re.sub(r"</json>", "", text)

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

    def _fix_common_json_issues(candidate: str) -> str:
        """Fixes common JSON formatting issues."""
        # Remove trailing commas before closing brackets/braces
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)

        # Fix unquoted keys (common issue)
        # This is a simple fix - may not catch all cases
        candidate = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', candidate)

        # Fix single quotes to double quotes (but be careful with escaped quotes)
        # Only replace single quotes that are not escaped
        in_string = False
        escape = False
        result = []
        for i, ch in enumerate(candidate):
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
            if not in_string and ch == "'":
                result.append('"')
                continue
            result.append(ch)
        return "".join(result)

    def _find_json_objects(source: str) -> list[str]:
        """Find all balanced JSON objects in source string."""
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
                    objects.append(source[start : i + 1])
                    start = None
        return objects

    # Try direct parse first
    for fix_func in [lambda x: x, _sanitize_json, _fix_common_json_issues]:
        try:
            parsed = json.loads(fix_func(text))
            if not required_keys or all(key in parsed for key in required_keys):
                return parsed
        except json.JSONDecodeError:
            continue

    # Try fenced JSON blocks (```json ... ``` or ``` ... ```)
    for pattern in (r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            for fix_func in [lambda x: x, _sanitize_json, _fix_common_json_issues]:
                try:
                    parsed = json.loads(fix_func(candidate))
                    if not required_keys or all(key in parsed for key in required_keys):
                        return parsed
                except json.JSONDecodeError:
                    continue

    # Find all balanced JSON objects and prefer the last valid one
    candidates = _find_json_objects(text)
    for candidate in reversed(candidates):
        for fix_func in [lambda x: x, _sanitize_json, _fix_common_json_issues]:
            try:
                parsed = json.loads(fix_func(candidate))
                if not required_keys or all(key in parsed for key in required_keys):
                    return parsed
            except json.JSONDecodeError:
                continue

    return None
