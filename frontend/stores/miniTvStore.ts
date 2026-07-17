import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface Position {
  x: number;
  y: number;
}

interface Size {
  width: number;
  height: number;
}

const DEFAULT_SIZE: Size = { width: 380, height: 440 };

function defaultPosition(size: Size): Position {
  if (typeof window === 'undefined') {
    return { x: 40, y: 40 };
  }
  return {
    x: Math.max(24, window.innerWidth - size.width - 32),
    y: Math.max(24, window.innerHeight - size.height - 32),
  };
}

interface MiniTvState {
  isOpen: boolean;
  isMinimized: boolean;
  position: Position;
  size: Size;
  setOpen: (open: boolean) => void;
  toggleOpen: () => void;
  setMinimized: (minimized: boolean) => void;
  setPosition: (position: Position) => void;
  setSize: (size: Size) => void;
}

export const useMiniTvStore = create<MiniTvState>()(
  persist(
    (set) => ({
      isOpen: false,
      isMinimized: false,
      position: defaultPosition(DEFAULT_SIZE),
      size: DEFAULT_SIZE,
      setOpen: (open) => set({ isOpen: open }),
      toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),
      setMinimized: (minimized) => set({ isMinimized: minimized }),
      setPosition: (position) => set({ position }),
      setSize: (size) => set({ size }),
    }),
    {
      name: 'mini-tv-storage',
    }
  )
);
