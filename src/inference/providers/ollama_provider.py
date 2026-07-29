from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from src.inference.providers.base import (
    GenerationParams,
    InferenceProvider,
    ProviderError,
    _metadata,
)


class OllamaProvider(InferenceProvider):
    """Provider simple que usa la CLI local de Ollama cuando está disponible.

    Implementa generate_text, chat_completions y generate_with_vision.
    Intenta usar la API HTTP local en caso de que esté disponible y la CLI si no.
    """

    def __init__(self):
        candidate = shutil.which("ollama")
        self._cli_path = (
            str(Path(candidate).resolve())
            if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK)
            else None
        )

    def name(self) -> str:
        return "ollama"

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_chat": True,
            "supports_text": True,
            "supports_vision": True,
            "supports_tools": False,
        }

    def _ensure_cli(self) -> None:
        if not self._cli_path:
            raise ProviderError("Ollama CLI not found; ensure 'ollama' is installed and in PATH")

    def _run_ollama_cli(self, model_id: str, prompt: str, timeout: float = 30.0) -> str:
        """Run `ollama run <model> --prompt <prompt>` via subprocess and return stdout.

        This method quotes the prompt to avoid shell injections and returns the text output.
        """
        self._ensure_cli()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model_id):
            raise ProviderError("Invalid Ollama model identifier")
        if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 2_000_000:
            raise ProviderError("Ollama prompt exceeds the 2 MB safety limit")
        start_ts = time.time()
        try:
            cmd = [self._cli_path, "run", model_id, "--json", prompt]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                # Try legacy text mode
                cmd = [self._cli_path, "run", model_id, prompt]
                proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                if proc2.returncode != 0:
                    raise ProviderError(f"Ollama CLI run failed: {proc2.stderr or proc.stderr}")
                else:
                    out = proc2.stdout or ""
            else:
                out = proc.stdout or ""

            # Ollama may return plain text or JSON; attempt to parse
            try:
                data = json.loads(out)
                # If JSON, common field is 'output' or 'text'
                if isinstance(data, dict) and "output" in data:
                    return data["output"]
                if isinstance(data, dict) and "text" in data:
                    return data["text"]
                # Otherwise return raw JSON string
                return json.dumps(data)
            except Exception:
                return out.strip()

        except subprocess.TimeoutExpired as e:
            raise ProviderError("Ollama CLI timeout", cause=e)
        except Exception as e:
            raise ProviderError(f"Ollama CLI error: {e}", cause=e)

    def generate_text(self, model_id: str, prompt: str, params: GenerationParams) -> dict[str, Any]:
        start_ts = time.time()
        try:
            txt = self._run_ollama_cli(model_id, prompt, timeout=params.timeout or 30.0)
            return {"text": txt, "metadata": _metadata(self.name(), model_id, start_ts)}
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Ollama generate_text failed: {e}", cause=e)

    def chat_completions(
        self, model_id: str, messages: list[dict[str, str]], params: GenerationParams
    ) -> dict[str, Any]:
        start_ts = time.time()
        # Convert messages to a simple prompt
        try:
            sys_msg = (params.extra or {}).get("system", "")
            prompt_lines = []
            if sys_msg:
                prompt_lines.append(f"System: {sys_msg}")
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                prompt_lines.append(f"{role.capitalize()}: {content}")
            prompt = "\n".join(prompt_lines)
            return {
                "text": self._run_ollama_cli(model_id, prompt, timeout=params.timeout or 30.0),
                "metadata": _metadata(self.name(), model_id, start_ts),
            }
        except Exception as e:
            raise ProviderError(f"Ollama chat failed: {e}", cause=e)

    def generate_with_vision(
        self, model_id: str, prompt: str, images: list[str], params: GenerationParams
    ) -> dict[str, Any]:
        start = time.time()
        # Ollama CLI doesn't support file uploads directly via CLI in many versions;
        # we fallback to attaching image URIs/paths in the prompt for models that accept it.
        try:
            refs = "\n".join([f"[image]: {u}" for u in images])
            combined_prompt = f"{prompt}\n{refs}" if refs else prompt
            text = self._run_ollama_cli(model_id, combined_prompt, timeout=params.timeout or 60.0)
            return {
                "text": text,
                "metadata": _metadata(self.name(), model_id, start, images_provided=len(images)),
            }
        except Exception as e:
            raise ProviderError(f"Ollama vision generation failed: {e}", cause=e)
