import { Router } from 'express';
import { agentService } from '../services/agentService';

const router = Router();

// Search reasoning logs
router.get('/search', (req, res) => {
  try {
    const { 
      query, 
      agentTypes, 
      startTime, 
      endTime, 
      tags, 
      limit = 50 
    } = req.query;

    // Validate limit
    const maxLimit = Math.min(parseInt(limit as string) || 50, 100);
    
    // Get all reasoning logs
    let results = agentService.getReasoningLogs(undefined, maxLimit);
    
    // Apply filters
    if (query && typeof query === 'string') {
      results = agentService.searchReasoningLogs(query, agentTypes as string[]);
    }
    
    if (agentTypes && Array.isArray(agentTypes)) {
      results = results.filter(log => agentTypes.includes(log.agent));
    }
    
    if (startTime) {
      const start = new Date(startTime as string);
      results = results.filter(log => new Date(log.timestamp) >= start);
    }
    
    if (endTime) {
      const end = new Date(endTime as string);
      results = results.filter(log => new Date(log.timestamp) <= end);
    }
    
    if (tags && Array.isArray(tags)) {
      results = results.filter(log => 
        log.judge_tags && log.judge_tags.some(tag => tags.includes(tag))
      );
    }
    
    // Create facets for filtering
    const facets = {
      agents: Array.from(new Set(results.map(log => log.agent))),
      tags: Array.from(new Set(results.flatMap(log => log.judge_tags || []))),
      dateRange: {
        min: results.length > 0 ? results[results.length - 1].timestamp : null,
        max: results.length > 0 ? results[0].timestamp : null
      }
    };

    res.json({
      success: true,
      data: {
        results: results.slice(0, maxLimit),
        total: results.length,
        facets
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to search reasoning logs',
      details: (error as Error).message
    });
  }
});

// Get reasoning patterns
router.get('/patterns', (req, res) => {
  try {
    const logs = agentService.getReasoningLogs(undefined, 1000);
    
    // Analyze patterns
    const patterns = analyzePatterns(logs);
    const correlations = analyzeCorrelations(logs);
    const successRates = analyzeSuccessRates(logs);

    res.json({
      success: true,
      data: {
        patterns,
        correlations,
        successRates
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to analyze reasoning patterns',
      details: (error as Error).message
    });
  }
});

// Get recent reasoning entries
router.get('/recent/:agentType?', (req, res) => {
  try {
    const { agentType } = req.params;
    const { limit = 50 } = req.query;
    
    const logs = agentService.getReasoningLogs(agentType, parseInt(limit as string));
    
    res.json({
      success: true,
      data: logs
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get recent reasoning entries',
      details: (error as Error).message
    });
  }
});

// Get reasoning statistics
router.get('/stats', (req, res) => {
  try {
    const logs = agentService.getReasoningLogs(undefined, 10000);
    
    const stats = {
      totalEntries: logs.length,
      agentDistribution: calculateAgentDistribution(logs),
      confidenceDistribution: calculateConfidenceDistribution(logs),
      decisionDistribution: calculateDecisionDistribution(logs),
      timeAnalysis: calculateTimeAnalysis(logs),
      judgeAnalysis: calculateJudgeAnalysis(logs)
    };

    res.json({
      success: true,
      data: stats
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get reasoning statistics',
      details: (error as Error).message
    });
  }
});

// Export reasoning data
router.get('/export', (req, res) => {
  try {
    const { format = 'json', agentTypes, startTime, endTime } = req.query;
    
    let logs = agentService.getReasoningLogs(undefined, 10000);
    
    if (agentTypes && Array.isArray(agentTypes)) {
      logs = logs.filter(log => agentTypes.includes(log.agent));
    }
    
    if (startTime) {
      const start = new Date(startTime as string);
      logs = logs.filter(log => new Date(log.timestamp) >= start);
    }
    
    if (endTime) {
      const end = new Date(endTime as string);
      logs = logs.filter(log => new Date(log.timestamp) <= end);
    }
    
    if (format === 'csv') {
      const csv = convertToCSV(logs);
      res.setHeader('Content-Type', 'text/csv');
      res.setHeader('Content-Disposition', 'attachment; filename="reasoning_logs.csv"');
      res.send(csv);
    } else {
      res.json({
        success: true,
        data: logs
      });
    }
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to export reasoning data',
      details: (error as Error).message
    });
  }
});

// Helper functions
function analyzePatterns(logs: any[]) {
  const patterns = {
    commonDecisions: {} as Record<string, number>,
    confidenceTrends: [] as number[],
    agentCorrelations: {} as Record<string, number>,
    marketPatterns: {} as Record<string, any>
  };

  // Analyze common decisions
  logs.forEach(log => {
    patterns.commonDecisions[log.action] = (patterns.commonDecisions[log.action] || 0) + 1;
  });

  // Analyze confidence trends
  patterns.confidenceTrends = logs.slice(-100).map(log => log.confidence);

  // Analyze agent correlations
  const agentGroups = logs.reduce((groups, log) => {
    if (!groups[log.agent]) groups[log.agent] = [];
    groups[log.agent].push(log);
    return groups;
  }, {} as Record<string, any[]>);

  Object.keys(agentGroups).forEach(agent => {
    const agentLogs = agentGroups[agent];
    const avgConfidence = agentLogs.reduce((sum, log) => sum + log.confidence, 0) / agentLogs.length;
    patterns.agentCorrelations[agent] = avgConfidence;
  });

  return patterns;
}

function analyzeCorrelations(logs: any[]) {
  const correlations = {
    confidenceVsSuccess: calculateCorrelation(
      logs.map(log => log.confidence),
      logs.map(log => log.outcome?.success ? 1 : 0)
    ),
    timeVsAccuracy: calculateTimeAccuracyCorrelation(logs),
    agentAgreement: calculateAgentAgreement(logs)
  };

  return correlations;
}

function analyzeSuccessRates(logs: any[]) {
  const successRates = {
    overall: calculateOverallSuccessRate(logs),
    byAgent: calculateSuccessRateByAgent(logs),
    byDecision: calculateSuccessRateByDecision(logs),
    byConfidence: calculateSuccessRateByConfidence(logs)
  };

  return successRates;
}

function calculateAgentDistribution(logs: any[]) {
  const distribution = {} as Record<string, number>;
  logs.forEach(log => {
    distribution[log.agent] = (distribution[log.agent] || 0) + 1;
  });
  return distribution;
}

function calculateConfidenceDistribution(logs: any[]) {
  const ranges = {
    '0.0-0.2': 0,
    '0.2-0.4': 0,
    '0.4-0.6': 0,
    '0.6-0.8': 0,
    '0.8-1.0': 0
  };

  logs.forEach(log => {
    const confidence = log.confidence;
    if (confidence < 0.2) ranges['0.0-0.2']++;
    else if (confidence < 0.4) ranges['0.2-0.4']++;
    else if (confidence < 0.6) ranges['0.4-0.6']++;
    else if (confidence < 0.8) ranges['0.6-0.8']++;
    else ranges['0.8-1.0']++;
  });

  return ranges;
}

function calculateDecisionDistribution(logs: any[]) {
  const distribution = {} as Record<string, number>;
  logs.forEach(log => {
    distribution[log.action] = (distribution[log.action] || 0) + 1;
  });
  return distribution;
}

function calculateTimeAnalysis(logs: any[]) {
  const hourly = Array(24).fill(0);
  const daily = Array(7).fill(0);

  logs.forEach(log => {
    const date = new Date(log.timestamp);
    hourly[date.getHours()]++;
    daily[date.getDay()]++;
  });

  return { hourly, daily };
}

function calculateJudgeAnalysis(logs: any[]) {
  const judged = logs.filter(log => log.judge_score !== undefined);
  
  return {
    totalJudged: judged.length,
    averageScore: judged.reduce((sum, log) => sum + (log.judge_score || 0), 0) / judged.length,
    averageConfidence: judged.reduce((sum, log) => sum + (log.judge_confidence || 0), 0) / judged.length,
    tagDistribution: calculateTagDistribution(judged)
  };
}

function calculateTagDistribution(logs: any[]) {
  const tags = {} as Record<string, number>;
  logs.forEach(log => {
    if (log.judge_tags) {
      log.judge_tags.forEach(tag => {
        tags[tag] = (tags[tag] || 0) + 1;
      });
    }
  });
  return tags;
}

function calculateCorrelation(x: number[], y: number[]): number {
  if (x.length !== y.length || x.length === 0) return 0;
  
  const n = x.length;
  const sumX = x.reduce((a, b) => a + b, 0);
  const sumY = y.reduce((a, b) => a + b, 0);
  const sumXY = x.reduce((sum, xi, i) => sum + xi * y[i], 0);
  const sumX2 = x.reduce((sum, xi) => sum + xi * xi, 0);
  const sumY2 = y.reduce((sum, yi) => sum + yi * yi, 0);
  
  const numerator = n * sumXY - sumX * sumY;
  const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
  
  return denominator === 0 ? 0 : numerator / denominator;
}

function calculateTimeAccuracyCorrelation(logs: any[]): number {
  const recentLogs = logs.slice(-100);
  const times = recentLogs.map((_, index) => index);
  const accuracies = recentLogs.map(log => log.outcome?.success ? 1 : 0);
  
  return calculateCorrelation(times, accuracies);
}

function calculateAgentAgreement(logs: any[]): number {
  const agentDecisions = {} as Record<string, string[]>;
  
  logs.forEach(log => {
    if (!agentDecisions[log.agent]) {
      agentDecisions[log.agent] = [];
    }
    agentDecisions[log.agent].push(log.action);
  });

  const agents = Object.keys(agentDecisions);
  if (agents.length < 2) return 0;

  let agreements = 0;
  let total = 0;

  for (let i = 0; i < agents.length - 1; i++) {
    for (let j = i + 1; j < agents.length; j++) {
      const decisions1 = agentDecisions[agents[i]];
      const decisions2 = agentDecisions[agents[j]];
      
      const minLength = Math.min(decisions1.length, decisions2.length);
      for (let k = 0; k < minLength; k++) {
        if (decisions1[k] === decisions2[k]) agreements++;
        total++;
      }
    }
  }

  return total > 0 ? agreements / total : 0;
}

function calculateOverallSuccessRate(logs: any[]): number {
  const judged = logs.filter(log => log.outcome !== undefined);
  if (judged.length === 0) return 0;
  
  const successful = judged.filter(log => log.outcome?.success).length;
  return (successful / judged.length) * 100;
}

function calculateSuccessRateByAgent(logs: any[]): Record<string, number> {
  const byAgent = {} as Record<string, { successful: number; total: number }>;
  
  logs.forEach(log => {
    if (log.outcome !== undefined) {
      if (!byAgent[log.agent]) {
        byAgent[log.agent] = { successful: 0, total: 0 };
      }
      byAgent[log.agent].total++;
      if (log.outcome.success) {
        byAgent[log.agent].successful++;
      }
    }
  });

  const rates = {} as Record<string, number>;
  Object.keys(byAgent).forEach(agent => {
    rates[agent] = (byAgent[agent].successful / byAgent[agent].total) * 100;
  });

  return rates;
}

function calculateSuccessRateByDecision(logs: any[]): Record<string, number> {
  const byDecision = {} as Record<string, { successful: number; total: number }>;
  
  logs.forEach(log => {
    if (log.outcome !== undefined) {
      if (!byDecision[log.action]) {
        byDecision[log.action] = { successful: 0, total: 0 };
      }
      byDecision[log.action].total++;
      if (log.outcome.success) {
        byDecision[log.action].successful++;
      }
    }
  });

  const rates = {} as Record<string, number>;
  Object.keys(byDecision).forEach(decision => {
    rates[decision] = (byDecision[decision].successful / byDecision[decision].total) * 100;
  });

  return rates;
}

function calculateSuccessRateByConfidence(logs: any[]): Record<string, number> {
  const ranges = {
    '0.0-0.2': { successful: 0, total: 0 },
    '0.2-0.4': { successful: 0, total: 0 },
    '0.4-0.6': { successful: 0, total: 0 },
    '0.6-0.8': { successful: 0, total: 0 },
    '0.8-1.0': { successful: 0, total: 0 }
  };

  logs.forEach(log => {
    if (log.outcome !== undefined) {
      const confidence = log.confidence;
      let range: keyof typeof ranges;
      
      if (confidence < 0.2) range = '0.0-0.2';
      else if (confidence < 0.4) range = '0.2-0.4';
      else if (confidence < 0.6) range = '0.4-0.6';
      else if (confidence < 0.8) range = '0.6-0.8';
      else range = '0.8-1.0';

      ranges[range].total++;
      if (log.outcome.success) {
        ranges[range].successful++;
      }
    }
  });

  const rates = {} as Record<string, number>;
  Object.keys(ranges).forEach(range => {
    if (ranges[range as keyof typeof ranges].total > 0) {
      rates[range] = (ranges[range as keyof typeof ranges].successful / 
                      ranges[range as keyof typeof ranges].total) * 100;
    }
  });

  return rates;
}

function convertToCSV(logs: any[]): string {
  const headers = [
    'Agent', 'Timestamp', 'Action', 'Confidence', 'Reasoning', 
    'Backend', 'Latency (ms)', 'Judge Score', 'Judge Confidence', 
    'Judge Tags', 'Outcome Success', 'Outcome Reward'
  ];

  const rows = logs.map(log => [
    log.agent,
    log.timestamp,
    log.action,
    log.confidence,
    `"${log.reasoning.replace(/"/g, '""')}"`,
    log.backend,
    log.latency_ms,
    log.judge_score || '',
    log.judge_confidence || '',
    (log.judge_tags || []).join(';'),
    log.outcome?.success || '',
    log.outcome?.reward || ''
  ]);

  return [headers, ...rows].map(row => row.join(',')).join('\n');
}

export default router;