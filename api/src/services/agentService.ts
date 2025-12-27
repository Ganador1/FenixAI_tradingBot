import { EventEmitter } from 'events';
import { connectionManager } from './connectionManager';
import { systemMonitor } from './systemMonitor';
import winston from 'winston';
import { readFileSync, existsSync } from 'fs';
import { watch } from 'chokidar';
import { join } from 'path';

export interface AgentOutput {
  agentId: string;
  agentType: 'sentiment' | 'technical' | 'visual' | 'qabba' | 'decision' | 'risk';
  timestamp: string;
  marketSymbol: string;
  confidence: number;
  decision: 'BUY' | 'SELL' | 'HOLD';
  reasoning: {
    summary: string;
    details: Record<string, any>;
    factors: string[];
  };
  metadata: {
    processingTime: number;
    dataSource: string;
    version: string;
    backend?: string;
    latency?: number;
  };
}

export interface ReasoningEntry {
  id: string;
  agent: string;
  prompt_digest?: string;
  prompt?: string;
  reasoning: string;
  action: string;
  confidence: number;
  backend: string;
  latency_ms: number;
  metadata: Record<string, any>;
  timestamp: string;
  judge_score?: number;
  judge_confidence?: number;
  judge_tags?: string[];
  judge_notes?: string;
  outcome?: {
    success: boolean;
    reward: number;
    near_miss?: boolean;
    reward_signal?: number;
  };
}

export interface AgentScorecard {
  agentId: string;
  agentType: string;
  timeframe: 'hour' | 'day' | 'week' | 'month';
  timestamp: string;
  totalAnalyses: number;
  correctPredictions: number;
  accuracyRate: number;
  averageConfidence: number;
  performanceMetrics: {
    precision: number;
    recall: number;
    f1Score: number;
    sharpeRatio: number;
    maxDrawdown: number;
  };
  recentDecisions: AgentOutput[];
}

export class AgentService extends EventEmitter {
  private reasoningBankPath: string;
  private scorecardsPath: string;
  private tradeMemoryPath: string;
  private agents: Map<string, AgentOutput> = new Map();
  private reasoningLogs: Map<string, ReasoningEntry[]> = new Map();
  private scorecards: Map<string, AgentScorecard> = new Map();
  private fileWatchers: Map<string, any> = new Map();
  private logger: winston.Logger;
  private isInitialized: boolean = false;

  constructor() {
    super();
    
    // Set up paths based on existing system structure
    this.reasoningBankPath = join(process.cwd(), '..', 'logs', 'reasoning_bank');
    this.scorecardsPath = join(process.cwd(), '..', 'monitoring', 'scorecards');
    this.tradeMemoryPath = join(process.cwd(), '..', 'trade_memory');

    this.logger = winston.createLogger({
      level: 'info',
      format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json()
      ),
      transports: [
        new winston.transports.Console(),
        new winston.transports.File({ filename: 'logs/agent-service.log' })
      ]
    });
  }

  async initialize(): Promise<void> {
    if (this.isInitialized) {
      this.logger.warn('Agent service already initialized');
      return;
    }

    try {
      this.logger.info('Initializing agent service');
      
      // Ensure directories exist
      this.ensureDirectories();
      
      // Load existing data
      await this.loadExistingData();
      
      // Set up file watchers
      this.setupFileWatchers();
      
      // Initialize agent types
      this.initializeAgentTypes();
      
      this.isInitialized = true;
      this.logger.info('Agent service initialized successfully');
      this.emit('service:initialized');
    } catch (error) {
      this.logger.error('Failed to initialize agent service:', error);
      throw error;
    }
  }

  private ensureDirectories(): void {
    const fs = require('fs');
    const paths = [this.reasoningBankPath, this.scorecardsPath, this.tradeMemoryPath];
    
    paths.forEach(path => {
      if (!existsSync(path)) {
        fs.mkdirSync(path, { recursive: true });
        this.logger.info(`Created directory: ${path}`);
      }
    });
  }

  private async loadExistingData(): Promise<void> {
    const agentTypes = ['sentiment', 'technical', 'visual', 'qabba', 'decision', 'risk'];
    
    for (const agentType of agentTypes) {
      await this.loadReasoningLogs(agentType);
      await this.loadScorecards(agentType);
    }
    
    this.logger.info(`Loaded data for ${agentTypes.length} agent types`);
  }

  private async loadReasoningLogs(agentType: string): Promise<void> {
    const filePath = join(this.reasoningBankPath, `${agentType}.jsonl`);
    
    if (!existsSync(filePath)) {
      this.logger.info(`No reasoning logs found for ${agentType}`);
      return;
    }

    try {
      const content = readFileSync(filePath, 'utf8');
      const lines = content.split('\n').filter(line => line.trim());
      const entries: ReasoningEntry[] = [];

      for (const line of lines) {
        try {
          const entry = JSON.parse(line);
          entries.push(entry);
        } catch (error) {
          this.logger.warn(`Invalid JSON in ${agentType} reasoning log:`, line);
        }
      }

      this.reasoningLogs.set(agentType, entries);
      this.logger.info(`Loaded ${entries.length} reasoning entries for ${agentType}`);
    } catch (error) {
      this.logger.error(`Failed to load reasoning logs for ${agentType}:`, error);
    }
  }

  private async loadScorecards(agentType: string): Promise<void> {
    const filePath = join(this.scorecardsPath, `${agentType}.jsonl`);
    
    if (!existsSync(filePath)) {
      this.logger.info(`No scorecards found for ${agentType}`);
      return;
    }

    try {
      const content = readFileSync(filePath, 'utf8');
      const lines = content.split('\n').filter(line => line.trim());
      
      if (lines.length > 0) {
        const latestLine = lines[lines.length - 1];
        const scorecard = JSON.parse(latestLine);
        this.scorecards.set(agentType, this.transformScorecard(scorecard, agentType));
        this.logger.info(`Loaded scorecard for ${agentType}`);
      }
    } catch (error) {
      this.logger.error(`Failed to load scorecards for ${agentType}:`, error);
    }
  }

  private setupFileWatchers(): void {
    const agentTypes = ['sentiment', 'technical', 'visual', 'qabba', 'decision', 'risk'];

    // Watch reasoning bank files
    agentTypes.forEach(agentType => {
      const reasoningFile = join(this.reasoningBankPath, `${agentType}.jsonl`);
      const scorecardFile = join(this.scorecardsPath, `${agentType}.jsonl`);

      // Watch reasoning logs
      if (existsSync(reasoningFile)) {
        const watcher = watch(reasoningFile, {
          persistent: true,
          ignoreInitial: true
        });

        watcher.on('change', () => {
          this.logger.info(`Reasoning log changed for ${agentType}`);
          this.loadReasoningLogs(agentType);
          this.processNewReasoningEntries(agentType);
        });

        this.fileWatchers.set(`reasoning_${agentType}`, watcher);
      }

      // Watch scorecards
      if (existsSync(scorecardFile)) {
        const watcher = watch(scorecardFile, {
          persistent: true,
          ignoreInitial: true
        });

        watcher.on('change', () => {
          this.logger.info(`Scorecard changed for ${agentType}`);
          this.loadScorecards(agentType);
          this.emit('scorecard:updated', { agentType });
        });

        this.fileWatchers.set(`scorecard_${agentType}`, watcher);
      }
    });

    this.logger.info('File watchers set up for agent data');
  }

  private initializeAgentTypes(): void {
    const agentTypes = [
      { id: 'sentiment', name: 'Sentiment Agent', description: 'Market sentiment analysis from news and social media' },
      { id: 'technical', name: 'Technical Analyst', description: 'Technical analysis and pattern recognition' },
      { id: 'visual', name: 'Visual Analyst', description: 'Visual pattern detection and chart analysis' },
      { id: 'qabba', name: 'QABBA Analyst', description: 'Quantitative analysis and statistical modeling' },
      { id: 'decision', name: 'Decision Agent', description: 'Final decision synthesis from all agent inputs' },
      { id: 'risk', name: 'Risk Manager', description: 'Risk assessment and probability calculations' }
    ];

    agentTypes.forEach(agent => {
      this.agents.set(agent.id, {
        agentId: agent.id,
        agentType: agent.id as any,
        timestamp: new Date().toISOString(),
        marketSymbol: '',
        confidence: 0,
        decision: 'HOLD',
        reasoning: {
          summary: agent.description,
          details: {},
          factors: []
        },
        metadata: {
          processingTime: 0,
          dataSource: 'system',
          version: '1.0.0'
        }
      });
    });

    this.logger.info('Agent types initialized');
  }

  private processNewReasoningEntries(agentType: string): void {
    const entries = this.reasoningLogs.get(agentType) || [];
    const recentEntries = entries.slice(-10); // Process last 10 entries

    recentEntries.forEach(entry => {
      const agentOutput = this.transformReasoningEntry(entry, agentType);
      this.agents.set(agentType, agentOutput);
      
      this.emit('agent:reasoning', {
        agentId: agentType,
        agentType: agentType,
        marketSymbol: entry.metadata?.market_symbol || '',
        reasoning: agentOutput.reasoning,
        confidence: agentOutput.confidence,
        decision: agentOutput.decision,
        timestamp: entry.timestamp
      });
    });
  }

  private transformReasoningEntry(entry: ReasoningEntry, agentType: string): AgentOutput {
    return {
      agentId: agentType,
      agentType: agentType as any,
      timestamp: entry.timestamp,
      marketSymbol: entry.metadata?.market_symbol || '',
      confidence: entry.confidence,
      decision: this.mapActionToDecision(entry.action),
      reasoning: {
        summary: entry.reasoning,
        details: {
          prompt_digest: entry.prompt_digest,
          backend: entry.backend,
          latency_ms: entry.latency_ms,
          judge_score: entry.judge_score,
          judge_confidence: entry.judge_confidence,
          judge_tags: entry.judge_tags,
          judge_notes: entry.judge_notes
        },
        factors: entry.judge_tags || []
      },
      metadata: {
        processingTime: entry.latency_ms,
        dataSource: entry.backend,
        version: '1.0.0',
        backend: entry.backend
      }
    };
  }

  private transformScorecard(rawScorecard: any, agentType: string): AgentScorecard {
    return {
      agentId: agentType,
      agentType: agentType,
      timeframe: rawScorecard.timeframe || 'day',
      timestamp: rawScorecard.timestamp || new Date().toISOString(),
      totalAnalyses: rawScorecard.total_analyses || 0,
      correctPredictions: rawScorecard.correct_predictions || 0,
      accuracyRate: rawScorecard.accuracy_rate || 0,
      averageConfidence: rawScorecard.average_confidence || 0,
      performanceMetrics: {
        precision: rawScorecard.precision || 0,
        recall: rawScorecard.recall || 0,
        f1Score: rawScorecard.f1_score || 0,
        sharpeRatio: rawScorecard.sharpe_ratio || 0,
        maxDrawdown: rawScorecard.max_drawdown || 0
      },
      recentDecisions: rawScorecard.recent_decisions || []
    };
  }

  private mapActionToDecision(action: string): 'BUY' | 'SELL' | 'HOLD' {
    const normalizedAction = action.toUpperCase();
    if (normalizedAction.includes('BUY') || normalizedAction.includes('LONG')) {
      return 'BUY';
    } else if (normalizedAction.includes('SELL') || normalizedAction.includes('SHORT')) {
      return 'SELL';
    }
    return 'HOLD';
  }

  // Public API methods
  getAgentStatus(): Map<string, AgentOutput> {
    return new Map(this.agents);
  }

  getAgentOutputs(agentType?: string): AgentOutput[] {
    if (agentType) {
      const agent = this.agents.get(agentType);
      return agent ? [agent] : [];
    }
    return Array.from(this.agents.values());
  }

  getReasoningLogs(agentType?: string, limit: number = 50): ReasoningEntry[] {
    if (agentType) {
      const logs = this.reasoningLogs.get(agentType) || [];
      return logs.slice(-limit);
    }

    const allLogs: ReasoningEntry[] = [];
    this.reasoningLogs.forEach(logs => {
      allLogs.push(...logs.slice(-limit));
    });

    return allLogs.sort((a, b) => 
      new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    ).slice(0, limit);
  }

  getScorecards(agentType?: string): AgentScorecard[] {
    if (agentType) {
      const scorecard = this.scorecards.get(agentType);
      return scorecard ? [scorecard] : [];
    }
    return Array.from(this.scorecards.values());
  }

  searchReasoningLogs(query: string, agentTypes?: string[]): ReasoningEntry[] {
    const results: ReasoningEntry[] = [];
    const searchAgentTypes = agentTypes || Array.from(this.reasoningLogs.keys());

    searchAgentTypes.forEach(agentType => {
      const logs = this.reasoningLogs.get(agentType) || [];
      const filtered = logs.filter(entry => {
        const searchText = `${entry.reasoning} ${entry.action} ${entry.metadata?.market_symbol || ''}`.toLowerCase();
        return searchText.includes(query.toLowerCase());
      });
      results.push(...filtered);
    });

    return results.sort((a, b) => 
      new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    );
  }

  getAgentConsensus(marketSymbol: string): {
    sentiment: { decision: string; confidence: number; };
    technical: { decision: string; confidence: number; };
    visual: { decision: string; confidence: number; };
    qabba: { decision: string; confidence: number; };
    risk: { decision: string; confidence: number; };
    final: { decision: string; confidence: number; reasoning: string; };
  } | null {
    const decisionAgent = this.agents.get('decision');
    if (!decisionAgent) {
      return null;
    }

    return {
      sentiment: {
        decision: this.agents.get('sentiment')?.decision || 'HOLD',
        confidence: this.agents.get('sentiment')?.confidence || 0
      },
      technical: {
        decision: this.agents.get('technical')?.decision || 'HOLD',
        confidence: this.agents.get('technical')?.confidence || 0
      },
      visual: {
        decision: this.agents.get('visual')?.decision || 'HOLD',
        confidence: this.agents.get('visual')?.confidence || 0
      },
      qabba: {
        decision: this.agents.get('qabba')?.decision || 'HOLD',
        confidence: this.agents.get('qabba')?.confidence || 0
      },
      risk: {
        decision: this.agents.get('risk')?.decision || 'HOLD',
        confidence: this.agents.get('risk')?.confidence || 0
      },
      final: {
        decision: decisionAgent.decision,
        confidence: decisionAgent.confidence,
        reasoning: decisionAgent.reasoning.summary
      }
    };
  }

  getAgentAnalytics(agentType?: string): {
    totalAnalyses: number;
    averageConfidence: number;
    accuracyRate: number;
    averageProcessingTime: number;
    topFactors: string[];
    recentPerformance: number[];
  } {
    const logs = this.getReasoningLogs(agentType);
    
    if (logs.length === 0) {
      return {
        totalAnalyses: 0,
        averageConfidence: 0,
        accuracyRate: 0,
        averageProcessingTime: 0,
        topFactors: [],
        recentPerformance: []
      };
    }

    const totalAnalyses = logs.length;
    const averageConfidence = logs.reduce((sum, log) => sum + log.confidence, 0) / totalAnalyses;
    const averageProcessingTime = logs.reduce((sum, log) => sum + log.latency_ms, 0) / totalAnalyses;
    
    const correctPredictions = logs.filter(log => log.outcome?.success).length;
    const accuracyRate = totalAnalyses > 0 ? (correctPredictions / totalAnalyses) * 100 : 0;

    // Extract top factors
    const factorCounts = new Map<string, number>();
    logs.forEach(log => {
      if (log.judge_tags) {
        log.judge_tags.forEach(tag => {
          factorCounts.set(tag, (factorCounts.get(tag) || 0) + 1);
        });
      }
    });

    const topFactors = Array.from(factorCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([factor]) => factor);

    // Recent performance (last 10 analyses)
    const recentPerformance = logs.slice(-10).map(log => log.confidence);

    return {
      totalAnalyses,
      averageConfidence,
      accuracyRate,
      averageProcessingTime,
      topFactors,
      recentPerformance
    };
  }

  async shutdown(): Promise<void> {
    this.logger.info('Shutting down agent service');
    
    // Stop file watchers
    this.fileWatchers.forEach(watcher => watcher.close());
    this.fileWatchers.clear();
    
    this.removeAllListeners();
  }
}

export const agentService = new AgentService();