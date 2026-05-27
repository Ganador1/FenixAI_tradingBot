# FenixAI v2.5.0 Release Checklist

Status: completed. The release is published.

## Documentation

- [x] README presents v2.5 as an official release, not as a proven profitable system.
- [x] `docs/CHANGELOG.md` has an updated v2.5.0 section.
- [x] `docs/releases/v2.5.md` summarizes the official release.
- [ ] `docs/releases/v2.5-development-history.md` explains the v2.0 -> v2.5 work clearly and does not overstate profitability.
- [ ] `docs/releases/v2.5-new-systems.md` explains NanoFenix, MiniFenix, Fenix Experimental, new agent paths, model-role teams, and live runners clearly.
- [ ] `docs/releases/v2.5-script-evolution.md` explains the v25/v31 launcher lineage and separates supported entry points from internal canaries and historical experiments.
- [ ] `scripts/README.md` identifies supported launchers and marks historical/fix scripts as non-public entry points.
- [ ] NanoFenix public docs are in English.
- [ ] MiniFenix public docs are in English.
- [ ] Old internal Spanish analysis files are either archived, translated, or intentionally excluded.
- [ ] Public docs separate proven hardening work from experimental model/NanoFenix research.

## Safety

- [ ] `.env` and private key files are not included in the release package.
- [ ] Logs do not expose account IDs, API keys, balances that should stay private, or private file paths.
- [ ] Live trading still requires explicit live-mode confirmation flags.
- [ ] Paper mode remains the recommended default.

## Verification

```bash
pytest tests/test_hybrid_runner.py tests/test_engine_position_management.py tests/test_executor_clamp.py tests/test_nanofenixv3_executor.py tests/test_run_fenix_live_slot.py tests/test_lite_pipeline_parallelism.py tests/test_technical_tools_stochastic.py -q
python -m py_compile scripts/run_hybrid_live_paper.py scripts/run_fenix_live_slot.py scripts/run_fenix_live_suite.py nanofenixv3/adaptive_fusion.py nanofenixv3/executor.py run_nanofenixv3.py src/trading/engine.py src/trading/market_data.py src/tools/technical_tools.py
```

Run the full suite before publishing:

```bash
pytest -q
ruff check src scripts nanofenixv3
```

## Live/paper review

- [ ] Latest paper run uses valid entry and exit prices.
- [ ] Paper and testnet slots use isolated runtime-risk state instead of inheriting prior live PnL.
- [ ] Latest live run hydrates existing Binance positions correctly after restart.
- [ ] Latest live slot summary accounts for hydrated-position closes without reporting a false `completed_with_accounting_gap`.
- [ ] Failed executions are visible in logs but do not pollute realized-loss accounting.
- [ ] Protective order checks include Binance Futures algorithmic orders.
- [ ] NanoFenix trailing exits account for estimated fees.
- [ ] Latest live canary outcome is documented honestly, including losses, fees, and unresolved strategy issues.

Latest local paper verification snapshots:

- Short release suite: `20260511_005135_v25_release_massive_paper_fixedrisk`, plan `plans/v25_release_massive_paper_20260510.json`. Result: 5/5 SOLUSDT paper slots completed OK across 1m, 3m, 5m, 15m, and 30m, with 0 simulated trades and one NanoFenix hard veto on the only BUY signal.
- Longer MTF/NanoFenix suite: `20260517_224826_v25_long_paper_mtf_SOLUSDT_20260517_184816`, plan `plans/v25_long_paper_mtf_20260517.json`. Result: 6/6 SOLUSDT paper slots completed OK across 1m, 3m, 5m, 15m, 30m, and 1h, but it exposed a paper-sizing defect: 54 SELL decisions and 30 filter blocks produced 0 simulated trades because runtime risk/current balance stayed at `0.0` and entries were skipped as non-positive notional.
- Follow-up long suite after paper sizing fix: `20260522_194204_v25_long_paper_mtf_SOLUSDT_20260522_154156`, plan `plans/v25_long_paper_mtf_20260517.json`. Result: 6/6 SOLUSDT paper slots completed OK and the zero-balance/non-positive-notional defect did not recur, but all 85 decisions were HOLD with 0 simulated trades. Review found the suite plan had not explicitly transported `FENIX_STRICT_MTF_BIAS_TIMEFRAME`, so the MTF guard recorded `_mtf_bias: {}` and blocked as `mtf=HOLD(0.00)`.
- May 24 fix: `scripts/run_fenix_live_suite.py`, `scripts/run_fenix_live_slot.py`, and `plans/v25_long_paper_mtf_20260517.json` now carry explicit lite/MTF guard settings. The engine also reports missing MTF configuration and prevents Technical timeout fallbacks from enabling the optional MTF+QABBA entry path.
- Follow-up requirement: rerun a long paper suite with the explicit MTF fields active and document whether simulated fills, risk accounting, and NanoFenix policy behavior are coherent.

## Release decision

- [ ] At least one ranging-market session has been reviewed.
- [ ] At least one trending-market session has been reviewed.
- [ ] Known blockers have either been fixed or documented as limitations.
- [ ] Script surface has been reviewed so production launchers, analysis tools, fixes, and historical experiments are not mixed together in public guidance.
- [ ] The final package has been reviewed manually before any public upload.
