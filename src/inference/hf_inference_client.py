"""
Hugging Face Inference API Client for Advanced Models
Integration with Kimi-K2-Thinking, Qwen3, and other cloud-based models
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class HFInferenceResponse:
    """Response from HF Inference API"""

    content: str
    model: str
    success: bool
    latency_ms: float
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class HFInferenceClient:
    """
    Client for Hugging Face Inference API with support for advanced thinking models
    """

    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.getenv("HF_API_TOKEN")
        if not self.api_token:
            raise ValueError("HF_API_TOKEN is required for Hugging Face Inference API")

        self.base_url = "https://router.huggingface.co/hf-inference"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        # Model configurations
        self.model_configs = {
            "moonshotai/Kimi-K2-Thinking": {
                "max_tokens": 32768,
                "temperature": 0.1,
                "supports_thinking": True,
                "provider": "novita",
                "description": "Advanced thinking model with deep reasoning capabilities",
            },
            "Qwen/Qwen3-30B-A3B-Thinking-2507-FP8": {
                "max_tokens": 32768,
                "temperature": 0.1,
                "supports_thinking": True,
                "provider": "hf_inference",
                "description": "Qwen3 thinking model with 30B parameters",
            },
            "Qwen/Qwen3-Next-80B-A3B-Thinking": {
                "max_tokens": 32768,
                "temperature": 0.1,
                "supports_thinking": True,
                "provider": "hf_inference",
                "description": "Next-generation Qwen3 model with 80B parameters",
            },
            "Qwen/Qwen3-Next-80B-A3B-Thinking-FP8": {
                "max_tokens": 32768,
                "temperature": 0.1,
                "supports_thinking": True,
                "provider": "hf_inference",
                "description": "FP8 quantized version of Qwen3-Next model",
            },
            "Qwen/Qwen3-VL-30B-A3B-Instruct": {
                "max_tokens": 8192,
                "temperature": 0.2,
                "supports_vision": True,
                "provider": "hf_inference",
                "description": "Vision-language model for chart analysis",
            },
            "Qwen/Qwen3-VL-235B-A22B-Instruct": {
                "max_tokens": 8192,
                "temperature": 0.2,
                "supports_vision": True,
                "provider": "hf_inference",
                "description": "Large vision-language model with 235B parameters",
            },
        }

        # Performance tracking
        self.performance_stats = {}
        self.response_log_dir = Path("logs/hf_inference_responses")
        self.response_log_dir.mkdir(parents=True, exist_ok=True)

    def _log_response(
        self, model: str, prompt: str, response: HFInferenceResponse, metadata: dict | None = None
    ):
        """Log response for analysis"""
        try:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "model": model,
                "prompt": prompt[:500] + "..." if len(prompt) > 500 else prompt,
                "response": response.content[:1000] + "..."
                if len(response.content) > 1000
                else response.content,
                "success": response.success,
                "latency_ms": response.latency_ms,
                "error_message": response.error_message,
                "metadata": metadata or {},
            }

            log_file = (
                self.response_log_dir
                / f"{model.replace('/', '_')}_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
            )
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"Failed to log response: {e}")

    def _update_performance_stats(self, model: str, success: bool, latency_ms: float):
        """Update performance statistics"""
        if model not in self.performance_stats:
            self.performance_stats[model] = {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "avg_latency_ms": 0.0,
                "min_latency_ms": float("inf"),
                "max_latency_ms": 0.0,
                "last_request_time": None,
            }

        stats = self.performance_stats[model]
        stats["total_requests"] += 1
        stats["last_request_time"] = datetime.utcnow().isoformat()

        if success:
            stats["successful_requests"] += 1
            stats["avg_latency_ms"] = (
                stats["avg_latency_ms"] * (stats["successful_requests"] - 1) + latency_ms
            ) / stats["successful_requests"]
            stats["min_latency_ms"] = min(stats["min_latency_ms"], latency_ms)
            stats["max_latency_ms"] = max(stats["max_latency_ms"], latency_ms)
        else:
            stats["failed_requests"] += 1

    def generate(
        self,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> HFInferenceResponse:
        """
        Generate response using HF Inference API

        Args:
            model: Model name (e.g., "moonshotai/Kimi-K2-Thinking")
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Optional temperature override
            max_tokens: Optional max tokens override
            **kwargs: Additional parameters

        Returns:
            HFInferenceResponse with result and metadata
        """
        start_time = time.time()

        try:
            # Get model configuration
            model_config = self.model_configs.get(model, {})

            # Prepare request payload
            payload = {
                "inputs": prompt,
                "parameters": {
                    "temperature": temperature or model_config.get("temperature", 0.1),
                    "max_new_tokens": max_tokens or model_config.get("max_tokens", 2048),
                    "return_full_text": False,
                    "do_sample": True,
                    "top_p": 0.9,
                    "repetition_penalty": 1.05,
                },
            }

            # Add system prompt if provided
            if system_prompt:
                payload["inputs"] = f"System: {system_prompt}\n\nUser: {prompt}\n\nAssistant:"

            # Update payload with additional parameters
            payload["parameters"].update(kwargs)

            # Make API request
            api_endpoint = f"{self.base_url}/models/{model}"

            logger.info(f"Calling HF Inference API for model: {model}")
            response = requests.post(
                api_endpoint,
                headers=self.headers,
                json=payload,
                timeout=120,  # 2 minute timeout
            )

            latency_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                try:
                    # Parse response based on model type
                    response_data = response.json()

                    if isinstance(response_data, list) and len(response_data) > 0:
                        content = response_data[0].get("generated_text", "")
                    elif isinstance(response_data, dict):
                        content = response_data.get("generated_text", "")
                    else:
                        content = str(response_data)

                    # Clean thinking tags if present
                    if model_config.get("supports_thinking"):
                        content = self._clean_thinking_content(content)

                    result = HFInferenceResponse(
                        content=content,
                        model=model,
                        success=True,
                        latency_ms=latency_ms,
                        usage=response_data.get("usage")
                        if isinstance(response_data, dict)
                        else None,
                        metadata={"raw_response": response_data},
                    )

                    self._update_performance_stats(model, True, latency_ms)
                    self._log_response(model, prompt, result, {"api_endpoint": api_endpoint})

                    return result

                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    error_msg = f"Failed to parse response: {e}"
                    logger.error(error_msg)

                    result = HFInferenceResponse(
                        content="",
                        model=model,
                        success=False,
                        latency_ms=latency_ms,
                        error_message=error_msg,
                        metadata={"raw_response": response.text},
                    )

                    self._update_performance_stats(model, False, latency_ms)
                    return result

            else:
                error_msg = (
                    f"API request failed with status {response.status_code}: {response.text}"
                )
                logger.error(error_msg)

                result = HFInferenceResponse(
                    content="",
                    model=model,
                    success=False,
                    latency_ms=latency_ms,
                    error_message=error_msg,
                )

                self._update_performance_stats(model, False, latency_ms)
                return result

        except requests.exceptions.Timeout:
            error_msg = "Request timeout after 120 seconds"
            logger.error(error_msg)

            result = HFInferenceResponse(
                content="",
                model=model,
                success=False,
                latency_ms=(time.time() - start_time) * 1000,
                error_message=error_msg,
            )

            self._update_performance_stats(model, False, (time.time() - start_time) * 1000)
            return result

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg, exc_info=True)

            result = HFInferenceResponse(
                content="",
                model=model,
                success=False,
                latency_ms=(time.time() - start_time) * 1000,
                error_message=error_msg,
            )

            self._update_performance_stats(model, False, (time.time() - start_time) * 1000)
            return result

    def generate_with_image(
        self,
        model: str,
        prompt: str,
        image_path: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> HFInferenceResponse:
        """
        Generate response with image input using HF Inference API

        Args:
            model: Vision model name
            prompt: User prompt
            image_path: Path to image file
            system_prompt: Optional system prompt
            temperature: Optional temperature override
            max_tokens: Optional max tokens override
            **kwargs: Additional parameters

        Returns:
            HFInferenceResponse with result and metadata
        """
        start_time = time.time()

        try:
            # Read and encode image
            import base64

            from PIL import Image

            with open(image_path, "rb") as img_file:
                image_data = base64.b64encode(img_file.read()).decode("utf-8")

            # Get image format
            with Image.open(image_path) as img:
                image_format = img.format.lower()

            # Prepare request payload for vision model
            payload = {
                "inputs": {
                    "prompt": prompt,
                    "image": f"data:image/{image_format};base64,{image_data}",
                },
                "parameters": {
                    "temperature": temperature or 0.2,
                    "max_new_tokens": max_tokens or 2048,
                    "return_full_text": False,
                    "do_sample": True,
                    "top_p": 0.9,
                },
            }

            # Add system prompt if provided
            if system_prompt:
                payload["inputs"]["prompt"] = (
                    f"System: {system_prompt}\n\nUser: {prompt}\n\nAssistant:"
                )

            # Make API request
            api_endpoint = f"{self.base_url}/models/{model}"

            logger.info(f"Calling HF Inference API for vision model: {model}")
            response = requests.post(
                api_endpoint,
                headers=self.headers,
                json=payload,
                timeout=180,  # 3 minute timeout for vision models
            )

            latency_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                try:
                    response_data = response.json()
                    content = (
                        response_data.get("generated_text", "")
                        if isinstance(response_data, dict)
                        else str(response_data)
                    )

                    result = HFInferenceResponse(
                        content=content,
                        model=model,
                        success=True,
                        latency_ms=latency_ms,
                        usage=response_data.get("usage")
                        if isinstance(response_data, dict)
                        else None,
                        metadata={"raw_response": response_data, "image_path": image_path},
                    )

                    self._update_performance_stats(model, True, latency_ms)
                    self._log_response(
                        model,
                        prompt,
                        result,
                        {"image_path": image_path, "api_endpoint": api_endpoint},
                    )

                    return result

                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    error_msg = f"Failed to parse vision response: {e}"
                    logger.error(error_msg)

                    result = HFInferenceResponse(
                        content="",
                        model=model,
                        success=False,
                        latency_ms=latency_ms,
                        error_message=error_msg,
                    )

                    self._update_performance_stats(model, False, latency_ms)
                    return result
            else:
                error_msg = (
                    f"Vision API request failed with status {response.status_code}: {response.text}"
                )
                logger.error(error_msg)

                result = HFInferenceResponse(
                    content="",
                    model=model,
                    success=False,
                    latency_ms=latency_ms,
                    error_message=error_msg,
                )

                self._update_performance_stats(model, False, latency_ms)
                return result

        except Exception as e:
            error_msg = f"Vision processing error: {str(e)}"
            logger.error(error_msg, exc_info=True)

            result = HFInferenceResponse(
                content="",
                model=model,
                success=False,
                latency_ms=(time.time() - start_time) * 1000,
                error_message=error_msg,
            )

            self._update_performance_stats(model, False, (time.time() - start_time) * 1000)
            return result

    def _clean_thinking_content(self, content: str) -> str:
        """Clean thinking tags from model responses"""
        # Remove thinking tags if present
        import re

        # Pattern to match thinking content
        thinking_pattern = r"<think>.*?</think>"
        content = re.sub(thinking_pattern, "", content, flags=re.DOTALL)

        # Clean up any remaining tags
        content = re.sub(r"</?think>", "", content)

        return content.strip()

    def get_performance_stats(self, model: str | None = None) -> dict[str, Any]:
        """Get performance statistics for models"""
        if model:
            return self.performance_stats.get(model, {})
        return self.performance_stats

    def get_model_info(self, model: str) -> dict[str, Any]:
        """Get information about a specific model"""
        return self.model_configs.get(model, {})

    def list_available_models(self) -> list[str]:
        """List all available models"""
        return list(self.model_configs.keys())


# Global client instance
_hf_inference_client: HFInferenceClient | None = None


def get_hf_inference_client() -> HFInferenceClient:
    """Get or create the global HF inference client"""
    global _hf_inference_client
    if _hf_inference_client is None:
        _hf_inference_client = HFInferenceClient()
    return _hf_inference_client
