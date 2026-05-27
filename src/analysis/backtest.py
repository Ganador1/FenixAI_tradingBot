# backtesting_fenix.py
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy  # type: ignore

# Ollama and instructor are imported by agents. No global patch here.

# --- Configuración de Logging ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FenixBacktest")

# --- Importar Agentes y sus Modelos Pydantic de Output (Nombres Corregidos) ---
from src.agents.decision import EnhancedDecisionAgent, FinalDecisionOutput
from src.agents.enhanced_qabba_agent import EnhancedQABBAAgent
from src.agents.enhanced_qabba_agent import EnhancedQabbaOutput as QABBAAnalysisOutput
from src.agents.enhanced_technical_analyst import EnhancedTechnicalAnalyst
from src.agents.enhanced_technical_analyst import (
    EnhancedTechnicalOutput as EnhancedTechnicalAnalysisOutput,
)
from src.agents.risk import AdvancedRiskManager, RiskAssessment
from src.agents.sentiment_enhanced import EnhancedSentimentAnalyst, SentimentOutput
from src.agents.visual_analyst_enhanced import (
    EnhancedVisualAnalystAgent,
    EnhancedVisualChartAnalysisOutput,
)
from src.tools.technical_tools import add_kline as tt_add_kline
from src.tools.technical_tools import close_buf, high_buf, low_buf, vol_buf
from src.tools.technical_tools import get_current_indicators as tt_get_current_indicators
from src.tools.technical_tools import get_indicator_sequences as tt_get_indicator_sequences

# --- Constantes y Configuración ---
SYMBOL_FOR_BACKTEST = "TEST_SYMBOL_BT"
INITIAL_CASH_BT = 100000.0
COMMISSION_BPS_BT = 0.0004
TRADE_LOG_PERFORMANCE_PATH_BT = Path("logs/fenix_tradelog_performance_backtest.jsonl")
TRADE_LOG_PERFORMANCE_PATH_BT.parent.mkdir(parents=True, exist_ok=True)

MOCK_SENTIMENT_LLM = True
MOCK_VISUAL_LLM = True
MOCK_QABBA_LLM = True
MOCK_TECHNICAL_LLM = True
MOCK_DECISION_LLM = True


# --- Funciones Mock (sin cambios, asumiendo que están bien) ---
def get_mock_sentiment_output() -> SentimentOutput:
    return SentimentOutput(
        overall_sentiment="NEUTRAL",
        positive_texts_count=5,
        negative_texts_count=5,
        neutral_texts_count=10,
        reasoning="Mocked sentiment",
        confidence_score=0.7,
        fear_greed_value_used=50,
        fear_greed_influence_factor=0.0,
        avg_data_quality_score=0.6,
        total_texts_analyzed_by_llm=20,
        total_texts_fetched_initially=100,
        top_keywords_found=["mock"],
        sentiment_trend_short_term="STABLE",
    )


def get_mock_technical_analysis_output(
    metrics: dict[str, float],
) -> EnhancedTechnicalAnalysisOutput:
    signal: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    rsi = metrics.get("rsi", 50.0)
    if rsi < 35.0:
        signal = "BUY"
    elif rsi > 65.0:
        signal = "SELL"
    return EnhancedTechnicalAnalysisOutput(
        signal=signal,
        reasoning=f"Mocked RSI ({rsi:.2f}).",
        confidence_level="MEDIUM",
        key_patterns_observed=["Mock_RSI"],
        temporal_analysis="Mocked LLM4FTS.",
    )


def get_mock_visual_analysis_output() -> EnhancedVisualChartAnalysisOutput:
    return EnhancedVisualChartAnalysisOutput(
        action="HOLD",
        confidence=0.5,
        reason="Mocked visual analysis output for backtesting.",
        overall_visual_assessment="NEUTRAL",
        key_candlestick_patterns=["Doji"],
        chart_patterns=["Channel"],
        trend_analysis={"direction": "SIDEWAYS"},
        indicator_interpretation={"RSI": "Neutral"},
        volume_analysis={"trend": "Average"},
        support_resistance_levels={"support": [95.0], "resistance": [105.0]},
        pattern_clarity_score=0.6,
        suggested_action_based_on_visuals="WAIT_CONFIRMATION",
        chart_timeframe_analyzed="1m",
        main_elements_focused_on=["RSI"],
        next_candle_prediction=None,
        supertrend_analysis=None,
        montecarlo_analysis=None,
        price_evolution_context=None,
        pattern_probability_scores=None,
    )


def get_mock_qabba_output() -> QABBAAnalysisOutput:
    return QABBAAnalysisOutput(
        qabba_signal="HOLD_QABBA", qabba_confidence=0.50, reasoning_short="Mocked QABBA."
    )


def get_mock_final_decision_output(s_out, t_out, v_out, q_out) -> FinalDecisionOutput:
    decision: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    if t_out.signal == "BUY" and s_out.overall_sentiment == "POSITIVE":
        decision = "BUY"
    elif t_out.signal == "SELL" and s_out.overall_sentiment == "NEGATIVE":
        decision = "SELL"
    return FinalDecisionOutput(
        final_decision=decision,
        combined_reasoning="Mocked decision.",
        confidence_in_decision="MEDIUM",
    )


class FenixMultiAgentStrategy(Strategy):
    def init(self):
        logger.info("Inicializando FenixMultiAgentStrategy para backtesting...")
        self.sentiment_agent = EnhancedSentimentAnalyst()
        self.technical_agent = EnhancedTechnicalAnalyst()
        self.visual_agent = EnhancedVisualAnalystAgent()
        self.qabba_agent = EnhancedQABBAAgent()
        self.decision_agent = EnhancedDecisionAgent()
        self.risk_manager = AdvancedRiskManager(
            symbol_tick_size=0.01, symbol_step_size=0.001, min_notional=5.0
        )

        close_buf.clear()
        high_buf.clear()
        low_buf.clear()
        vol_buf.clear()
        try:
            from tools.technical_tools import macd_line_buf, rsi_buf

            if "rsi_buf" in globals() and isinstance(rsi_buf, deque):
                rsi_buf.clear()
            if "macd_line_buf" in globals() and isinstance(macd_line_buf, deque):
                macd_line_buf.clear()
        except ImportError:
            pass
        self.trade_counter_bt = 0

    def next(self):
        current_close = self.data.Close[-1]
        current_high = self.data.High[-1]
        current_low = self.data.Low[-1]
        current_volume = self.data.Volume[-1] if "Volume" in self.data.df.columns else 0
        tt_add_kline(current_close, current_high, current_low, current_volume)
        if len(close_buf) < 50:
            return

        current_tech_metrics = tt_get_current_indicators()
        if not current_tech_metrics:
            return
        current_tech_metrics["last_price"] = current_close

        indicator_sequences = tt_get_indicator_sequences(
            sequence_length=getattr(self.technical_agent, "_sequence_length_for_llm4fts", 15)
        )

        sentiment_result = (
            get_mock_sentiment_output() if MOCK_SENTIMENT_LLM else self.sentiment_agent.run()
        )

        technical_result: EnhancedTechnicalAnalysisOutput
        if MOCK_TECHNICAL_LLM:
            technical_result = get_mock_technical_analysis_output(current_tech_metrics)
        else:
            technical_result = self.technical_agent.run(
                current_tech_metrics=current_tech_metrics,
                indicator_sequences=indicator_sequences,
                sentiment_label=sentiment_result.overall_sentiment,
            )

        visual_result = (
            get_mock_visual_analysis_output()
            if MOCK_VISUAL_LLM
            else self.visual_agent.run(
                SYMBOL_FOR_BACKTEST,
                "1m_bt",
                deque(close_buf),
                deque(high_buf),
                deque(low_buf),
                deque(vol_buf),
                current_tech_metrics,
            )
        )

        qabba_result = (
            get_mock_qabba_output()
            if MOCK_QABBA_LLM
            else self.qabba_agent.get_qabba_analysis(
                tech_metrics=current_tech_metrics, price_data_sequence=list(close_buf)
            )
        )

        final_decision_obj: FinalDecisionOutput
        if MOCK_DECISION_LLM:
            final_decision_obj = get_mock_final_decision_output(
                sentiment_result, technical_result, visual_result, qabba_result
            )
        else:
            final_decision_obj = self.decision_agent.run(
                sentiment_analysis=sentiment_result,
                numerical_technical_analysis=technical_result,
                visual_technical_analysis=visual_result,
                qabba_validation_analysis=qabba_result,
                current_tech_metrics=current_tech_metrics,
            )

        risk_assessment: RiskAssessment = self.risk_manager.run(
            proposal_decision=final_decision_obj.final_decision,
            current_balance=self.equity,
            tech_metrics=current_tech_metrics,
        )

        if final_decision_obj.final_decision == "BUY":
            if self.position.is_short:
                self.position.close()
            if (
                not self.position
                and risk_assessment.verdict == "APPROVE"
                and risk_assessment.order_details
            ):
                self.buy(
                    sl=risk_assessment.order_details.stop_loss_price,
                    tp=risk_assessment.order_details.take_profit_price,
                )
                self.trade_counter_bt += 1
        elif final_decision_obj.final_decision == "SELL":
            if self.position.is_long:
                self.position.close()
            if (
                not self.position
                and risk_assessment.verdict == "APPROVE"
                and risk_assessment.order_details
            ):
                self.sell(
                    sl=risk_assessment.order_details.stop_loss_price,
                    tp=risk_assessment.order_details.take_profit_price,
                )
                self.trade_counter_bt += 1

        log_entry_bt = {
            "timestamp_decision_utc": pd.Timestamp(self.data.index[-1])
            .tz_localize("UTC")
            .isoformat()
            if hasattr(self.data.index[-1], "tz_localize")
            else pd.Timestamp(self.data.index[-1]).isoformat(),
            "symbol": SYMBOL_FOR_BACKTEST,
            "final_decision_from_agent": final_decision_obj.final_decision,
            "risk_manager_verdict": risk_assessment.verdict,
            "risk_manager_reason": risk_assessment.reason,
            "decision_context": {
                "sentiment_analysis": sentiment_result.model_dump(mode="json"),
                "numerical_technical_analysis": technical_result.model_dump(mode="json"),
                "visual_technical_analysis": visual_result.model_dump(mode="json"),
                "qabba_validation_analysis": qabba_result.model_dump(mode="json"),
                "final_decision_output": final_decision_obj.model_dump(mode="json"),
                "raw_tech_metrics_at_decision": current_tech_metrics,
                "risk_calculation_details": risk_assessment.order_details.model_dump(mode="json")
                if risk_assessment.order_details
                else None,
            },
        }
        try:
            with open(TRADE_LOG_PERFORMANCE_PATH_BT, "a") as f:
                f.write(json.dumps(log_entry_bt) + "\n")
        except Exception as e:
            logger.error(f"Error escribiendo log de performance BT: {e}")


if __name__ == "__main__":
    logger.info("Iniciando Backtesting para Fénix Bot...")
    try:
        data_df = pd.read_csv("data/BTCUSDT_1m_recent.csv", index_col="Timestamp", parse_dates=True)
        data_df.columns = [col.capitalize() for col in data_df.columns]
        logger.info(f"Cargados datos de data/BTCUSDT_1m_recent.csv: {len(data_df)} filas")
        if data_df.empty:
            raise FileNotFoundError
    except FileNotFoundError:
        logger.warning("Archivo data/BTCUSDT_1m_recent.csv no encontrado. Usando datos de ejemplo.")
        n_rows = 2000
        base_time = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
        idx = pd.to_datetime([base_time + pd.Timedelta(minutes=i) for i in range(n_rows)])
        open_prices = np.full(n_rows, 100.0)
        for i in range(1, n_rows):
            open_prices[i] = open_prices[i - 1] + np.random.normal(0, 0.5)
        data_df = pd.DataFrame(index=idx)
        data_df["Open"] = open_prices
        data_df["High"] = data_df["Open"] + np.random.uniform(0, 2, n_rows)
        data_df["Low"] = data_df["Open"] - np.random.uniform(0, 2, n_rows)
        data_df["Close"] = (
            data_df["Open"] + data_df["High"] + data_df["Low"] + data_df["Open"]
        ) / 4 + np.random.uniform(-0.5, 0.5, n_rows)
        data_df["Volume"] = np.random.uniform(1000, 10000, n_rows)
        data_df["High"] = np.maximum(data_df["High"], data_df[["Open", "Close"]].max(axis=1))
        data_df["Low"] = np.minimum(data_df["Low"], data_df[["Open", "Close"]].min(axis=1))
        data_df["High"] = np.maximum(data_df["High"], data_df["Low"] + 0.001)
        data_df.index.name = "Timestamp"

    if data_df.empty:
        logger.critical("No se cargaron datos para el backtest. Saliendo.")
        exit(1)
    if not isinstance(data_df.index, pd.DatetimeIndex):
        logger.critical("Índice no es DatetimeIndex.")
        exit(1)
    if TRADE_LOG_PERFORMANCE_PATH_BT.exists():
        TRADE_LOG_PERFORMANCE_PATH_BT.unlink()
    bt = Backtest(
        data_df,
        FenixMultiAgentStrategy,
        cash=INITIAL_CASH_BT,
        commission=COMMISSION_BPS_BT,
        trade_on_close=True,
        exclusive_orders=True,
    )
    logger.info("Ejecutando backtest...")
    stats = bt.run()
    logger.info("Backtest completado.")
    print("\n--- Estadísticas del Backtest Fénix ---")
    print(stats)
    logger.info(f"Log de decisiones detalladas guardado en: {TRADE_LOG_PERFORMANCE_PATH_BT}")
    try:
        plot_filename = "logs/fenix_backtest_plot.html"
        bt.plot(filename=plot_filename, open_browser=False)
        logger.info(f"Gráfico del backtest guardado en: {plot_filename}")
    except Exception as e:
        logger.warning(f"No se pudo generar el gráfico del backtest: {e}.")
