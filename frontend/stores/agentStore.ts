import { create } from 'zustand';
import { io, Socket } from 'socket.io-client';

export interface Agent {
  id: string;
  name: string;
  type: 'sentiment' | 'technical' | 'visual' | 'qabba' | 'decision' | 'risk';
  status: 'active' | 'inactive' | 'error';
  last_run: string;
  config: Record<string, unknown>;
  performance: {
    total_signals: number;
    successful_signals: number;
    accuracy: number;
    average_confidence: number;
  };
}

export interface ReasoningEntry {
  id: string;
  agent_id: string;
  agent_name: string;
  timestamp: string;
  input_data: unknown;
  reasoning: string;
  decision: string;
  confidence: number;
  outcome?: {
    actual_price: number;
    predicted_price: number;
    accuracy: number;
    judge_feedback: string;
  };
}

export interface Scorecard {
  id: string;
  agent_id: string;
  agent_name: string;
  timestamp: string;
  total_signals: number;
  successful_signals: number;
  failed_signals: number;
  accuracy: number;
  average_confidence: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown: number;
  sharpe_ratio: number;
}

export interface AgentState {
  agents: Agent[];
  reasoningLogs: ReasoningEntry[];
  scorecards: Scorecard[];
  isLoading: boolean;
  error: string | null;
  socket: Socket | null;
  initializeSocket: () => void;
  disconnectSocket: () => void;
  fetchAgents: () => Promise<void>;
  fetchReasoningLogs: (options?: { agentId?: string; timeframe?: string }) => Promise<void>;
  fetchScorecards: () => Promise<void>;
  clearError: () => void;
}

export const useAgentStore = create<AgentState>()((set, get) => ({
  agents: [],
  reasoningLogs: [],
  scorecards: [],
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
      console.log('Connected to agent service');
      newSocket.emit('subscribe:agents');
    });

    newSocket.on('agent:reasoning', (data: ReasoningEntry) => {
      set(state => ({ 
        reasoningLogs: [data, ...state.reasoningLogs.slice(0, 99)] // Keep last 100 entries
      }));
    });

    newSocket.on('agentOutput', (data: ReasoningEntry) => {
      set(state => ({
        reasoningLogs: [data, ...state.reasoningLogs.slice(0, 99)]
      }));
    });

    newSocket.on('agent:scorecard', (data: Scorecard) => {
      set(state => ({ 
        scorecards: [data, ...state.scorecards.slice(0, 49)] // Keep last 50 scorecards
      }));
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

  fetchAgents: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/agents');
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch agents');
      }

      set({ 
        agents: data.data || data.agents || [],
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch agents',
        isLoading: false,
      });
    }
  },

  fetchReasoningLogs: async (options?: { agentId?: string; timeframe?: string }) => {
    set({ isLoading: true, error: null });
    
    try {
      const { agentId, timeframe } = options || {};
      const params = new URLSearchParams();
      if (agentId) params.append('agent_id', agentId);
      if (timeframe) params.append('timeframe', timeframe);
      const query = params.toString();
      const url = query ? `/api/reasoning-bank/logs?${query}` : '/api/reasoning-bank/logs';
      const response = await fetch(url);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch reasoning logs');
      }

      set({ 
        reasoningLogs: data.data || data.logs || data.outputs || [],
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch reasoning logs',
        isLoading: false,
      });
    }
  },

  fetchScorecards: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const response = await fetch('/api/agents/scorecards');
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch scorecards');
      }

      set({ 
        scorecards: data.data || data.scorecards || [],
        isLoading: false 
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch scorecards',
        isLoading: false,
      });
    }
  },

  clearError: () => {
    set({ error: null });
  },
}));