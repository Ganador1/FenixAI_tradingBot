# FenixAI Scripts Index

Status: release-candidate script map for v2.5.

This directory contains both current tooling and many historical experiment scripts. Not every script in this directory is part of the public v2.5 release surface.

For the narrative explanation of how the multi-month v2.5 script families evolved, see
[`docs/releases/v2.5-script-evolution.md`](../docs/releases/v2.5-script-evolution.md).

## Recommended v2.5 Entry Points

Use these first when running or testing the current engine.

| Script | Purpose | Notes |
| --- | --- | --- |
| `run_fenix_live_slot.py` | Run one engine-first slot in paper, testnet, or live mode. | Preferred for controlled live-like tests. Writes `logs/live_slot_events_*.jsonl` and `logs/live_slot_summary_*.json`. |
| `run_fenix_live_suite.py` | Run a JSON plan of engine-first slots. | Useful for sequential model/timeframe comparison. Plan files can now declare lite/MTF guard settings explicitly. |
| `run_hybrid_live_paper.py` | Multi-timeframe hybrid paper/live comparison runner. | Useful for MTF research; not the primary live runner. |
| `run_chart_service.py` | Start chart service tooling. | Support utility for visual/chart workflows. |

## Current Internal Live Canary Template

| Script | Purpose | Notes |
| --- | --- | --- |
| `launch_fenix_v31_live_mtf_safe.sh` | Latest internal SOLUSDT v31 live-safe canary launcher. | Starts NanoFenix v3 observer-only companion plus one `run_fenix_live_slot.py` live slot with 15m entry, 30m deterministic MTF guard, conservative risk caps, and `technical_mtf_qabba_guard`. Treat as an internal template, not a generic public live command. |
| `launch_fenix_v25_live_mtf_safe.sh` | Superseded v25 live-safe MTF launcher. | Kept as the first May 2026 live-safe SOLUSDT template and fallback reference. Prefer the v31 live-safe launcher for the current canary family. |

`run_fenix_live_slot.py` summary accounting treats both new `position:opened` events and startup `position:hydrated` events as valid closeable position context. This prevents a correct reduce-only close of inherited exchange exposure from being reported as a false accounting gap.

For MTF/NanoFenix release checks, do not rely on inherited shell variables. Prefer explicit slot/suite options such as `lite_consensus_mode`, `strict_mtf_bias_timeframe`, `strict_mtf_opposing_veto_conf`, `lite_mtf_confirm_conf`, `lite_mtf_qabba_min_conf`, and `lite_allow_mtf_qabba_when_tech_hold`. `run_fenix_live_slot.py` prints these as `experiment_overrides` so a completed run can prove which guard stack was actually active.

## Historical Evolution Map

Use this map to understand where older scripts fit before promoting or deleting them.

| Family | Representative scripts | Historical role | v2.5 status |
| --- | --- | --- | --- |
| Baseline root runners | `../run_fenix.py`, `../run_nanofenix*.py`, `../run_minifenix*.py` | Original simple entry points and research runners. | Keep `run_fenix.py` and `run_nanofenixv3.py` prominent; older root runners are lineage. |
| Benchmark factory | `run_benchmark_suite.py`, `generate_experiment_plans.py`, `analyze_benchmark_results.py` | February slot plans, model comparisons, monolithic vs multi-agent tests, and directional scoring. | Research support, not normal launch commands. |
| v21 and team tests | `launch_v21_parallel.sh`, `run_v21_*.sh`, `run_team_*.sh` | Role-specific model/team validation. | Reproducibility artifacts. |
| R-series live hardening | `launch_fenix_r13.sh`, `launch_fenix_r15_*.sh`, `launch_fenix_r16_*.sh`, `launch_fenix_testnet_r17_solusdc.sh` | Early live/testnet safety work: locks, directional guards, strict Technical+QABBA, and MTF bias. | Historical launcher family; use `run_fenix_live_slot.py` for new controlled slots. |
| NanoFenix lineage | `launch_nanofenix_r*.sh`, `start_nanofenix_long_run.sh`, `run_nanofenix_companion_signal.sh` | Companion-signal experimentation before the current v3 path. | Prefer `../run_nanofenixv3.py --companion --adaptive-fusion`. |
| v25-v28 canaries | `launch_fenix_v25_*.sh`, `launch_fenix_v26_*.sh`, `launch_fenix_v27_*.sh`, `launch_fenix_v28_*.sh` | April/May live and paper canaries for ETH/SOL, protective orders, NanoFenix vetoes, hydration, and screen-based runs. | Document as internal templates or history. |
| v31 MTF safe | `launch_fenix_v31_live_mtf_safe.sh` | Current internal SOLUSDT live-safe canary built on the live slot runner. | Latest internal template, still not a generic public command. |

## Root-Level Research Entry Points

These live at the project root, not under `scripts/`, but they are part of the v2.5 research surface.

| Entry point | Purpose | Notes |
| --- | --- | --- |
| `run_nanofenixv3.py` | Run NanoFenix v3.5 standalone or as a Fenix companion signal. | Preferred NanoFenix v2.5 path. Use `--companion --adaptive-fusion` for companion research. |
| `run_minifenix.py` | Run the MiniFenix slow-brain/fast-trigger prototype. | Research prototype only; not the primary live runner. |
| `run_minifenix_model_sweep.py` | Compare slow-brain LLM choices for MiniFenix. | Useful for research logs and model selection experiments. |

## Release and Maintenance

| Script | Purpose |
| --- | --- |
| `release_cleanup.sh` | Local release cleanup helper. Dry-run by default, uses no git commands, and can quarantine generated artifacts with `--apply` before packaging a public release. |
| `generate_secrets_baseline.sh` | Generate/update secrets scanning baseline. |
| `run_md_lint.sh` | Markdown lint helper. |
| `link_check_docs.sh` | Documentation link check helper. |
| `update_doc_references.sh` | Documentation reference update helper. |
| `manage_migrations.py` | Alembic migration wrapper. |
| `verify_migrations.py` | Migration verification helper. |
| `verify_precision.py` | Exchange precision verification helper. |
| `verify_improvements.sh` | Focused verification wrapper for selected improvements. |

## Analysis Tools

Scripts under `scripts/analysis/` are intended for local post-run analysis. They are not launchers.

Examples:

- `scripts/analysis/analyze_recent_runs.py`
- `scripts/analysis/compare_runs.py`
- `scripts/analysis/fee_impact_report.py`
- `scripts/analysis/check_real_fees.py`
- `scripts/analysis/nanofenix_assessment.py`

Root-level `analyze_*.py` scripts are mostly older one-off analysis tools. Keep them for reproducibility, but prefer `scripts/analysis/` for new analysis utilities.

## Historical Launchers

Many `launch_fenix_*`, `launch_nanofenix_*`, `run_v21_*`, `run_team_*`, and benchmark launcher scripts preserve exact historical experiments.

Treat these as reproducibility artifacts unless the current release notes explicitly recommend one. They may include old model teams, old environment assumptions, or run tags that are not appropriate for a fresh v2.5 user.

`run_nanofenix_companion_signal.sh` is currently a legacy NanoFenix v2 wrapper. For the v2.5 public path, prefer `python run_nanofenixv3.py --companion --adaptive-fusion` until that wrapper is intentionally updated.

Examples of historical launcher families:

- `launch_fenix_r*.sh`
- `launch_fenix_v25_*.sh`
- `launch_fenix_v26_*.sh`
- `launch_fenix_v27_*.sh`
- `launch_fenix_v28_*.sh`
- `launch_nanofenix_r*.sh`
- `run_v21_*.sh`
- `run_team_*.sh`

## Fix and Patch Utilities

Scripts under `scripts/fixes/` are local repair/debug helpers. They should not be presented as public entry points.

Before publishing v2.5, review this folder and either:

- archive old one-off patch scripts;
- document why a repair helper is still useful;
- or exclude local-only fix artifacts from the release package.

## Generated Cache and Local Environments

Do not include these as public release content:

- `scripts/__pycache__/`
- `scripts/fenix_env/`
- `.DS_Store`
- local logs, run outputs, or private account artifacts

## Adding New Scripts

For new v2.5+ scripts:

1. Put reusable analysis scripts in `scripts/analysis/`.
2. Put temporary repair scripts in `scripts/fixes/` and remove or archive them after use.
3. Keep new public launchers small and document the expected mode: `paper`, `testnet`, or `live`.
4. Avoid hardcoded local paths, API key indexes, balances, or private run tags in scripts intended for public use.
5. Add the script to this index when it becomes part of the supported release surface.
