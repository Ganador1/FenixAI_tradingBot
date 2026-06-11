import React, { useCallback, useEffect, useState } from 'react';
import {
  Cpu,
  Play,
  Square,
  RefreshCw,
  Gauge,
  Radio,
  Eye,
  Zap,
  Layers,
  ShieldCheck,
  AlertTriangle,
  Clock,
} from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';

interface NanoSignal {
  symbol: string;
  timestamp_utc?: string | null;
  signal?: string | null;
  action?: string | null;
  confidence?: number | null;
  pred_bps?: number | null;
  direction_accuracy?: number | null;
  regime?: string | null;
  trend?: string | null;
  allow_execute?: boolean | null;
  allow_add_to_position?: boolean | null;
  size_multiplier_hint?: number | null;
  calibration_health?: number | null;
  uncertainty_bps?: number | null;
  actionable_edge_bps?: number | null;
  has_position?: boolean | null;
  age_seconds?: number | null;
}

interface NanoStatus {
  symbol: string;
  running: boolean;
  pid?: number | null;
  signal_path?: string | null;
  signal_age_seconds?: number | null;
}

interface ReleaseInfo {
  version: string;
  status: string;
  recommended_symbol: string;
  recommended_timeframe: string;
  recommended_mode: string;
  recommended_team: Record<string, string>;
  subsystems: Record<string, string>;
}

interface MiniRegime {
  source: string;
  regime: Record<string, unknown>;
  age_seconds: number;
}

const SYMBOLS = ['SOLUSDT', 'BTCUSDT', 'ETHUSDT'];

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function fmtAge(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function fmtPct(v?: number | null): string {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`;
}

function fmtNum(v?: number | null, digits = 2): string {
  return v === null || v === undefined ? '—' : v.toFixed(digits);
}

function signalBadgeVariant(signal?: string | null): 'success' | 'error' | 'default' {
  const s = (signal || '').toUpperCase();
  if (s.includes('BUY') || s.includes('LONG')) return 'success';
  if (s.includes('SELL') || s.includes('SHORT')) return 'error';
  return 'default';
}

export function Companions() {
  const [symbol, setSymbol] = useState<string>('SOLUSDT');
  const [status, setStatus] = useState<NanoStatus | null>(null);
  const [signal, setSignal] = useState<NanoSignal | null>(null);
  const [signalError, setSignalError] = useState<string | null>(null);
  const [release, setRelease] = useState<ReleaseInfo | null>(null);
  const [miniRegime, setMiniRegime] = useState<MiniRegime | null>(null);
  const [miniError, setMiniError] = useState<string | null>(null);
  const [observerOnly, setObserverOnly] = useState(true);
  const [adaptiveFusion, setAdaptiveFusion] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [statusRes, signalRes, miniRes] = await Promise.all([
        fetch(`/api/nanofenix/status?symbol=${symbol}`),
        fetch(`/api/nanofenix/signal?symbol=${symbol}`),
        fetch('/api/minifenix/regime'),
      ]);

      if (statusRes.ok) setStatus(await statusRes.json());

      if (signalRes.ok) {
        setSignal(await signalRes.json());
        setSignalError(null);
      } else {
        setSignal(null);
        const body = await signalRes.json().catch(() => ({}));
        setSignalError(body.detail || 'No signal available yet.');
      }

      if (miniRes.ok) {
        setMiniRegime(await miniRes.json());
        setMiniError(null);
      } else {
        setMiniRegime(null);
        const body = await miniRes.json().catch(() => ({}));
        setMiniError(body.detail || 'No MiniFenix regime available.');
      }
    } catch {
      // network hiccup; keep last state
    }
  }, [symbol]);

  useEffect(() => {
    refresh();
    fetch('/api/v25/release-info')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => data && setRelease(data))
      .catch(() => undefined);

    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const startNano = async () => {
    setBusy(true);
    try {
      const res = await fetch('/api/nanofenix/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ symbol, observer_only: observerOnly, adaptive_fusion: adaptiveFusion }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast.success(`NanoFenix companion started for ${symbol}`);
      await refresh();
    } catch (e) {
      toast.error(`Could not start NanoFenix: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const stopNano = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/nanofenix/stop?symbol=${symbol}`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast.success(`NanoFenix companion stopped for ${symbol}`);
      await refresh();
    } catch (e) {
      toast.error(`Could not stop NanoFenix: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="companions-page">
      {/* Page header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-600 flex items-center justify-center shadow-md">
              <Cpu className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Companions</h1>
              <p className="text-sm text-gray-500">
                NanoFenix v3.5 microstructure companion &amp; MiniFenix regime brain
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {release && (
            <Badge variant="purple" className="text-sm px-3 py-1" data-testid="release-badge">
              Fenix v{release.version} · {release.status}
            </Badge>
          )}
          <Button variant="outline" size="sm" icon={<RefreshCw className="w-4 h-4" />} onClick={refresh}>
            Refresh
          </Button>
        </div>
      </div>

      {/* NanoFenix control */}
      <Card className="overflow-hidden" data-testid="nano-control-card">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-6">
          <div className="flex items-center gap-3">
            <Radio className={`w-5 h-5 ${status?.running ? 'text-emerald-500 animate-pulse' : 'text-gray-400'}`} />
            <div>
              <h2 className="text-lg font-semibold text-gray-900">NanoFenix Companion</h2>
              <p className="text-xs text-gray-500">
                Zero-LLM LightGBM signal · {status?.running ? `running (pid ${status.pid})` : 'stopped'}
              </p>
            </div>
            <Badge variant={status?.running ? 'success' : 'default'} data-testid="nano-status-badge">
              {status?.running ? 'RUNNING' : 'STOPPED'}
            </Badge>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              data-testid="nano-symbol-select"
              className="px-3 py-2 rounded-lg border border-gray-300 bg-white text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-violet-500"
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>

            <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={observerOnly}
                onChange={(e) => setObserverOnly(e.target.checked)}
                data-testid="nano-observer-toggle"
                className="rounded border-gray-300 text-violet-600 focus:ring-violet-500"
              />
              <Eye className="w-4 h-4 text-gray-400" /> Observer only
            </label>

            <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={adaptiveFusion}
                onChange={(e) => setAdaptiveFusion(e.target.checked)}
                data-testid="nano-fusion-toggle"
                className="rounded border-gray-300 text-violet-600 focus:ring-violet-500"
              />
              <Zap className="w-4 h-4 text-gray-400" /> Adaptive fusion
            </label>

            {status?.running ? (
              <Button variant="danger" size="sm" loading={busy} icon={<Square className="w-4 h-4" />} onClick={stopNano} data-testid="nano-stop-btn">
                Stop
              </Button>
            ) : (
              <Button variant="success" size="sm" loading={busy} icon={<Play className="w-4 h-4" />} onClick={startNano} data-testid="nano-start-btn">
                Start
              </Button>
            )}
          </div>
        </div>

        {/* Signal panel */}
        {signal ? (
          <div data-testid="nano-signal-panel">
            <div className="flex items-center gap-3 mb-4">
              <Badge variant={signalBadgeVariant(signal.signal)} className="text-base px-4 py-1" data-testid="nano-signal-value">
                {signal.signal || signal.action || 'N/A'}
              </Badge>
              <span className="text-sm text-gray-500 flex items-center gap-1">
                <Clock className="w-3.5 h-3.5" /> {fmtAge(signal.age_seconds)} ago
              </span>
              {signal.allow_execute !== null && signal.allow_execute !== undefined && (
                <Badge variant={signal.allow_execute ? 'success' : 'warning'}>
                  {signal.allow_execute ? 'EXECUTE OK' : 'VETO'}
                </Badge>
              )}
              {signal.has_position && <Badge variant="info">IN POSITION</Badge>}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'Confidence', value: fmtPct(signal.confidence) },
                { label: 'Direction accuracy', value: fmtPct(signal.direction_accuracy) },
                { label: 'Predicted bps', value: fmtNum(signal.pred_bps, 1) },
                { label: 'Uncertainty bps', value: fmtNum(signal.uncertainty_bps, 1) },
                { label: 'Actionable edge bps', value: fmtNum(signal.actionable_edge_bps, 1) },
                { label: 'Calibration health', value: fmtPct(signal.calibration_health) },
                { label: 'Regime', value: signal.regime || '—' },
                { label: 'Trend', value: signal.trend || '—' },
              ].map((m) => (
                <div key={m.label} className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
                  <p className="text-xs uppercase tracking-wide text-gray-400">{m.label}</p>
                  <p className="text-lg font-semibold text-gray-900 mt-0.5">{m.value}</p>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-800 text-sm" data-testid="nano-signal-empty">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {signalError || 'No signal yet. Start the companion to begin publishing.'}
          </div>
        )}
      </Card>

      {/* MiniFenix + release info */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card data-testid="mini-regime-card">
          <div className="flex items-center gap-3 mb-4">
            <Gauge className="w-5 h-5 text-blue-500" />
            <h2 className="text-lg font-semibold text-gray-900">MiniFenix Regime</h2>
            <Badge variant="outline">read-only</Badge>
          </div>
          {miniRegime ? (
            <div className="space-y-3" data-testid="mini-regime-data">
              <p className="text-xs text-gray-400">
                {miniRegime.source} · {fmtAge(miniRegime.age_seconds)} old
              </p>
              <pre className="rounded-xl bg-gray-900 text-emerald-300 text-xs p-4 overflow-x-auto">
                {JSON.stringify(miniRegime.regime, null, 2)}
              </pre>
            </div>
          ) : (
            <p className="text-sm text-gray-500" data-testid="mini-regime-empty">
              {miniError || 'MiniFenix is a research prototype; no regime file published yet.'}
            </p>
          )}
        </Card>

        <Card data-testid="release-info-card">
          <div className="flex items-center gap-3 mb-4">
            <ShieldCheck className="w-5 h-5 text-violet-500" />
            <h2 className="text-lg font-semibold text-gray-900">v2.5 Recommended Setup</h2>
          </div>
          {release ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <Badge variant="info">{release.recommended_symbol}</Badge>
                <Badge variant="info">{release.recommended_timeframe}</Badge>
                <Badge variant="success">{release.recommended_mode}</Badge>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-gray-400 mb-2">Agent team</p>
                <ul className="space-y-1.5">
                  {Object.entries(release.recommended_team).map(([agent, model]) => (
                    <li key={agent} className="flex items-center justify-between text-sm">
                      <span className="capitalize text-gray-600">{agent.replace('_', ' ')}</span>
                      <code className="text-xs bg-gray-100 rounded px-2 py-0.5 text-gray-700">{model}</code>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-gray-400 mb-2 flex items-center gap-1">
                  <Layers className="w-3 h-3" /> Subsystems
                </p>
                <ul className="space-y-1">
                  {Object.entries(release.subsystems).map(([name, desc]) => (
                    <li key={name} className="text-xs text-gray-500">
                      <span className="font-medium text-gray-700">{name}</span>: {desc}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading release info…</p>
          )}
        </Card>
      </div>
    </div>
  );
}
