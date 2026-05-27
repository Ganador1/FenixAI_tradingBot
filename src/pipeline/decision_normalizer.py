"""Decision output normalization utilities.

Este módulo convierte salidas heterogéneas del Decision Agent a un esquema común:
{
  'decision': 'BUY'|'SELL'|'HOLD'|'VETO'|...
  'reasoning': str,
  'confidence': float|str (se mantiene si viene categórico),
  ... (otras claves originales preservadas)
}
"""

from __future__ import annotations

from typing import Any

CANONICAL_KEYS = {
    "decision": ("decision", "final_decision", "action"),
    "reasoning": ("reasoning", "combined_reasoning", "reason"),
    "confidence": ("confidence", "confidence_in_decision"),
}


def normalize_decision_output(result: Any) -> dict[str, Any]:
    """Normaliza la salida del Decision Agent a un dict con claves canónicas.

    - Acepta dicts u objetos con atributos.
    - Preserva el resto de las claves originales.
    - Eleva ValueError si no se puede determinar 'decision'.
    """
    if result is None:
        raise ValueError("Decision result is None")

    # Convertir a dict si es un objeto con atributos esperados
    if not isinstance(result, dict):
        obj_dict: dict[str, Any] = {}
        for variants in CANONICAL_KEYS.values():
            for v in variants:
                if hasattr(result, v):
                    obj_dict[v] = getattr(result, v)
        # Añadir __dict__ si existe
        if hasattr(result, "__dict__"):
            obj_dict.update({k: v for k, v in vars(result).items() if k not in obj_dict})
        result = obj_dict

    if not isinstance(result, dict):
        raise ValueError(f"Invalid decision type: {type(result)}")

    normalized: dict[str, Any] = dict(result)  # copia superficial

    # Mapear claves canónicas
    decision_val = _first_key(result, CANONICAL_KEYS["decision"])
    if decision_val is not None:
        normalized["decision"] = str(decision_val).upper()

    reasoning_val = _first_key(result, CANONICAL_KEYS["reasoning"])
    if reasoning_val is not None:
        normalized["reasoning"] = reasoning_val

    confidence_val = _first_key(result, CANONICAL_KEYS["confidence"])
    if confidence_val is not None:
        normalized["confidence"] = confidence_val

    # Validación mínima
    if "decision" not in normalized:
        raise ValueError(f"Invalid decision format: {result}")

    return normalized


def _first_key(d: dict[str, Any], keys: tuple) -> Any:
    return next((d[k] for k in keys if k in d), None)
