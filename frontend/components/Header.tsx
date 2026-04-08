import React from 'react';
import { Bell, User, LogOut, Sparkles, Activity } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { Button } from './ui/Button';

export function Header() {
  const { user, logout } = useAuthStore();

  return (
    <header className="sticky top-0 z-20 border-b border-gray-200 bg-white/80 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <div className="h-12 w-12 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-md">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-gray-400">Command Center</p>
            <h1 className="text-xl font-semibold text-gray-900 leading-tight">Multi-Agent Trading</h1>
            <div className="hidden md:flex items-center space-x-2 text-xs text-gray-500">
              <Activity className="w-3 h-3 text-emerald-500" />
              <span>System online</span>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-4">
          <div className="hidden md:flex items-center space-x-2 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span>Live stream</span>
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="relative text-gray-600 hover:text-gray-900"
          >
            <Bell className="w-5 h-5" />
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-rose-500 rounded-full"></span>
          </Button>

          <div className="flex items-center space-x-3">
            <div className="text-right hidden md:block">
              <div className="text-sm font-semibold text-gray-900">{user?.name}</div>
              <div className="text-xs text-gray-500 capitalize">{user?.role}</div>
            </div>
            <div className="w-10 h-10 bg-gray-100 border border-gray-200 rounded-2xl flex items-center justify-center">
              <User className="w-4 h-4 text-gray-600" />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={logout}
              className="text-gray-600 hover:text-gray-900"
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
    </header>
  );
}
