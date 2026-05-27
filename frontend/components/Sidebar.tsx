import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  TrendingUp,
  Brain,
  BarChart3,
  Settings,
  Users,
  Activity,
  Database,
  Shield,
  Sparkles,
  Wifi
} from 'lucide-react';
import { cn } from '@/lib/utils';

const sidebarItems = [
  {
    name: 'Dashboard',
    href: '/dashboard',
    icon: LayoutDashboard,
    roles: ['admin', 'trader', 'analyst', 'ai_agent']
  },
  {
    name: 'Market Data',
    href: '/market',
    icon: TrendingUp,
    roles: ['admin', 'trader', 'analyst', 'ai_agent']
  },
  {
    name: 'Trading',
    href: '/trading',
    icon: BarChart3,
    roles: ['admin', 'trader']
  },
  {
    name: 'AI Agents',
    href: '/agents',
    icon: Brain,
    roles: ['admin', 'analyst', 'ai_agent']
  },
  {
    name: 'Reasoning Bank',
    href: '/reasoning',
    icon: Database,
    roles: ['admin', 'analyst', 'ai_agent']
  },
  {
    name: 'System Monitor',
    href: '/system',
    icon: Activity,
    roles: ['admin', 'analyst']
  },
  {
    name: 'Users',
    href: '/users',
    icon: Users,
    roles: ['admin']
  },
  {
    name: 'Settings',
    href: '/settings',
    icon: Settings,
    roles: ['admin', 'trader', 'analyst']
  }
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="relative hidden lg:flex w-72 flex-col border-r border-gray-200 bg-white shadow-sm">
      <div className="p-6 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-2xl flex items-center justify-center shadow-md">
            <Shield className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-gray-400">Fenix AI</p>
            <span className="text-lg font-semibold text-gray-900">Trading Control</span>
          </div>
        </div>
        <div className="flex items-center text-xs text-blue-700 bg-blue-50 px-3 py-1 rounded-full border border-blue-200">
          <Wifi className="w-3 h-3 mr-1" /> Live
        </div>
      </div>

      <nav className="px-4 pb-8 space-y-3 overflow-y-auto">
        <div className="px-3 text-xs uppercase tracking-[0.2em] text-gray-400">Navigation</div>
        <ul className="space-y-1">
          {sidebarItems.map((item) => {
            const active = location.pathname === item.href;
            return (
              <li key={item.href}>
                <Link
                  to={item.href}
                  className={cn(
                    "group flex items-center justify-between px-4 py-3 rounded-xl border transition-all duration-200",
                    active
                      ? "border-blue-200 bg-blue-50 text-blue-900 shadow-sm"
                      : "border-transparent text-gray-600 hover:border-gray-200 hover:bg-gray-50"
                  )}
                >
                  <div className="flex items-center space-x-3">
                    <div className={cn(
                      "p-2 rounded-lg border",
                      active ? "border-blue-200 bg-blue-100" : "border-gray-200 bg-gray-50 group-hover:border-blue-200"
                    )}>
                      <item.icon className={cn("w-4 h-4", active ? "text-blue-600" : "text-gray-500")}/>
                    </div>
                    <span className="font-medium tracking-tight">{item.name}</span>
                  </div>
                  {active && (
                    <Sparkles className="w-4 h-4 text-blue-500" />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
