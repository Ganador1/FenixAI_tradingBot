import { create } from 'zustand';
import { io, Socket } from 'socket.io-client';

export interface SystemMetrics {
  cpu: number;
  memory: number;
  disk: number;
  network: number;
  process: number;
  timestamp: string;
  raw?: Record<string, unknown>;
}

export interface SystemAlert {
  id: string;
  type: 'warning' | 'error' | 'info';
  title: string;
  message: string;
  component: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  created_at: string;
  resolved: boolean;
}

export interface ConnectionStatus {
  service: string;
  status: 'connected' | 'disconnected' | 'error' | 'connecting';
  last_ping: number;
  reconnect_attempts: number;
  error_count: number;
}

export interface SystemState {
  metrics: SystemMetrics | null;
  alerts: SystemAlert[];
  connections: ConnectionStatus[];
  engineConfig: {
    symbol: string;
    timeframe: string;
    paper_trading: boolean;
    allow_live_trading: boolean;
    enable_visual_agent: boolean;
    enable_sentiment_agent: boolean;
  } | null;
  isLoading: boolean;
  error: string | null;
  socket: Socket | null;
  initializeSocket: () => void;
  disconnectSocket: () => void;
  fetchSystemStatus: () => Promise<void>;
  fetchAlerts: () => Promise<void>;
  fetchConnections: () => Promise<void>;
  fetchEngineConfig: () => Promise<void>;
  updateEngineConfig: (changes: Partial<SystemState['engineConfig']>) => Promise<void>;
  clearError: () => void;
}

export const useSystemStore = create<SystemState>()((set, get) => ({
  metrics: null,
  alerts: [],
  connections: [],
  engineConfig: null,
  isLoading: false,
  error: null,
  socket: null,

  initializeSocket: () => {
    const { socket } = get();
    if (socket) return;

    const newSocket = io(window.location.origin, {
      path: '/socket.io',
      transports: ['websocket', 'polling']
    });

    newSocket.on('connect', () => {
      console.log('Connected to server');
      newSocket.emit('subscribe:system');
    });

    newSocket.on('system:metrics', (data: { summary: SystemMetrics } | SystemMetrics) => {
      const summary = (data as { summary: SystemMetrics }).summary || (data as SystemMetrics);
      set({ metrics: summary });
    });

    newSocket.on('system:alert', (alert: SystemAlert) => {
      set(state => ({ 
        alerts: [alert, ...state.alerts.slice(0, 49)] // Keep last 50 alerts
      }));
    });

    newSocket.on('system:connection', (payload: ConnectionStatus[] | { connections: ConnectionStatus[] }) => {
      const connections = (payload as { connections: ConnectionStatus[] }).connections || (payload as ConnectionStatus[]) || [];
      set({ connections });
    });

    set({ socket: newSocket });
  },

  disconnectSocket: () => {
    const { socket } = get();
    if (socket) {
      socket.disconnect();
      set({ socket: null });
    }
  },

  fetchSystemStatus: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/system/status');
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch system status');
      }

      set({ 
        metrics: { ...data.metrics, raw: data.raw_metrics },
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch system status',
        isLoading: false,
      });
    }
  },

  fetchEngineConfig: async () => {
    set({ isLoading: true, error: null });

    try {
      const response = await fetch('/api/engine/config');
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch engine config');
      }
      set({ engineConfig: data.config, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch engine config',
        isLoading: false,
      });
    }
  },

  updateEngineConfig: async (changes) => {
    set({ isLoading: true, error: null });

    try {
      const response = await fetch('/api/engine/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to update engine config');
      }
      set({ engineConfig: data.config, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to update engine config',
        isLoading: false,
      });
    }
  },

  fetchAlerts: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/system/alerts');
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch alerts');
      }

      set({ 
        alerts: data.data || data.alerts || [],
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch alerts',
        isLoading: false,
      });
    }
  },

  fetchConnections: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/system/connections');
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch connections');
      }

      set({ 
        connections: data.data || data.connections || [],
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch connections',
        isLoading: false,
      });
    }
  },

  clearError: () => {
    set({ error: null });
  },
}));