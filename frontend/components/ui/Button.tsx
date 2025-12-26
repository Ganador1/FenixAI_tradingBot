import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'success' | 'warning' | 'default';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  icon?: React.ReactNode;
  fullWidth?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', loading = false, icon, fullWidth = false, children, disabled, ...props }, ref) => {
    return (
      <button
        className={cn(
          'inline-flex items-center justify-center gap-2 font-medium rounded-lg transition-all focus:outline-none focus:ring-2 focus:ring-offset-2',
          {
            'bg-cyan-600 text-white hover:bg-cyan-500 focus:ring-cyan-500 disabled:bg-cyan-800': variant === 'primary' || variant === 'default',
            'bg-slate-700 text-slate-200 hover:bg-slate-600 focus:ring-slate-500 disabled:bg-slate-800': variant === 'secondary',
            'text-slate-300 hover:bg-slate-700/50 focus:ring-slate-500': variant === 'ghost',
            'bg-red-600 text-white hover:bg-red-500 focus:ring-red-500 disabled:bg-red-800': variant === 'danger',
            'border border-slate-600 text-slate-300 hover:bg-slate-700/50 focus:ring-slate-500': variant === 'outline',
            'bg-emerald-600 text-white hover:bg-emerald-500 focus:ring-emerald-500 disabled:bg-emerald-800': variant === 'success',
            'bg-amber-600 text-white hover:bg-amber-500 focus:ring-amber-500 disabled:bg-amber-800': variant === 'warning',
            'px-3 py-1.5 text-sm': size === 'sm',
            'px-4 py-2 text-base': size === 'md',
            'px-6 py-3 text-lg': size === 'lg',
            'opacity-50 cursor-not-allowed': disabled || loading,
            'w-full': fullWidth,
          },
          className
        )}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading && <Loader2 className="w-4 h-4 animate-spin" />}
        {icon && !loading && icon}
        {children}
      </button>
    );
  },
);

Button.displayName = 'Button';