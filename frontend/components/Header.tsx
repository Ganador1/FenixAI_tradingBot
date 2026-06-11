import React, { useEffect, useState } from 'react';
import { Bell, User, LogOut, Sparkles, Activity, Play, Square } from 'lucide-react';
import { toast } from 'sonner';
import { useAuthStore } from '@/stores/authStore';
import { Button } from './ui/Button';

interface EngineState {
  running: boolean;
  symbol?: string;
  timeframe?: string;
  paper?: boolean;
}

export function Header() {
  const { user, logout } = useAuthStore();
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [engineBusy, setEngineBusy] = useState(false);

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) {
        const data = await res.json();
        const eng = data.engine || data;
        setEngine({
          running: Boolean(eng.running),
          symbol: eng.symbol,
          timeframe: eng.timeframe,
          paper: eng.paper_trading,
        });
      }
    } catch {
      setEngine(null);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 15000);
    return () => clearInterval(interval);
  }, []);

  const toggleEngine = async () => {
    const action = engine?.running ? 'stop' : 'start';
    setEngineBusy(true);
    try {
      const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
      const res = await fetch(`/api/engine/${action}`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast.success(`Engine ${action} requested`);
      setTimeout(fetchStatus, 1500);
    } catch (e) {
      toast.error(`Engine ${action} failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setEngineBusy(false);
    }
  };

  const online = engine?.running ?? false;

  return (
    <header className="sticky top-0 z-20 border-b border-gray-200 bg-white/80 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <div className="h-12 w-12 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-md">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-gray-400">Command Center · v2.5</p>
            <h1 className="text-xl font-semibold text-gray-900 leading-tight">Multi-Agent Trading</h1>
            <div className="hidden md:flex items-center space-x-2 text-xs text-gray-500" data-testid="header-engine-status">
              <Activity className={`w-3 h-3 ${online ? 'text-emerald-500' : 'text-gray-400'}`} />
              <span>
                {online
                  ? `Engine running · ${engine?.symbol || ''}@${engine?.timeframe || ''}${engine?.paper ? ' · paper' : ''}`
                  : 'Engine stopped'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-4">
          <div
            className={`hidden md:flex items-center space-x-2 px-3 py-1 rounded-full text-xs border ${
              online
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-gray-50 border-gray-200 text-gray-500'
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${online ? 'bg-emerald-500' : 'bg-gray-400'}`} />
            <span>{online ? 'Live stream' : 'Offline'}</span>
          </div>

          <Button
            variant={online ? 'danger' : 'success'}
            size="sm"
            loading={engineBusy}
            onClick={toggleEngine}
            data-testid="engine-toggle-btn"
            icon={online ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          >
            {online ? 'Stop engine' : 'Start engine'}
          </Button>

          <Button
            variant="ghost"
            size="sm"
            className="relative text-gray-600 hover:text-gray-900"
          >
            <Bell className="w-5 h-5" />
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-rose-500 rounded-full"></span>
          </Button>

          <div className="flex items-center space-x-3">
            <div className="text-right hidden md:block">
              <div className="text-sm font-semibold text-gray-900">{user?.name}</div>
              <div className="text-xs text-gray-500 capitalize">{user?.role}</div>
            </div>
            <div className="w-10 h-10 bg-gray-100 border border-gray-200 rounded-2xl flex items-center justify-center">
              <User className="w-4 h-4 text-gray-600" />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={logout}
              className="text-gray-600 hover:text-gray-900"
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
    </header>
  );
}
