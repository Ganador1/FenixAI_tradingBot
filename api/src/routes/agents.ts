import { Router } from 'express';
import { agentService } from '../services/agentService';

const router = Router();

// Get all agents status
router.get('/status', (req, res) => {
  try {
    const agents = agentService.getAgentOutputs();
    
    res.json({
      success: true,
      data: agents
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent status',
      details: (error as Error).message
    });
  }
});

// Get specific agent status
router.get('/status/:agentType', (req, res) => {
  try {
    const { agentType } = req.params;
    const agents = agentService.getAgentOutputs(agentType);
    
    if (agents.length === 0) {
      return res.status(404).json({
        success: false,
        error: 'Agent not found'
      });
    }
    
    res.json({
      success: true,
      data: agents[0]
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent status',
      details: (error as Error).message
    });
  }
});

// Get agent scorecards
router.get('/scorecards/:agentType?', (req, res) => {
  try {
    const { agentType } = req.params;
    const scorecards = agentService.getScorecards(agentType);
    
    res.json({
      success: true,
      data: scorecards
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent scorecards',
      details: (error as Error).message
    });
  }
});

// Get agent analytics
router.get('/analytics/:agentType?', (req, res) => {
  try {
    const { agentType } = req.params;
    const analytics = agentService.getAgentAnalytics(agentType);
    
    res.json({
      success: true,
      data: analytics
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent analytics',
      details: (error as Error).message
    });
  }
});

// Get agent consensus
router.get('/consensus', (req, res) => {
  try {
    const { symbol } = req.query;
    
    if (!symbol || typeof symbol !== 'string') {
      return res.status(400).json({
        success: false,
        error: 'Market symbol is required'
      });
    }
    
    const consensus = agentService.getAgentConsensus(symbol);
    
    if (!consensus) {
      return res.status(404).json({
        success: false,
        error: 'No consensus data available'
      });
    }
    
    res.json({
      success: true,
      data: consensus
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent consensus',
      details: (error as Error).message
    });
  }
});

// Update agent configuration
router.post('/config/:agentType', (req, res) => {
  try {
    const { agentType } = req.params;
    const config = req.body;
    
    // Here you would typically save the configuration
    // For now, we'll just validate and return success
    if (!['sentiment', 'technical', 'visual', 'qabba', 'decision', 'risk'].includes(agentType)) {
      return res.status(400).json({
        success: false,
        error: 'Invalid agent type'
      });
    }
    
    res.json({
      success: true,
      message: `Configuration updated for ${agentType}`,
      data: config
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to update agent configuration',
      details: (error as Error).message
    });
  }
});

// Get agent performance summary
router.get('/performance', (req, res) => {
  try {
    const agentTypes = ['sentiment', 'technical', 'visual', 'qabba', 'decision', 'risk'];
    const performance = agentTypes.map(agentType => {
      const analytics = agentService.getAgentAnalytics(agentType);
      const scorecards = agentService.getScorecards(agentType);
      
      return {
        agentType,
        analytics,
        scorecards
      };
    });
    
    res.json({
      success: true,
      data: performance
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get agent performance',
      details: (error as Error).message
    });
  }
});

export default router;