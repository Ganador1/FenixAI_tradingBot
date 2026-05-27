import React, { ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Header } from './Header';
import { useAuthStore } from '@/stores/authStore';

interface LayoutProps {
  children?: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  const { user } = useAuthStore();

  if (!user) {
    return <Outlet />;
  }

  return (
    <div className="relative flex h-screen w-full overflow-hidden bg-gray-50">
      <Sidebar />
      <div className="relative z-10 flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto px-4 py-4 sm:px-8 lg:px-10">
          <div className="max-w-7xl mx-auto space-y-6">
            {children}
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
