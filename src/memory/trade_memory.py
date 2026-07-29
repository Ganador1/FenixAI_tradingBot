# memory/trade_memory.py
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.memory.reasoning_bank import get_reasoning_bank
from src.security.private_files import (
    ensure_private_directory,
    read_private_text,
    write_private_text,
)
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class TradeMemory:
    def __init__(self, memory_file: str = "logs/trade_memory.json", max_trades: int = 100):
        self.memory_file = Path(memory_file)
        ensure_private_directory(self.memory_file.parent)
        if self.memory_file.is_symlink():
            raise ValueError("Trade memory file cannot be a symbolic link")
        if not 1 <= max_trades <= 10_000:
            raise ValueError("max_trades must be between 1 and 10000")
        self.max_trades = max_trades
        self.trades: list[dict[str, Any]] = []
        # Parámetros de reward shaping inspirados en ReasoningBank paper
        self.reward_clip = 2.0
        self.reward_scale_pct = 0.25  # 0.25% de movimiento = reward 1.0
        self.reward_scale_abs = 25.0  # $25 = reward 1.0 para escenarios sin entry_price
        self.near_miss_pct = 0.15  # +/-0.15% se considera near miss
        self.near_miss_abs = 5.0   # o +/-$5 cuando no hay entry_price
        self._load_memory()

    def _load_memory(self):
        """Carga trades anteriores del archivo"""
        if self.memory_file.exists():
            try:
                data = json.loads(
                    read_private_text(self.memory_file, max_bytes=16 * 1024 * 1024)
                )
                trades = data.get("trades", []) if isinstance(data, dict) else []
                if not isinstance(trades, list):
                    raise ValueError("Trade memory trades must be a list")
                self.trades = [trade for trade in trades if isinstance(trade, dict)][
                    -self.max_trades:
                ]
            except Exception as e:
                logger.error(f"Error cargando memoria de trades: {e}")
                self.trades = []

    def save_trade(self, trade_data: dict[str, Any]):
        """Guarda un nuevo trade en memoria"""
        trade_record = {
            'timestamp': datetime.now().isoformat(),
            'symbol': trade_data.get('symbol'),
            'side': trade_data.get('side'),
            'entry_price': trade_data.get('entry_price'),
            'exit_price': trade_data.get('exit_price'),
            'pnl': trade_data.get('pnl'),
            'decision_context': {
                'sentiment': trade_data.get('decision_context', {}).get('sentiment_analysis', {}),
                'technical': trade_data.get('decision_context', {}).get('numerical_technical_analysis', {}),
                'visual': trade_data.get('decision_context', {}).get('visual_technical_analysis', {}),
                'qabba': trade_data.get('decision_context', {}).get('qabba_validation_analysis', {}),
                'final_decision': trade_data.get('decision_context', {}).get('final_decision_output', {})
            },
            'risk_assessment': trade_data.get('decision_context', {}).get('risk_assessment', {}),
            'market_conditions': trade_data.get('market_conditions', {})
        }

        self.trades.append(trade_record)
        if len(self.trades) > self.max_trades:
            self.trades = self.trades[-self.max_trades:]

        self._save_to_file()
        # Nuevo: Etiquetado automático de ReasoningBank (self-judgment)
        try:
            self._label_reasoning_entries(trade_record)
        except Exception as e:
            logger.debug(f"Error etiquetando ReasoningBank: {e}")

    def _save_to_file(self):
        """Guarda la memoria en archivo"""
        try:
            write_private_text(
                self.memory_file,
                json.dumps({
                    'last_updated': datetime.now().isoformat(),
                    'trades': self.trades
                }, indent=2),
            )
        except Exception as e:
            logger.error(f"Error guardando memoria: {e}")

    def _label_reasoning_entries(self, trade_record: dict[str, Any]):
        """Etiqueta las entradas relevantes del ReasoningBank con el outcome del trade.

        Busca la última entrada por agente y la actualiza con success/reward/trade_id.
        """
        try:
            bank = get_reasoning_bank()
        except Exception:
            logger.debug("ReasoningBank unavailable - skipping labeling")
            return

        # Determinar éxito a partir del P&L guardado en trade_record
        pnl = trade_record.get('pnl', None)
        if pnl is None:
            logger.debug("Trade record no contiene pnl - skipping ReasoningBank labeling")
            return
        success = pnl > 0
        reward = float(pnl)
        trade_id = trade_record.get('trade_id') or trade_record.get('timestamp')
        reward_signal, near_miss, reward_notes = self._compute_reward_annotations(
            trade_record.get('entry_price'),
            reward
        )

        # Agentes a etiquetar si se encuentran en decision_context
        decision_ctx = trade_record.get('decision_context', {})
        agents_to_label = []
        for key in ['sentiment', 'technical', 'visual', 'qabba', 'final_decision_output']:
            if key in decision_ctx and decision_ctx.get(key):
                # Map keys to agent naming in ReasoningBank
                if key == 'final_decision_output':
                    agents_to_label.append('general')
                else:
                    agents_to_label.append(key)

        for agent_name in agents_to_label:
            recent = bank.get_recent(agent_name, limit=5)
            if not recent:
                logger.debug(f"No recent entries to label for {agent_name}")
                continue
            # Tomar la entrada más reciente y actualizarla
            latest = recent[-1]
            bank.update_entry_outcome(
                agent_name,
                latest.prompt_digest,
                success,
                reward,
                trade_id,
                reward_signal=reward_signal,
                near_miss=near_miss,
                reward_notes=reward_notes,
            )
            logger.info(
                "Labeled reasoning entry %s for %s as %s (pnl %.2f, reward_signal %.2f)%s",
                latest.prompt_digest[:8],
                agent_name,
                "success" if success else "failure",
                reward,
                reward_signal,
                " [near miss]" if near_miss else "",
            )

    def _compute_reward_annotations(
        self,
        entry_price: float | None,
        pnl: float,
    ) -> tuple[float, bool, str]:
        percent_move: float | None = None
        if entry_price:
            try:
                percent_move = (pnl / entry_price) * 100.0
            except ZeroDivisionError:
                percent_move = None

        if percent_move is not None:
            near_miss = abs(percent_move) <= self.near_miss_pct
            baseline = self.reward_scale_pct or 0.25
            reward_signal = percent_move / baseline
        else:
            near_miss = abs(pnl) <= self.near_miss_abs
            baseline_abs = self.reward_scale_abs or 25.0
            reward_signal = pnl / baseline_abs

        reward_signal = max(-self.reward_clip, min(self.reward_clip, reward_signal))
        notes = [f"abs_pnl={pnl:.4f}"]
        if percent_move is not None:
            notes.append(f"pct_move={percent_move:.4f}")
        if near_miss:
            notes.append("near_miss=True")
        notes.append(f"reward_signal={reward_signal:.3f}")
        return reward_signal, near_miss, ";".join(notes)

    def get_recent_trades(self, hours: int = 24) -> list[dict[str, Any]]:
        """Obtiene trades recientes"""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []
        for trade in self.trades:
            try:
                trade_time = datetime.fromisoformat(trade['timestamp'])
                if trade_time > cutoff:
                    recent.append(trade)
            except (KeyError, TypeError, ValueError):
                continue
        return recent

    def get_performance_summary(self) -> dict[str, Any]:
        """Resumen de performance basado en memoria"""
        if not self.trades:
            return {'total_trades': 0, 'win_rate': 0, 'avg_pnl': 0}

        wins = sum(1 for t in self.trades if t.get('pnl', 0) > 0)
        total = len(self.trades)
        total_pnl = sum(t.get('pnl', 0) for t in self.trades)

        return {
            'total_trades': total,
            'win_rate': (wins / total) * 100 if total > 0 else 0,
            'avg_pnl': total_pnl / total if total > 0 else 0,
            'total_pnl': total_pnl,
            'recent_trades': self.get_recent_trades(24)
        }

    def get_similar_contexts(self, current_context: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        """Encuentra trades anteriores con contexto similar"""
        similar_trades = []

        current_sentiment = current_context.get('sentiment_analysis', {}).get('overall_sentiment')
        current_technical = current_context.get('numerical_technical_analysis', {}).get('signal')

        for trade in reversed(self.trades):  # Más recientes primero
            trade_sentiment = trade.get('decision_context', {}).get('sentiment', {}).get('overall_sentiment')
            trade_technical = trade.get('decision_context', {}).get('technical', {}).get('signal')

            if trade_sentiment == current_sentiment and trade_technical == current_technical:
                similar_trades.append(trade)
                if len(similar_trades) >= limit:
                    break

        return similar_trades
