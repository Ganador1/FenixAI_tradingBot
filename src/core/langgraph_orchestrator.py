# src/core/langgraph_orchestrator.py
"""
LangGraph Orchestrator para Fenix Trading Bot.

Implementa un grafo de agentes usando LangGraph.
Características:
- Agentes especializados: técnico, sentimiento, visual, QABBA, decisión final
- Multi-provider LLM support
- Ponderación dinámica de agentes
- Sistema de riesgo integrado
- Multi-timeframe context
- Integración con ReasoningBank para persistencia
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, TypedDict, Annotated

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
# LOGGING HELPER
# ============================================================================

def save_legacy_agent_log(
    agent_name: str,
    prompt: list[dict[str, str]] | list[Any],
    response_content: str,
    parsed_json: dict | None,
):
    """
    Guarda logs detallados al estilo legacy (input/output/prompt/raw) en src/logs/llm_responses.
    Útil para debugging humano y análisis detallado.
    """
    try:
        # Directorio base - usar path absoluto basado en ubicación del script
        project_root = Path(__file__).parent.parent.parent
        base_dir = project_root / "src" / "logs" / "llm_responses" / agent_name
        base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📝 Saving agent log to: {base_dir}")
        
        # Timestamp único
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"{timestamp}"
        
        # Guardar prompt (raw list of messages)
        prompt_path = base_dir / f"{base_name}_prompt.txt"
        with open(prompt_path, "w", encoding="utf-8") as f:
            for i, msg in enumerate(prompt):
                content = getattr(msg, "content", str(msg))
                role = getattr(msg, "type", "unknown")
                f.write(f"--- Message {i} ({role}) ---\n{content}\n\n")
        
        # Guardar input estructurado (si es posible extraerlo del prompt)
        # Esto es más difícil de reconstruir genéricamente, pero guardamos el prompt completo
        
        # Guardar raw response
        raw_path = base_dir / f"{base_name}_raw_response.txt"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response_content)
            
        # Guardar output JSON
        if parsed_json:
            output_path = base_dir / f"{base_name}_output.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(parsed_json, f, indent=2, ensure_ascii=False)
                
    except Exception as e:
        logger.warning(f"Error saving legacy log for {agent_name}: {e}")

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
    Obtiene contexto histórico relevante del ReasoningBank.
    
    Busca decisiones anteriores similares para informar al agente actual.
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return ""
    
    try:
        # Buscar entradas relevantes
        relevant = reasoning_bank.get_relevant_context(
            agent_name=agent_name,
            current_prompt=current_prompt,
            limit=limit,
        )
        
        if not relevant:
            return ""
        
        context_parts = ["### Contexto Histórico (decisiones anteriores similares):"]
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
        logger.warning("Error obteniendo contexto de ReasoningBank: %s", e)
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
    Almacena la decisión del agente en ReasoningBank.
    
    Returns:
        prompt_digest para tracking posterior
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
        logger.warning("Error almacenando en ReasoningBank: %s", e)
        return None


# ============================================================================
# STATE DEFINITION
# ============================================================================

def merge_dicts(a: dict, b: dict) -> dict:
    """Combina dos diccionarios (para execution_times)."""
    return {**a, **b}


def append_lists(a: list, b: list) -> list:
    """Concatena dos listas (para errors y messages)."""
    return a + b


class FenixAgentState(TypedDict, total=False):
    """Estado compartido entre todos los agentes del grafo."""
    # Identificadores
    symbol: str
    timeframe: str
    timestamp: str
    
    # Datos de mercado
    kline_data: dict[str, list]
    current_price: float
    current_volume: float
    
    # Indicadores técnicos
    indicators: dict[str, Any]
    mtf_context: dict[str, Any]
    
    # Microestructura
    obi: float
    cvd: float
    spread: float
    orderbook_depth: dict[str, float]
    
    # Gráfico generado
    chart_image_b64: str | None
    chart_indicators_summary: dict[str, Any]
    
    # News data for sentiment agent
    news_data: list[dict[str, Any]]
    # Social data & metrics (Twitter/Reddit/fear_greed)
    social_data: dict[str, Any]
    fear_greed_value: str | None
    
    # Resultados de agentes (cada uno escribe a su propio campo)
    technical_report: dict[str, Any]
    sentiment_report: dict[str, Any]
    visual_report: dict[str, Any]
    qabba_report: dict[str, Any]
    
    # Decisión y riesgo
    decision_report: dict[str, Any]
    risk_assessment: dict[str, Any]
    final_trade_decision: dict[str, Any]
    
    # Metadata - Usando Annotated para permitir múltiples writes
    messages: Annotated[list[Any], append_lists]
    errors: Annotated[list[str], append_lists]
    execution_times: Annotated[dict[str, float], merge_dicts]


# ============================================================================
# LLM FACTORY
# ============================================================================

class LLMFactory:
    """Fábrica de LLMs que soporta múltiples providers."""
    
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
        """Obtiene el LLM configurado para un tipo de agente."""
        if agent_type in self._llm_cache:
            return self._llm_cache[agent_type]
        
        agent_config = self.config.get_agent_config(agent_type)
        logger.info(f"🏭 LLMFactory: Creating LLM for {agent_type} with provider={agent_config.provider_type}, model={agent_config.model_name}")
        llm = self._create_llm(agent_config)
        self._llm_cache[agent_type] = llm
        return llm
    
    def _create_llm(self, config: AgentProviderConfig) -> Any:
        """Crea una instancia de LLM basada en la configuración."""
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
                logger.warning(f"Provider {provider} no soportado, usando Ollama local")
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
            # Intentar fallback si está configurado
            if config.fallback_provider_type and config.fallback_model_name:
                fallback_config = AgentProviderConfig(
                    provider_type=config.fallback_provider_type,
                    model_name=config.fallback_model_name,
                    temperature=config.temperature,
                )
                logger.info(f"Intentando fallback: {config.fallback_provider_type}/{config.fallback_model_name}")
                return self._create_llm(fallback_config)
            # Si falla el fallback, devolver un StubLLM para evitar romper todo el grafo
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
    """Crea el nodo del agente técnico."""
    def technical_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()
        
        try:
            # Preparar indicadores como JSON
            indicators = state.get("indicators", {})
            mtf_context = state.get("mtf_context", {})
            
            # Formatear prompt
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
                raise ValueError("No se pudo formatear el prompt técnico")
            
            # Invocar LLM
            llm_messages = [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
            response = llm.invoke(llm_messages)
            
            # Parsear respuesta JSON
            content = response.content
            try:
                # Extraer JSON del contenido
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                
                report = json.loads(json_str)
            except json.JSONDecodeError:
                report = {
                    "signal": "HOLD",
                    "confidence_level": "LOW",
                    "reasoning": content,
                    "raw_response": True,
                }
            
            # Legacy logging
            save_legacy_agent_log("technical_enhanced", llm_messages, content, report)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            # Store result in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    prompt_digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="technical_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=content,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    if prompt_digest:
                        report["_reasoning_digest"] = prompt_digest
            except Exception as e:
                logger.debug(f"Technical ReasoningBank store failed: {e}")

            return {
                "technical_report": report,
                "messages": state.get("messages", []) + [response],
                "execution_times": {
                    **state.get("execution_times", {}),
                    "technical": elapsed
                },
            }
            
        except Exception as e:
            logger.error(f"Error en agente técnico: {e}")
            return {
                "technical_report": {"signal": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"Technical: {e}"],
            }
    
    def traced_technical_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("technical_agent"):
            return technical_node(state)

    return traced_technical_node


def create_sentiment_agent_node(llm: Any, reasoning_bank: Any = None):
    """Crea el nodo del agente de sentimiento."""
    def sentiment_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()
        
        try:
            # Build news summary from state news_data
            news_list = state.get("news_data", [])
            if news_list:
                news_items = [f"- [{n.get('source', 'N/A')}] {n.get('title', 'Sin título')}: {n.get('summary', '')[:100]}..." 
                              for n in news_list[:5]]
                news_summary = "\n".join(news_items)
            else:
                news_summary = "No hay noticias recientes disponibles"
            
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
                additional_context=(f"Las noticias fueron obtenidas de fuentes como CoinDesk y Cointelegraph. "
                                    f"Total de artículos disponibles: {len(news_list)}. "
                                    f"Social: Twitter={twitter_count}, Reddit={reddit_count}, Fear&Greed={fg_value}"),
            )
            
            if not messages:
                raise ValueError("No se pudo formatear el prompt de sentimiento")
            
            llm_messages = [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
            response = llm.invoke(llm_messages)
            
            content = response.content
            try:
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                report = json.loads(json_str)
            except json.JSONDecodeError:
                report = {
                    "overall_sentiment": "NEUTRAL",
                    "confidence_score": 0.5,
                    "reasoning": content,
                }
            
            # Legacy logging
            save_legacy_agent_log("sentiment", llm_messages, content, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            # Persist sentiment analysis in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="sentiment_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=content,
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
            logger.error(f"Error en agente de sentimiento: {e}")
            return {
                "sentiment_report": {"overall_sentiment": "NEUTRAL", "error": str(e)},
                "errors": state.get("errors", []) + [f"Sentiment: {e}"],
            }
    
    def traced_sentiment_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("sentiment_agent"):
            return sentiment_node(state)
    return traced_sentiment_node


def create_visual_agent_node(llm: Any, reasoning_bank: Any = None):
    """Crea el nodo del agente visual."""
    def visual_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()
        
        try:
            chart_b64 = state.get("chart_image_b64")
            
            logger.info(f"🖼️ Visual Agent: LLM type: {type(llm)}, model: {getattr(llm, 'model', 'unknown')}, base_url: {getattr(llm, 'base_url', 'unknown')}")
            logger.info(f"🖼️ Visual Agent: chart_image_b64 present = {chart_b64 is not None}, length = {len(chart_b64) if chart_b64 else 0}")
            
            if not chart_b64:
                # Sin imagen, análisis básico
                logger.warning("🖼️ Visual Agent: No chart image in state")
                return {
                    "visual_report": {
                        "action": "HOLD",
                        "confidence": 0.5,
                        "reason": "No hay imagen de gráfico disponible",
                        "visual_analysis": "No se proporcionó imagen para análisis visual"
                    },
                }
            
            # Preparar mensaje con imagen
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
                raise ValueError("No se pudo formatear el prompt visual")
            
            logger.info(f"🖼️ Visual Agent: Sending image ({len(chart_b64)} chars) to vision model...")
            
            # Crear mensaje con imagen para modelos vision
            vision_content = [
                {"type": "text", "text": image_prompt[1]["content"]},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{chart_b64}"}
                },
            ]
            
            llm_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(content=vision_content),
            ]
            response = llm.invoke(llm_messages)
            
            content = response.content
            logger.info(f"🖼️ Visual Agent: Response received, length = {len(content) if content else 0}")
            # DEBUG LOGGING - Log entire raw response to debug file
            with open("debug_visual_raw.log", "a") as f:
                f.write(f"\n--- {datetime.now()} ---\n{content}\n----------------\n")
            logger.info(f"🖼️ Visual Agent: Raw response preview: {content[:500] if content else 'EMPTY'}...")
            
            # Parse JSON response
            try:
                json_str = content
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    # Fallback: intentar extraer el primer objeto JSON
                    start = content.find("{")
                    end = content.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        json_str = content[start:end + 1]
                report = json.loads(json_str)
                # Ensure visual_analysis is present
                if "visual_analysis" not in report and content:
                    report["visual_analysis"] = content[:1000]
                logger.info(f"🖼️ Visual Agent: Parsed JSON with action={report.get('action')}")
            except json.JSONDecodeError:
                report = {
                    "action": "HOLD",
                    "confidence": 0.5,
                    "visual_analysis": content if content else "Sin análisis visual disponible",
                    "raw_parse_error": True
                }
                logger.warning(f"🖼️ Visual Agent: Could not parse JSON, storing raw content")
            
            # Legacy logging - avoid logging base64 image to text file
            log_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(content=f"[IMAGE CONTENT HIDDEN] \nPrompt: {image_prompt[1]['content']}"),
            ]
            save_legacy_agent_log("visual", log_messages, content, report)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            # Persist visual analysis in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = image_prompt[1]["content"][:500] if image_prompt and len(image_prompt) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="visual_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=content,
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
            logger.error(f"Error en agente visual: {e}")
            return {
                "visual_report": {
                    "action": "HOLD", 
                    "error": str(e),
                    "visual_analysis": f"Error en análisis visual: {str(e)}"
                },
                "errors": state.get("errors", []) + [f"Visual: {e}"],
            }
    
    def traced_visual_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("visual_agent"):
            return visual_node(state)
    return traced_visual_node


def create_qabba_agent_node(llm: Any, reasoning_bank: Any = None):
    """Crea el nodo del agente QABBA (microestructura)."""
    def qabba_node(state: FenixAgentState) -> dict:
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
                raise ValueError("No se pudo formatear el prompt QABBA")
            
            llm_messages = [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
            response = llm.invoke(llm_messages)
            
            content = response.content
            try:
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                report = json.loads(json_str)
            except json.JSONDecodeError:
                report = {
                    "signal": "HOLD_QABBA",
                    "qabba_confidence": 0.5,
                    "reasoning": content,
                }
            
            # Legacy logging
            save_legacy_agent_log("qabba_enhanced", llm_messages, content, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            # Store QABBA report in ReasoningBank
            try:
                if reasoning_bank and REASONING_BANK_AVAILABLE:
                    prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
                    digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="qabba_agent",
                        prompt=prompt_snippet,
                        result=report,
                        raw_response=content,
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
            logger.error(f"Error en agente QABBA: {e}")
            return {
                "qabba_report": {"signal": "HOLD_QABBA", "error": str(e)},
                "errors": state.get("errors", []) + [f"QABBA: {e}"],
            }
    
    def traced_qabba_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("qabba_agent"):
            return qabba_node(state)
    return traced_qabba_node


def create_decision_agent_node(llm: Any, reasoning_bank: Any = None):
    """Crea el nodo del agente de decisión final."""
    def decision_node(state: FenixAgentState) -> dict:
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
                raise ValueError("No se pudo formatear el prompt de decisión")
            
            llm_messages = [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
            response = llm.invoke(llm_messages)
            
            content = response.content
            try:
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                report = json.loads(json_str)
            except json.JSONDecodeError:
                report = {
                    "final_decision": "HOLD",
                    "confidence_in_decision": "LOW",
                    "combined_reasoning": content,
                }
            
            # Legacy logging
            save_legacy_agent_log("decision_agent", llm_messages, content, report)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            # Store decision in Reasoning Bank
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    entry_digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="decision_agent",
                        prompt=messages[1]["content"][:500],  # User prompt (truncated)
                        result=report,
                        raw_response=content,
                        backend=getattr(llm, 'model', 'langchain'),
                        latency_ms=elapsed * 1000,
                    )
                    
                    # --- JUDGE INTEGRATION (FIXED) ---
                    if entry_digest:
                        try:
                            logger.info("⚖️ Calling Reasoning Judge...")
                            judge_config = get_judge_model_config()
                            judge = ReasoningLLMJudge(config=judge_config)
                            
                            # Construct payload from local variables
                            payload = ReasoningJudgePayload(
                                agent_name="decision_agent",
                                prompt=messages[1]["content"],
                                normalized_result=report,
                                raw_response=content,
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
            logger.error("Error en agente de decisión: %s", e)
            return {
                "decision_report": {"final_decision": "HOLD", "error": str(e)},
                "final_trade_decision": {"final_decision": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"Decision: {e}"],
            }
    
    def traced_decision_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("decision_agent"):
            return decision_node(state)
    return traced_decision_node


def create_risk_agent_node(llm: Any, reasoning_bank: Any = None):
    """
    Crea el nodo del agente de riesgo.
    
    Este agente evalúa la decisión final y puede vetarla si el riesgo
    es demasiado alto.
    """
    def risk_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()
        
        try:
            # Obtener la decisión propuesta
            decision = state.get("final_trade_decision", {})
            proposed_action = decision.get("final_decision", decision.get("action", "HOLD"))
            
            # Si es HOLD, no hay riesgo que evaluar
            if proposed_action == "HOLD":
                return {
                    "risk_assessment": {
                        "verdict": "APPROVED",
                        "reason": "No action proposed",
                        "adjusted_position_size": 0,
                    },
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "risk": 0.01,
                    },
                }

            # Obtener contexto histórico si hay ReasoningBank
            historical_context = ""
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    success_rate = reasoning_bank.get_success_rate("decision_agent", lookback=20)
                    historical_context = f"""
### Historial de Decisiones Recientes:
- Win Rate: {success_rate.get('win_rate', 0):.1%}
- Trades totales: {success_rate.get('total', 0)}
- Racha actual: {success_rate.get('streak', 0)} {'wins' if success_rate.get('last_was_win') else 'losses'}
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
                raise ValueError("No se pudo formatear el prompt de riesgo")
            
            # Añadir contexto histórico al prompt
            if historical_context:
                messages[1]["content"] += f"\n\n{historical_context}"
            
            llm_messages = [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
            response = llm.invoke(llm_messages)
            
            content = response.content
            try:
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()
                else:
                    json_str = content
                report = json.loads(json_str)
            except json.JSONDecodeError:
                report = {
                    "verdict": "APPROVED",
                    "reason": content[:200],
                    "risk_notes": content,
                }
            
            # Legacy logging
            save_legacy_agent_log("risk_manager", llm_messages, content, report)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            # Almacenar en ReasoningBank
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                prompt_summary = f"Risk eval: {proposed_action} @ {state.get('current_price')}"
                store_agent_decision(
                    reasoning_bank=reasoning_bank,
                    agent_name="risk_manager",
                    prompt=prompt_summary,
                    result=report,
                    raw_response=content,
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
            logger.error("Error en agente de riesgo: %s", e)
            return {
                "risk_assessment": {"verdict": "APPROVED", "error": str(e)},
                "errors": state.get("errors", []) + [f"Risk: {e}"],
            }
    
    def traced_risk_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("risk_manager"):
            return risk_node(state)
    return traced_risk_node


# ============================================================================
# GRAPH BUILDER
# ============================================================================

class FenixTradingGraph:
    """
    Grafo de trading multi-agente de Fenix usando LangGraph.
    
    Flujo mejorado:
    START -> [Technical, Sentiment, QABBA] (paralelo) -> Visual -> Decision -> Risk -> END
    
    Con integración de ReasoningBank para persistencia y contexto histórico.
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
            raise ImportError("LangGraph no está instalado. Ejecuta: pip install langgraph")
        
        self.llm_factory = LLMFactory(llm_config)
        self.enable_visual = enable_visual
        self.enable_sentiment = enable_sentiment
        self.enable_risk = enable_risk
        
        # Inicializar ReasoningBank si está disponible
        if reasoning_bank is not None:
            self.reasoning_bank = reasoning_bank
        elif REASONING_BANK_AVAILABLE and get_reasoning_bank is not None:
            try:
                self.reasoning_bank = get_reasoning_bank()
            except Exception as e:
                logger.warning("No se pudo inicializar ReasoningBank: %s", e)
                self.reasoning_bank = None
        else:
            self.reasoning_bank = None
        
        self.graph = self._build_graph()
    
    def _build_graph(self) -> Any:
        """Construye el StateGraph con todos los agentes."""
        # Obtener LLMs para cada agente
        technical_llm = self.llm_factory.get_llm_for_agent("technical")
        qabba_llm = self.llm_factory.get_llm_for_agent("qabba")
        decision_llm = self.llm_factory.get_llm_for_agent("decision")
        
        # Crear nodos
        technical_node = create_technical_agent_node(technical_llm, self.reasoning_bank)
        qabba_node = create_qabba_agent_node(qabba_llm, self.reasoning_bank)
        decision_node = create_decision_agent_node(decision_llm, self.reasoning_bank)
        
        # Construir grafo
        graph = StateGraph(FenixAgentState)
        
        # Añadir nodos principales (siempre activos)
        graph.add_node("Technical Agent", technical_node)
        graph.add_node("QABBA Agent", qabba_node)
        graph.add_node("Decision Agent", decision_node)
        
        # Risk Agent (después de Decision)
        if self.enable_risk:
            risk_llm = self.llm_factory.get_llm_for_agent("risk_manager")
            risk_node = create_risk_agent_node(risk_llm, self.reasoning_bank)
            graph.add_node("Risk Agent", risk_node)
        
        # Añadir nodos opcionales
        if self.enable_sentiment:
            sentiment_llm = self.llm_factory.get_llm_for_agent("sentiment")
            sentiment_node = create_sentiment_agent_node(sentiment_llm, self.reasoning_bank)
            graph.add_node("Sentiment Agent", sentiment_node)
        
        if self.enable_visual:
            visual_llm = self.llm_factory.get_llm_for_agent("visual")
            visual_node = create_visual_agent_node(visual_llm, self.reasoning_bank)
            graph.add_node("Visual Agent", visual_node)
        
        # Definir flujo
        # Fase 1: Análisis paralelo (Technical, QABBA, Sentiment)
        graph.add_edge(START, "Technical Agent")
        graph.add_edge(START, "QABBA Agent")
        
        if self.enable_sentiment:
            graph.add_edge(START, "Sentiment Agent")
            graph.add_edge("Sentiment Agent", "Decision Agent")
        
        # Technical y QABBA van a Visual o Decision
        if self.enable_visual:
            graph.add_edge("Technical Agent", "Visual Agent")
            graph.add_edge("QABBA Agent", "Visual Agent")
            graph.add_edge("Visual Agent", "Decision Agent")
        else:
            graph.add_edge("Technical Agent", "Decision Agent")
            graph.add_edge("QABBA Agent", "Decision Agent")
        
        # Flujo de Decision a Risk (si habilitado) o END
        if self.enable_risk:
            graph.add_edge("Decision Agent", "Risk Agent")
            graph.add_edge("Risk Agent", END)
        else:
            graph.add_edge("Decision Agent", END)
        
        # Compilar con checkpointer para persistencia
        memory = MemorySaver()
        return graph.compile(checkpointer=memory)
    
    def invoke(
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
        Ejecuta el grafo de trading completo.
        
        Args:
            symbol: Símbolo del par (ej: "BTCUSDT")
            timeframe: Temporalidad (ej: "15m")
            indicators: Diccionario de indicadores técnicos
            current_price: Precio actual
            current_volume: Volumen actual
            obi: Order Book Imbalance
            cvd: Cumulative Volume Delta
            spread: Spread bid-ask
            orderbook_depth: Profundidad del order book
            mtf_context: Contexto multi-timeframe
            chart_image_b64: Imagen del gráfico en base64
            thread_id: ID del hilo para persistencia
            social_data: Diccionario con posts de Twitter/Reddit u otra data social
            fear_greed_value: Valor del Fear & Greed Index (string)
        
        Returns:
            Estado final con todas las decisiones
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

        config = {"configurable": {"thread_id": thread_id}}

        # Medir tiempo total y capturar en dashboard
        import time
        start_time = time.time()

        result = self.graph.invoke(initial_state, config)

        total_latency_ms = (time.time() - start_time) * 1000

        # Actualizar dashboard si está disponible
        if DASHBOARD_AVAILABLE:
            self._update_dashboard(result, total_latency_ms)

        return result

    def _update_dashboard(
        self, result: FenixAgentState, total_latency_ms: float
    ) -> None:
        """Actualiza el dashboard con los resultados del pipeline."""
        try:
            dashboard = get_dashboard()

            # Actualizar estados de agentes
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

            # Extraer señal final
            final_signal = None
            decision = result.get("final_trade_decision") or result.get(
                "decision_report"
            )
            if isinstance(decision, dict):
                final_signal = decision.get("final_decision") or decision.get("signal")

            # Registrar ejecución del pipeline
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
        """Versión asíncrona de invoke."""
        return await asyncio.to_thread(
            self.invoke,
            symbol=symbol,
            timeframe=timeframe,
            indicators=indicators,
            current_price=current_price,
            current_volume=current_volume,
            **kwargs,
        )
    
    def get_graph_visualization(self) -> str | None:
        """Retorna una visualización ASCII del grafo."""
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
    """Obtiene o crea el grafo de trading singleton."""
    global _trading_graph
    
    if _trading_graph is None or force_new:
        _trading_graph = FenixTradingGraph(llm_config=llm_config)
    
    return _trading_graph


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Test básico
    print("=== Fenix LangGraph Orchestrator ===")
    
    if not LANGGRAPH_AVAILABLE:
        print("❌ LangGraph no está instalado")
        print("   Ejecuta: pip install langgraph langchain-core")
        exit(1)
    
    # Crear configuración de prueba (Ollama local)
    from config.llm_provider_config import EXAMPLE_ALL_LOCAL
    
    print("✅ Creando grafo de trading...")
    trading_graph = FenixTradingGraph(
        llm_config=EXAMPLE_ALL_LOCAL,
        enable_visual=False,  # Desactivar para test sin imagen
        enable_sentiment=True,
    )
    
    # Visualizar grafo
    viz = trading_graph.get_graph_visualization()
    if viz:
        print("\n=== Estructura del Grafo ===")
        print(viz)
    
    # Ejecutar con datos de ejemplo
    print("\n=== Ejecutando análisis de prueba ===")
    
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
    
    print("\n=== Resultado Final ===")
    print(f"Decisión: {result.get('final_trade_decision', {}).get('final_decision', 'N/A')}")
    print(f"Confianza: {result.get('final_trade_decision', {}).get('confidence_in_decision', 'N/A')}")
    print(f"\nTiempos de ejecución: {result.get('execution_times', {})}")
    
    if result.get("errors"):
        print(f"\n⚠️ Errores: {result['errors']}")
