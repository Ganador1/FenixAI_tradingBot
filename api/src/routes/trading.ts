import { Router } from 'express';
import { supabase } from '../config/supabase';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth';
import { AppError } from '../utils/errorHandler';
import { randomUUID } from 'crypto';

const router = Router();

// Apply authentication middleware to all trading routes
router.use(authenticateToken);

// Order Management
router.post('/orders', async (req: AuthenticatedRequest, res, next) => {
  try {
    const {
      instrument_id,
      order_type,
      side,
      quantity,
      price,
      stop_price,
      time_in_force = 'DAY',
      notes
    } = req.body;

    // Validate required fields
    if (!instrument_id || !order_type || !side || !quantity) {
      throw new AppError('Missing required fields', 400);
    }

    // Validate order type
    const validOrderTypes = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'];
    if (!validOrderTypes.includes(order_type)) {
      throw new AppError('Invalid order type', 400);
    }

    // Validate side
    const validSides = ['BUY', 'SELL'];
    if (!validSides.includes(side)) {
      throw new AppError('Invalid order side', 400);
    }

    // Validate price for limit orders
    if ((order_type === 'LIMIT' || order_type === 'STOP_LIMIT') && !price) {
      throw new AppError('Price required for limit orders', 400);
    }

    // Validate stop price for stop orders
    if ((order_type === 'STOP' || order_type === 'STOP_LIMIT') && !stop_price) {
      throw new AppError('Stop price required for stop orders', 400);
    }

    const order = {
      id: randomUUID(),
      user_id: req.user.id,
      instrument_id,
      order_type,
      side,
      quantity,
      price,
      stop_price,
      time_in_force,
      status: 'PENDING',
      filled_quantity: 0,
      remaining_quantity: quantity,
      avg_fill_price: null,
      notes,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };

    const { data, error } = await supabase
      .from('orders')
      .insert(order)
      .select('*')
      .single();

    if (error) {
      throw new AppError(`Failed to create order: ${error.message}`, 500);
    }

    res.status(201).json({
      success: true,
      data
    });
  } catch (error) {
    next(error);
  }
});

router.get('/orders', async (req: AuthenticatedRequest, res, next) => {
  try {
    const {
      status,
      side,
      instrument_id,
      limit = 50,
      offset = 0
    } = req.query;

    let query = supabase
      .from('orders')
      .select(`
        *,
        instrument:instruments(symbol, name)
      `)
      .eq('user_id', req.user.id)
      .order('created_at', { ascending: false })
      .range(Number(offset), Number(offset) + Number(limit) - 1);

    if (status) {
      query = query.eq('status', status);
    }

    if (side) {
      query = query.eq('side', side);
    }

    if (instrument_id) {
      query = query.eq('instrument_id', instrument_id);
    }

    const { data, error, count } = await query;

    if (error) {
      throw new AppError(`Failed to fetch orders: ${error.message}`, 500);
    }

    res.json({
      success: true,
      data,
      pagination: {
        limit: Number(limit),
        offset: Number(offset),
        total: count || data.length
      }
    });
  } catch (error) {
    next(error);
  }
});

router.get('/orders/:id', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { id } = req.params;

    const { data, error } = await supabase
      .from('orders')
      .select(`
        *,
        instrument:instruments(symbol, name),
        fills:order_fills(*)
      `)
      .eq('id', id)
      .eq('user_id', req.user.id)
      .single();

    if (error) {
      throw new AppError(`Failed to fetch order: ${error.message}`, 500);
    }

    if (!data) {
      throw new AppError('Order not found', 404);
    }

    res.json({
      success: true,
      data
    });
  } catch (error) {
    next(error);
  }
});

router.put('/orders/:id/cancel', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { id } = req.params;

    // First check if order exists and belongs to user
    const { data: existingOrder, error: fetchError } = await supabase
      .from('orders')
      .select('status')
      .eq('id', id)
      .eq('user_id', req.user.id)
      .single();

    if (fetchError) {
      throw new AppError(`Failed to fetch order: ${fetchError.message}`, 500);
    }

    if (!existingOrder) {
      throw new AppError('Order not found', 404);
    }

    // Check if order can be cancelled
    const cancellableStatuses = ['PENDING', 'PARTIALLY_FILLED'];
    if (!cancellableStatuses.includes(existingOrder.status)) {
      throw new AppError('Order cannot be cancelled in current status', 400);
    }

    const { data, error } = await supabase
      .from('orders')
      .update({
        status: 'CANCELLED',
        updated_at: new Date().toISOString()
      })
      .eq('id', id)
      .eq('user_id', req.user.id)
      .select('*')
      .single();

    if (error) {
      throw new AppError(`Failed to cancel order: ${error.message}`, 500);
    }

    res.json({
      success: true,
      data
    });
  } catch (error) {
    next(error);
  }
});

// Position Management
router.get('/positions', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { instrument_id, limit = 50, offset = 0 } = req.query;

    let query = supabase
      .from('positions')
      .select(`
        *,
        instrument:instruments(symbol, name, current_price)
      `)
      .eq('user_id', req.user.id)
      .order('updated_at', { ascending: false })
      .range(Number(offset), Number(offset) + Number(limit) - 1);

    if (instrument_id) {
      query = query.eq('instrument_id', instrument_id);
    }

    const { data, error, count } = await query;

    if (error) {
      throw new AppError(`Failed to fetch positions: ${error.message}`, 500);
    }

    // Calculate unrealized P&L for each position
    const positionsWithPnL = data.map(position => {
      const unrealized_pnl = position.quantity * (position.instrument.current_price - position.avg_price);
      const unrealized_pnl_percentage = ((position.instrument.current_price - position.avg_price) / position.avg_price) * 100;
      
      return {
        ...position,
        unrealized_pnl,
        unrealized_pnl_percentage
      };
    });

    res.json({
      success: true,
      data: positionsWithPnL,
      pagination: {
        limit: Number(limit),
        offset: Number(offset),
        total: count || data.length
      }
    });
  } catch (error) {
    next(error);
  }
});

router.get('/positions/summary', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { data: positions, error } = await supabase
      .from('positions')
      .select(`
        *,
        instrument:instruments(symbol, name, current_price)
      `)
      .eq('user_id', req.user.id);

    if (error) {
      throw new AppError(`Failed to fetch positions: ${error.message}`, 500);
    }

    // Calculate portfolio summary
    let total_value = 0;
    let total_unrealized_pnl = 0;
    let long_positions = 0;
    let short_positions = 0;

    positions.forEach(position => {
      const position_value = position.quantity * position.instrument.current_price;
      const unrealized_pnl = position.quantity * (position.instrument.current_price - position.avg_price);
      
      total_value += position_value;
      total_unrealized_pnl += unrealized_pnl;
      
      if (position.quantity > 0) {
        long_positions++;
      } else {
        short_positions++;
      }
    });

    const summary = {
      total_positions: positions.length,
      total_value,
      total_unrealized_pnl,
      total_unrealized_pnl_percentage: total_value > 0 ? (total_unrealized_pnl / total_value) * 100 : 0,
      long_positions,
      short_positions,
      positions
    };

    res.json({
      success: true,
      data: summary
    });
  } catch (error) {
    next(error);
  }
});

// Trade History
router.get('/trades', async (req: AuthenticatedRequest, res, next) => {
  try {
    const {
      instrument_id,
      side,
      start_date,
      end_date,
      limit = 50,
      offset = 0
    } = req.query;

    let query = supabase
      .from('trades')
      .select(`
        *,
        instrument:instruments(symbol, name),
        order:orders(order_type, side)
      `)
      .eq('user_id', req.user.id)
      .order('trade_time', { ascending: false })
      .range(Number(offset), Number(offset) + Number(limit) - 1);

    if (instrument_id) {
      query = query.eq('instrument_id', instrument_id);
    }

    if (side) {
      query = query.eq('side', side);
    }

    if (start_date) {
      query = query.gte('trade_time', start_date);
    }

    if (end_date) {
      query = query.lte('trade_time', end_date);
    }

    const { data, error, count } = await query;

    if (error) {
      throw new AppError(`Failed to fetch trades: ${error.message}`, 500);
    }

    res.json({
      success: true,
      data,
      pagination: {
        limit: Number(limit),
        offset: Number(offset),
        total: count || data.length
      }
    });
  } catch (error) {
    next(error);
  }
});

router.get('/trades/summary', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { start_date, end_date } = req.query;

    let query = supabase
      .from('trades')
      .select('quantity, price, side, commission, trade_time')
      .eq('user_id', req.user.id);

    if (start_date) {
      query = query.gte('trade_time', start_date);
    }

    if (end_date) {
      query = query.lte('trade_time', end_date);
    }

    const { data: trades, error } = await query;

    if (error) {
      throw new AppError(`Failed to fetch trades: ${error.message}`, 500);
    }

    // Calculate trade summary
    let total_trades = 0;
    let winning_trades = 0;
    let losing_trades = 0;
    let total_pnl = 0;
    let total_commission = 0;
    let total_volume = 0;

    trades.forEach(trade => {
      total_trades++;
      const trade_value = trade.quantity * trade.price;
      const trade_pnl = trade.side === 'BUY' ? -trade_value : trade_value; // Simplified P&L calculation
      
      total_pnl += trade_pnl;
      total_commission += trade.commission || 0;
      total_volume += Math.abs(trade_value);
      
      if (trade_pnl > 0) {
        winning_trades++;
      } else if (trade_pnl < 0) {
        losing_trades++;
      }
    });

    const summary = {
      total_trades,
      winning_trades,
      losing_trades,
      win_rate: total_trades > 0 ? (winning_trades / total_trades) * 100 : 0,
      total_pnl,
      total_commission,
      net_pnl: total_pnl - total_commission,
      total_volume,
      avg_trade_pnl: total_trades > 0 ? total_pnl / total_trades : 0
    };

    res.json({
      success: true,
      data: summary
    });
  } catch (error) {
    next(error);
  }
});

// Risk Management
router.get('/risk/metrics', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { data: positions, error: positionsError } = await supabase
      .from('positions')
      .select(`
        *,
        instrument:instruments(symbol, name, current_price, daily_change)
      `)
      .eq('user_id', req.user.id);

    if (positionsError) {
      throw new AppError(`Failed to fetch positions: ${positionsError.message}`, 500);
    }

    const { data: account, error: accountError } = await supabase
      .from('accounts')
      .select('balance, equity')
      .eq('user_id', req.user.id)
      .single();

    if (accountError) {
      throw new AppError(`Failed to fetch account: ${accountError.message}`, 500);
    }

    // Calculate risk metrics
    let total_exposure = 0;
    let total_unrealized_pnl = 0;
    let max_position_size = 0;
    let max_position_symbol = '';

    positions.forEach(position => {
      const position_value = Math.abs(position.quantity * position.instrument.current_price);
      const unrealized_pnl = position.quantity * (position.instrument.current_price - position.avg_price);
      
      total_exposure += position_value;
      total_unrealized_pnl += unrealized_pnl;
      
      if (position_value > max_position_size) {
        max_position_size = position_value;
        max_position_symbol = position.instrument.symbol;
      }
    });

    const risk_metrics = {
      account_balance: account.balance,
      account_equity: account.equity,
      total_exposure,
      exposure_percentage: (total_exposure / account.equity) * 100,
      total_unrealized_pnl,
      unrealized_pnl_percentage: (total_unrealized_pnl / account.equity) * 100,
      max_position_size,
      max_position_symbol,
      max_position_percentage: (max_position_size / account.equity) * 100,
      position_count: positions.length,
      positions
    };

    res.json({
      success: true,
      data: risk_metrics
    });
  } catch (error) {
    next(error);
  }
});

router.post('/risk/alerts', async (req: AuthenticatedRequest, res, next) => {
  try {
    const {
      alert_type,
      threshold_value,
      comparison_operator,
      notification_method = 'EMAIL',
      is_active = true
    } = req.body;

    // Validate required fields
    if (!alert_type || threshold_value === undefined || !comparison_operator) {
      throw new AppError('Missing required fields', 400);
    }

    // Validate alert type
    const validAlertTypes = ['EXPOSURE_LIMIT', 'POSITION_SIZE', 'DAILY_LOSS', 'TOTAL_LOSS'];
    if (!validAlertTypes.includes(alert_type)) {
      throw new AppError('Invalid alert type', 400);
    }

    // Validate comparison operator
    const validOperators = ['>', '<', '>=', '<=', '=='];
    if (!validOperators.includes(comparison_operator)) {
      throw new AppError('Invalid comparison operator', 400);
    }

    const alert = {
      id: randomUUID(),
      user_id: req.user.id,
      alert_type,
      threshold_value,
      comparison_operator,
      notification_method,
      is_active,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };

    const { data, error } = await supabase
      .from('risk_alerts')
      .insert(alert)
      .select('*')
      .single();

    if (error) {
      throw new AppError(`Failed to create risk alert: ${error.message}`, 500);
    }

    res.status(201).json({
      success: true,
      data
    });
  } catch (error) {
    next(error);
  }
});

router.get('/risk/alerts', async (req: AuthenticatedRequest, res, next) => {
  try {
    const { is_active, limit = 50, offset = 0 } = req.query;

    let query = supabase
      .from('risk_alerts')
      .select('*')
      .eq('user_id', req.user.id)
      .order('created_at', { ascending: false })
      .range(Number(offset), Number(offset) + Number(limit) - 1);

    if (is_active !== undefined) {
      query = query.eq('is_active', is_active === 'true');
    }

    const { data, error, count } = await query;

    if (error) {
      throw new AppError(`Failed to fetch risk alerts: ${error.message}`, 500);
    }

    res.json({
      success: true,
      data,
      pagination: {
        limit: Number(limit),
        offset: Number(offset),
        total: count || data.length
      }
    });
  } catch (error) {
    next(error);
  }
});

export default router;