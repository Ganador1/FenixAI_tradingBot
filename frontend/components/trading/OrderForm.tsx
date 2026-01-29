import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { TrendingUp, TrendingDown, AlertCircle } from 'lucide-react';

interface OrderFormData {
  symbol: string;
  type: 'market' | 'limit' | 'stop';
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  stopPrice: number;
}

interface OrderFormProps {
  onSubmit: (order: OrderFormData) => Promise<void>;
  isLoading?: boolean;
  defaultSymbol?: string;
}

export function OrderForm({ onSubmit, isLoading = false, defaultSymbol = 'BTCUSDT' }: OrderFormProps) {
  const [form, setForm] = useState<OrderFormData>({
    symbol: defaultSymbol,
    type: 'market',
    side: 'buy',
    quantity: 0.1,
    price: 0,
    stopPrice: 0
  });
  
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    
    // Validation
    if (form.quantity <= 0) {
      setError('Quantity must be greater than 0');
      return;
    }
    
    if (form.type === 'limit' && form.price <= 0) {
      setError('Price is required for limit orders');
      return;
    }
    
    if (form.type === 'stop' && form.stopPrice <= 0) {
      setError('Stop price is required for stop orders');
      return;
    }

    try {
      await onSubmit(form);
      // Reset form after successful submission
      setForm(prev => ({
        ...prev,
        quantity: 0.1,
        price: 0,
        stopPrice: 0
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit order');
    }
  };

  const updateForm = <K extends keyof OrderFormData>(field: K, value: OrderFormData[K]) => {
    setForm(prev => ({ ...prev, [field]: value }));
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {form.side === 'buy' ? (
            <TrendingUp className="w-5 h-5 text-green-600" />
          ) : (
            <TrendingDown className="w-5 h-5 text-red-600" />
          )}
          Place Order
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="flex items-center gap-2 p-3 bg-red-50 text-red-700 rounded-lg text-sm">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}

          {/* Symbol */}
          <div>
            <label className="block text-sm font-medium mb-1">Symbol</label>
            <Input
              value={form.symbol}
              onChange={(e) => updateForm('symbol', e.target.value.toUpperCase())}
              placeholder="BTCUSDT"
            />
          </div>

          {/* Type & Side */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Type</label>
              <Select
                value={form.type}
                onChange={(e) => updateForm('type', e.target.value as OrderFormData['type'])}
              >
                <option value="market">Market</option>
                <option value="limit">Limit</option>
                <option value="stop">Stop</option>
              </Select>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Side</label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={form.side === 'buy' ? 'primary' : 'outline'}
                  className={form.side === 'buy' ? 'bg-green-600 hover:bg-green-700 flex-1' : 'flex-1'}
                  onClick={() => updateForm('side', 'buy')}
                >
                  Buy
                </Button>
                <Button
                  type="button"
                  variant={form.side === 'sell' ? 'primary' : 'outline'}
                  className={form.side === 'sell' ? 'bg-red-600 hover:bg-red-700 flex-1' : 'flex-1'}
                  onClick={() => updateForm('side', 'sell')}
                >
                  Sell
                </Button>
              </div>
            </div>
          </div>

          {/* Quantity */}
          <div>
            <label className="block text-sm font-medium mb-1">Quantity</label>
            <Input
              type="number"
              step="0.001"
              min="0"
              value={form.quantity}
              onChange={(e) => updateForm('quantity', parseFloat(e.target.value) || 0)}
              placeholder="0.1"
            />
          </div>

          {/* Price (for limit orders) */}
          {form.type === 'limit' && (
            <div>
              <label className="block text-sm font-medium mb-1">Limit Price</label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={form.price || ''}
                onChange={(e) => updateForm('price', parseFloat(e.target.value) || 0)}
                placeholder="Enter limit price"
              />
            </div>
          )}

          {/* Stop Price (for stop orders) */}
          {form.type === 'stop' && (
            <div>
              <label className="block text-sm font-medium mb-1">Stop Price</label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={form.stopPrice || ''}
                onChange={(e) => updateForm('stopPrice', parseFloat(e.target.value) || 0)}
                placeholder="Enter stop price"
              />
            </div>
          )}

          {/* Submit Button */}
          <Button
            type="submit"
            disabled={isLoading}
            className={`w-full ${
              form.side === 'buy' 
                ? 'bg-green-600 hover:bg-green-700' 
                : 'bg-red-600 hover:bg-red-700'
            }`}
          >
            {isLoading ? 'Submitting...' : `${form.side === 'buy' ? 'Buy' : 'Sell'} ${form.symbol}`}
          </Button>

          {/* Quick Amount Buttons */}
          <div className="flex gap-2">
            {[0.01, 0.1, 0.5, 1].map(amount => (
              <Button
                key={amount}
                type="button"
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => updateForm('quantity', amount)}
              >
                {amount}
              </Button>
            ))}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default OrderForm;
