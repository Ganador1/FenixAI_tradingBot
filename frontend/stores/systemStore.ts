import { create } from 'zustand';
import { Socket } from 'socket.io-client';
import { getSocket, releaseSocket } from '../lib/socket';

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

// Handlers registrados por este store sobre el socket compartido; se guardan
// a nivel de módulo para poder retirarlos exactamente al desconectar.
let socketHandlers: {
  onConnect: () => void;
  onMetrics: (data: { summary: SystemMetrics } | SystemMetrics) => void;
  onAlert: (alert: SystemAlert) => void;
  onConnection: (payload: ConnectionStatus[] | { connections: ConnectionStatus[] }) => void;
} | null = null;

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

    // Socket compartido (ver lib/socket.ts). Guardamos referencias con nombre
    // a cada handler para poder retirar SOLO los de este store al desconectar
    // (un `off('connect')` a secas borraría también los de agentStore).
    const newSocket = getSocket();

    const onConnect = () => {
      console.log('Connected to server');
      newSocket.emit('subscribe:system');
    };
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
    newSocket.on('system:metrics', onMetrics);
    newSocket.on('system:alert', onAlert);
    newSocket.on('system:connection', onConnection);

    socketHandlers = { onConnect, onMetrics, onAlert, onConnection };
    set({ socket: newSocket });
  },

  disconnectSocket: () => {
    const { socket } = get();
    if (socket && socketHandlers) {
      // Solo retira los handlers de este store; la conexión se cierra cuando
      // el último consumidor la libera (reference counting en lib/socket.ts).
      socket.off('connect', socketHandlers.onConnect);
      socket.off('system:metrics', socketHandlers.onMetrics);
      socket.off('system:alert', socketHandlers.onAlert);
      socket.off('system:connection', socketHandlers.onConnection);
      socketHandlers = null;
      releaseSocket();
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