import React, { useEffect, useState } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  CircleSlash,
  TrendingUp,
  TrendingDown,
  MinusCircle,
  Activity,
  Cpu,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { useSystemStore } from '../../stores/systemStore';

/**
 * Live feed of the decision → filters → execution pipeline.
 *
 * Surfaces the events that previously were invisible outside the logs:
 * final decisions, entry-filter blocks (MTF veto, directional score,
 * min confidence…), risk/circuit-breaker blocks, NanoFenix vetoes and
 * actual executions.
 */

type FlowKind =
  | 'decision'
  | 'filter_blocked'
  | 'filter_adjusted'
  | 'risk_blocked'
  | 'nanofenix'
  | 'position'
  | 'trade';

interface FlowEvent {
  id: string;
  kind: FlowKind;
  title: string;
  detail?: string;
  tone: 'buy' | 'sell' | 'hold' | 'blocked' | 'ok' | 'info';
  timestamp: string;
}

const MAX_EVENTS = 60;

function toneBadge(tone: FlowEvent['tone']): 'success' | 'error' | 'warning' | 'default' | 'info' {
  switch (tone) {
    case 'buy':
      return 'success';
    case 'sell':
      return 'error';
    case 'blocked':
      return 'warning';
    case 'ok':
      return 'success';
    case 'info':
      return 'info';
    default:
      return 'default';
  }
}

function kindIcon(kind: FlowKind, tone: FlowEvent['tone']) {
  const cls = 'w-4 h-4 shrink-0';
  switch (kind) {
    case 'decision':
      if (tone === 'buy') return <TrendingUp className={`${cls} text-emerald-500`} />;
      if (tone === 'sell') return <TrendingDown className={`${cls} text-red-500`} />;
      return <MinusCircle className={`${cls} text-gray-400`} />;
    case 'filter_blocked':
      return <CircleSlash className={`${cls} text-amber-500`} />;
    case 'filter_adjusted':
      return <ShieldCheck className={`${cls} text-blue-500`} />;
    case 'risk_blocked':
      return <ShieldAlert className={`${cls} text-red-500`} />;
    case 'nanofenix':
      return <Cpu className={`${cls} text-violet-500`} />;
    case 'trade':
      return <Activity className={`${cls} text-emerald-600`} />;
    default:
      return <Activity className={`${cls} text-gray-400`} />;
  }
}

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return ts;
  }
}

export function ExecutionFlowFeed() {
  const { socket } = useSystemStore();
  const [events, setEvents] = useState<FlowEvent[]>([]);

  useEffect(() => {
    if (!socket) return;

    const push = (event: Omit<FlowEvent, 'id'>) => {
      setEvents((prev) =>
        [{ ...event, id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }, ...prev].slice(
          0,
          MAX_EVENTS,
        ),
      );
    };

    const now = () => new Date().toISOString();

    const onSignal = (d: Record<string, unknown>) => {
      const decision = String(d.decision || 'HOLD').toUpperCase();
      push({
        kind: 'decision',
        title: `Decision: ${decision} (${d.confidence || '—'})`,
        detail: typeof d.reasoning === 'string' ? d.reasoning.slice(0, 180) : undefined,
        tone: decision === 'BUY' ? 'buy' : decision === 'SELL' ? 'sell' : 'hold',
        timestamp: String(d.timestamp || now()),
      });
    };

    const onFilterBlocked = (d: Record<string, unknown>) => {
      push({
        kind: 'filter_blocked',
        title: `Blocked by ${d.filter || 'filter'}`,
        detail: typeof d.reason === 'string' ? d.reason : undefined,
        tone: 'blocked',
        timestamp: String(d.timestamp || now()),
      });
    };

    const onFilterAdjusted = (d: Record<string, unknown>) => {
      push({
        kind: 'filter_adjusted',
        title: `Adjusted by ${d.filter || 'filter'}`,
        detail: typeof d.reason === 'string' ? d.reason : undefined,
        tone: 'info',
        timestamp: String(d.timestamp || now()),
      });
    };

    const onRiskBlocked = (d: Record<string, unknown>) => {
      push({
        kind: 'risk_blocked',
        title: 'Blocked by risk manager',
        detail: typeof d.reason === 'string' ? d.reason : undefined,
        tone: 'blocked',
        timestamp: String(d.timestamp || now()),
      });
    };

    const onNanoPolicy = (d: Record<string, unknown>) => {
      const allow = Boolean(d.allow_execute);
      push({
        kind: 'nanofenix',
        title: `NanoFenix: ${allow ? 'execute OK' : 'veto'}`,
        detail: [
          d.signal ? `signal=${d.signal}` : null,
          d.edge_net_bps !== undefined ? `edge=${d.edge_net_bps}bps` : null,
          d.reason ? String(d.reason) : null,
        ]
          .filter(Boolean)
          .join(' · '),
        tone: allow ? 'ok' : 'blocked',
        timestamp: now(),
      });
    };

    const onPosition = (d: Record<string, unknown>) => {
      push({
        kind: 'position',
        title: String(d.kind || 'position:update').replace('position:', 'Position '),
        detail: [d.side, d.quantity, d.entry_price ? `@ ${d.entry_price}` : null]
          .filter(Boolean)
          .join(' '),
        tone: 'info',
        timestamp: String(d.timestamp || now()),
      });
    };

    const onTrade = (d: Record<string, unknown>) => {
      const side = String(d.side || d.action || '').toUpperCase();
      push({
        kind: 'trade',
        title: `${d.simulated ? 'Simulated trade' : 'Trade executed'}: ${side || '—'}`,
        detail: [d.quantity, d.price ? `@ ${d.price}` : null].filter(Boolean).join(' '),
        tone: side === 'SELL' ? 'sell' : 'buy',
        timestamp: String(d.timestamp || now()),
      });
    };

    socket.on('trade:signal', onSignal);
    socket.on('filter:blocked', onFilterBlocked);
    socket.on('filter:adjusted', onFilterAdjusted);
    socket.on('risk:blocked', onRiskBlocked);
    socket.on('nanofenix:policy', onNanoPolicy);
    socket.on('position:update', onPosition);
    socket.on('trade:executed', onTrade);

    return () => {
      socket.off('trade:signal', onSignal);
      socket.off('filter:blocked', onFilterBlocked);
      socket.off('filter:adjusted', onFilterAdjusted);
      socket.off('risk:blocked', onRiskBlocked);
      socket.off('nanofenix:policy', onNanoPolicy);
      socket.off('position:update', onPosition);
      socket.off('trade:executed', onTrade);
    };
  }, [socket]);

  return (
    <Card data-testid="execution-flow-feed">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-violet-500" />
          Execution Flow
          <span className="text-xs font-normal text-gray-400">decision → filters → orders</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="text-sm text-gray-500 py-6 text-center" data-testid="execution-flow-empty">
            Waiting for engine events… Decisions, filter blocks (MTF veto, directional score,
            min-confidence) and executions will appear here in real time.
          </p>
        ) : (
          <ul className="space-y-2 max-h-96 overflow-y-auto pr-1" data-testid="execution-flow-list">
            {events.map((e) => (
              <li
                key={e.id}
                className="flex items-start gap-3 rounded-lg border border-gray-100 bg-gray-50/50 px-3 py-2"
              >
                <div className="mt-0.5">{kindIcon(e.kind, e.tone)}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{e.title}</span>
                    <Badge variant={toneBadge(e.tone)} className="text-[10px] shrink-0">
                      {e.kind.replace('_', ' ')}
                    </Badge>
                  </div>
                  {e.detail && <p className="text-xs text-gray-500 mt-0.5 break-words">{e.detail}</p>}
                </div>
                <span className="text-[11px] text-gray-400 shrink-0 mt-0.5">{fmtTime(e.timestamp)}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
