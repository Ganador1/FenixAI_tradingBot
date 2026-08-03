import { create } from 'zustand';
import { Socket } from 'socket.io-client';
import { getSocket, releaseSocket } from '../lib/socket';
import { useAuthStore } from './authStore';

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
  riskFlags: {
    macro_riskoff_enabled: boolean;
  } | null;
  isLoading: boolean;
  error: string | null;
  socket: Socket | null;
  socketConnected: boolean;
  initializeSocket: () => void;
  disconnectSocket: () => void;
  fetchSystemStatus: () => Promise<void>;
  fetchAlerts: () => Promise<void>;
  fetchConnections: () => Promise<void>;
  fetchEngineConfig: () => Promise<void>;
  updateEngineConfig: (changes: Partial<SystemState['engineConfig']>) => Promise<void>;
  fetchRiskFlags: () => Promise<void>;
  updateRiskFlags: (changes: Partial<SystemState['riskFlags']>) => Promise<void>;
  clearError: () => void;
}

// Handlers registrados por este store sobre el socket compartido; se guardan
// a nivel de módulo para poder retirarlos exactamente al desconectar.
let socketHandlers: {
  onConnect: () => void;
  onDisconnect: () => void;
  onConnectError: () => void;
  onMetrics: (data: { summary: SystemMetrics } | SystemMetrics) => void;
  onAlert: (alert: SystemAlert) => void;
  onConnection: (payload: ConnectionStatus[] | { connections: ConnectionStatus[] }) => void;
} | null = null;

const authenticatedHeaders = (): HeadersInit => {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const useSystemStore = create<SystemState>()((set, get) => ({
  metrics: null,
  alerts: [],
  connections: [],
  engineConfig: null,
  riskFlags: null,
  isLoading: false,
  error: null,
  socket: null,
  socketConnected: false,

  initializeSocket: () => {
    const { socket } = get();
    if (socket) return;

    // Socket compartido (ver lib/socket.ts). Guardamos referencias con nombre
    // a cada handler para poder retirar SOLO los de este store al desconectar
    // (un `off('connect')` a secas borraría también los de agentStore).
    const newSocket = getSocket();

    const onConnect = () => {
      console.log('Connected to server');
      set({ socketConnected: true });
      newSocket.emit('subscribe:system');
    };
    const onDisconnect = () => set({ socketConnected: false });
    const onConnectError = () => set({ socketConnected: false });
    const onMetrics = (data: { summary: SystemMetrics } | SystemMetrics) => {
      const summary = (data as { summary: SystemMetrics }).summary || (data as SystemMetrics);
      set({ metrics: summary });
    };
    const onAlert = (alert: SystemAlert) => {
      set(state => ({
        alerts: [alert, ...state.alerts.slice(0, 49)] // Keep last 50 alerts
      }));
    };
    const onConnection = (payload: ConnectionStatus[] | { connections: ConnectionStatus[] }) => {
      const connections = (payload as { connections: ConnectionStatus[] }).connections || (payload as ConnectionStatus[]) || [];
      set({ connections });
    };

    // Si ya está conectado (otro consumidor lo abrió primero) suscribimos ya.
    if (newSocket.connected) onConnect();
    newSocket.on('connect', onConnect);
    newSocket.on('disconnect', onDisconnect);
    newSocket.on('connect_error', onConnectError);
    newSocket.on('system:metrics', onMetrics);
    newSocket.on('system:alert', onAlert);
    newSocket.on('system:connection', onConnection);

    socketHandlers = {
      onConnect,
      onDisconnect,
      onConnectError,
      onMetrics,
      onAlert,
      onConnection,
    };
    set({ socket: newSocket, socketConnected: newSocket.connected });
  },

  disconnectSocket: () => {
    const { socket } = get();
    if (socket && socketHandlers) {
      // Solo retira los handlers de este store; la conexión se cierra cuando
      // el último consumidor la libera (reference counting en lib/socket.ts).
      socket.off('connect', socketHandlers.onConnect);
      socket.off('disconnect', socketHandlers.onDisconnect);
      socket.off('connect_error', socketHandlers.onConnectError);
      socket.off('system:metrics', socketHandlers.onMetrics);
      socket.off('system:alert', socketHandlers.onAlert);
      socket.off('system:connection', socketHandlers.onConnection);
      socketHandlers = null;
      releaseSocket();
      set({ socket: null, socketConnected: false });
    }
  },

  fetchSystemStatus: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/system/status', { headers: authenticatedHeaders() });
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
      const response = await fetch('/api/engine/config', { headers: authenticatedHeaders() });
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
      const token = useAuthStore.getState().token;
      const response = await fetch('/api/engine/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
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

  fetchRiskFlags: async () => {
    set({ isLoading: true, error: null });

    try {
      const response = await fetch('/api/engine/risk-flags', { headers: authenticatedHeaders() });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch risk flags');
      }
      set({ riskFlags: data, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch risk flags',
        isLoading: false,
      });
    }
  },

  updateRiskFlags: async (changes) => {
    set({ isLoading: true, error: null });

    try {
      const token = useAuthStore.getState().token;
      const response = await fetch('/api/engine/risk-flags', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ...get().riskFlags, ...changes }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to update risk flags');
      }
      set({ riskFlags: data, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to update risk flags',
        isLoading: false,
      });
    }
  },

  fetchAlerts: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/system/alerts', { headers: authenticatedHeaders() });
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
      const response = await fetch('/api/system/connections', { headers: authenticatedHeaders() });
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
