#!/usr/bin/env python3
"""
🦅 FENIX V3 - PRODUCTION TESTNET RUNNER

Ejecuta el bot completo en producción con Binance Futures Testnet.
Analiza todas las respuestas de agentes y verifica ejecución de órdenes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading.engine import TradingEngine
from src.core.langgraph_orchestrator import validate_agent_response
from src.services.binance_service import BinanceService

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/fenix_v3_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
    ],
)

logger = logging.getLogger("FenixV3Test")


class FenixV3TestRunner:
    """Runner de prueba en producción con Testnet."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "1m",
        cycles: int = 5,
        use_testnet: bool = True,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.cycles = cycles
        self.use_testnet = use_testnet
        self.results: list[dict[str, Any]] = []
        self.order_results: list[dict[str, Any]] = []

        self.engine = TradingEngine(
            symbol=symbol,
            timeframe=timeframe,
            use_testnet=use_testnet,
            paper_trading=True,
            enable_visual_agent=False,  # Desactivado por velocidad en 1min
            enable_sentiment_agent=False,
            allow_live_trading=False,
        )
        self.binance = BinanceService(use_testnet=use_testnet)

    async def run_single_cycle(self, cycle_num: int) -> dict[str, Any]:
        """Ejecuta un ciclo completo de análisis."""
        logger.info(f"\n{'='*80}")
        logger.info(f"🔄 CICLO {cycle_num}/{self.cycles} - {self.symbol}@{self.timeframe}")
        logger.info(f"{'='*80}")

        cycle_start = time.time()

        try:
            # 1. Obtener datos de mercado
            logger.info("📊 Obteniendo datos de mercado...")
            klines = await self.binance.get_klines(self.symbol, self.timeframe, limit=100)
            current_price = float(klines[-1][4])
            logger.info(f"   Precio actual: ${current_price:,.2f}")

            # 2. Calcular indicadores
            from src.tools.technical_tools import TechnicalAnalysisTools
            tech_tools = TechnicalAnalysisTools()
            indicators = await tech_tools.get_all_indicators(self.symbol, self.timeframe, limit=100)
            logger.info(f"   RSI={indicators.get('rsi', 0):.1f}, MACD={indicators.get('macd_line', 0):.2f}")

            # 3. Ejecutar grafo
            logger.info("🤖 Ejecutando grafo de agentes...")
            graph_start = time.time()

            if not self.engine._trading_graph:
                await self.engine.initialize()

            result = await self.engine._trading_graph.ainvoke(
                symbol=self.symbol,
                timeframe=self.timeframe,
                indicators=indicators,
                current_price=current_price,
                current_volume=indicators.get("volume", 0),
                obi=indicators.get("order_book_imbalance", 1.0),
                cvd=indicators.get("cvd", 0),
                spread=indicators.get("spread", 0.01),
                thread_id=f"test_cycle_{cycle_num}_{int(time.time())}",
            )

            graph_latency = (time.time() - graph_start) * 1000

            # 4. Analizar resultados
            agent_reports = {
                "technical": result.get("technical_report", {}),
                "qabba": result.get("qabba_report", {}),
                "decision": result.get("decision_report", {}),
                "risk": result.get("risk_assessment", {}),
            }

            analysis = {}
            for agent, report in agent_reports.items():
                if not report:
                    continue
                errors = validate_agent_response(agent, report)
                signal = report.get("signal") or report.get("final_decision") or report.get("verdict")
                confidence = report.get("confidence_level") or report.get("confidence")

                analysis[agent] = {
                    "signal": signal,
                    "confidence": confidence,
                    "validation_passed": len(errors) == 0,
                    "errors": errors,
                    "attempts": report.get("_attempts", 1),
                }

                status = "✅" if len(errors) == 0 else "❌"
                logger.info(f"   {status} {agent:12} | {signal:8} | {confidence} | {len(errors)} errors")

            # 5. Decisión final
            decision = result.get("final_trade_decision", {})
            final_decision = decision.get("final_decision", "HOLD")
            logger.info(f"\n🎯 DECISIÓN: {final_decision}")

            # 6. Simular orden
            order_result = None
            if final_decision in ["BUY", "SELL"]:
                order_result = {
                    "simulated": True,
                    "side": final_decision,
                    "symbol": self.symbol,
                    "quantity": 0.001,
                    "price": current_price,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info(f"   📤 Orden simulada: {final_decision} 0.001 BTC @ ${current_price:,.2f}")
                self.order_results.append(order_result)

            cycle_time = (time.time() - cycle_start) * 1000
            logger.info(f"\n⏱️  Tiempo: {cycle_time:.0f}ms (graph: {graph_latency:.0f}ms)")

            return {
                "cycle": cycle_num,
                "price": current_price,
                "analysis": analysis,
                "decision": final_decision,
                "latency_ms": cycle_time,
            }

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return {"cycle": cycle_num, "error": str(e)}

    async def run_full_test(self):
        """Ejecuta test completo."""
        logger.info("\n" + "="*80)
        logger.info("🦅 FENIX V3 - TESTNET PRODUCTION TEST")
        logger.info("="*80)

        await self.engine.initialize()

        for i in range(1, self.cycles + 1):
            result = await self.run_single_cycle(i)
            self.results.append(result)
            if i < self.cycles:
                await asyncio.sleep(30)  # Esperar entre ciclos

        return self._generate_summary()

    def _generate_summary(self):
        """Genera resumen."""
        logger.info("\n" + "="*80)
        logger.info("📊 RESUMEN")
        logger.info("="*80)

        total = len(self.results)
        successful = sum(1 for r in self.results if "error" not in r)
        logger.info(f"Ciclos: {successful}/{total}")

        decisions = [r.get("decision", "HOLD") for r in self.results]
        logger.info(f"BUY: {decisions.count('BUY')}, SELL: {decisions.count('SELL')}, HOLD: {decisions.count('HOLD')}")
        logger.info(f"Órdenes simuladas: {len(self.order_results)}")

        # Guardar
        output = f"logs/fenix_v3_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output, "w") as f:
            json.dump({"results": self.results, "orders": self.order_results}, f, indent=2)
        logger.info(f"💾 Guardado: {output}")

        return {"success": successful == total, "file": output}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--cycles", type=int, default=3)
    args = parser.parse_args()

    runner = FenixV3TestRunner(args.symbol, args.timeframe, args.cycles)
    result = await runner.run_full_test()
    print(f"\n✅ Test completado: {result['file']}")


if __name__ == "__main__":
    asyncio.run(main())
