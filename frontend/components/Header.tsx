import React from 'react';
import { Bell, User, LogOut, Sparkles, Activity } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { Button } from './ui/Button';

export function Header() {
  const { user, logout } = useAuthStore();

  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-slate-900/70 backdrop-blur-2xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <div className="h-12 w-12 rounded-2xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/30">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Command Center</p>
            <h1 className="text-xl font-semibold text-white leading-tight">Multi-Agent Trading</h1>
            <div className="hidden md:flex items-center space-x-2 text-xs text-slate-400">
              <Activity className="w-3 h-3 text-emerald-400" />
              <span>System online</span>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-4">
          <div className="hidden md:flex items-center space-x-2 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-400/30 text-emerald-200 text-xs">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span>Live stream</span>
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="relative text-slate-100 hover:text-white"
          >
            <Bell className="w-5 h-5" />
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-rose-500 rounded-full"></span>
          </Button>
          
          <div className="flex items-center space-x-3">
            <div className="text-right hidden md:block">
              <div className="text-sm font-semibold text-white">{user?.name}</div>
              <div className="text-xs text-slate-400 capitalize">{user?.role}</div>
            </div>
            <div className="w-10 h-10 bg-slate-800 border border-white/5 rounded-2xl flex items-center justify-center">
              <User className="w-4 h-4 text-white" />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={logout}
              className="text-slate-200 hover:text-white"
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
    </header>
  );
}