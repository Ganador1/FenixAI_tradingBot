import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Tv, X, Minus, Square, ExternalLink } from 'lucide-react';
import { useMiniTvStore } from '@/stores/miniTvStore';

const MIN_WIDTH = 280;
const MIN_HEIGHT = 220;
const MAX_WIDTH = 900;
const MAX_HEIGHT = 800;
const HEADER_HEIGHT = 40;

const PRIMARY_URL = 'https://cryptobubbles.net/en';
const FALLBACK_URL = 'https://banterbubbles.com/';
const LOAD_TIMEOUT_MS = 6000;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function MiniTvWidget() {
  const { isOpen, isMinimized, position, size, setOpen, setMinimized, setPosition, setSize } =
    useMiniTvStore();
  const [isInteracting, setIsInteracting] = useState(false);
  const [activeUrl, setActiveUrl] = useState(PRIMARY_URL);
  const [usingFallback, setUsingFallback] = useState(false);
  const loadTimeoutRef = useRef<number | null>(null);

  const clearLoadTimeout = useCallback(() => {
    if (loadTimeoutRef.current !== null) {
      window.clearTimeout(loadTimeoutRef.current);
      loadTimeoutRef.current = null;
    }
  }, []);

  const fallbackToBanterBubbles = useCallback(() => {
    clearLoadTimeout();
    setUsingFallback(true);
    setActiveUrl(FALLBACK_URL);
  }, [clearLoadTimeout]);

  // Reset to the primary source each time the widget is (re)opened, and arm a
  // timeout so an unreachable/slow cryptobubbles.net falls back automatically.
  useEffect(() => {
    if (!isOpen) {
      clearLoadTimeout();
      return;
    }
    setActiveUrl(PRIMARY_URL);
    setUsingFallback(false);
    clearLoadTimeout();
    loadTimeoutRef.current = window.setTimeout(fallbackToBanterBubbles, LOAD_TIMEOUT_MS);
    return clearLoadTimeout;
  }, [isOpen, clearLoadTimeout, fallbackToBanterBubbles]);

  const handleIframeLoad = () => clearLoadTimeout();
  const handleIframeError = () => fallbackToBanterBubbles();

  const dragState = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const resizeState = useRef<{ startX: number; startY: number; startWidth: number; startHeight: number } | null>(
    null
  );

  const handleDragMove = useCallback(
    (e: PointerEvent) => {
      if (!dragState.current) return;
      const x = clamp(e.clientX - dragState.current.offsetX, -size.width + 80, window.innerWidth - 80);
      const y = clamp(e.clientY - dragState.current.offsetY, 0, window.innerHeight - HEADER_HEIGHT);
      setPosition({ x, y });
    },
    [setPosition, size.width]
  );

  const handleDragEnd = useCallback(() => {
    dragState.current = null;
    setIsInteracting(false);
    window.removeEventListener('pointermove', handleDragMove);
    window.removeEventListener('pointerup', handleDragEnd);
  }, [handleDragMove]);

  const handleDragStart = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button')) return;
    dragState.current = { offsetX: e.clientX - position.x, offsetY: e.clientY - position.y };
    setIsInteracting(true);
    window.addEventListener('pointermove', handleDragMove);
    window.addEventListener('pointerup', handleDragEnd);
  };

  const handleResizeMove = useCallback(
    (e: PointerEvent) => {
      if (!resizeState.current) return;
      const width = clamp(resizeState.current.startWidth + (e.clientX - resizeState.current.startX), MIN_WIDTH, MAX_WIDTH);
      const height = clamp(
        resizeState.current.startHeight + (e.clientY - resizeState.current.startY),
        MIN_HEIGHT,
        MAX_HEIGHT
      );
      setSize({ width, height });
    },
    [setSize]
  );

  const handleResizeEnd = useCallback(() => {
    resizeState.current = null;
    setIsInteracting(false);
    window.removeEventListener('pointermove', handleResizeMove);
    window.removeEventListener('pointerup', handleResizeEnd);
  }, [handleResizeMove]);

  const handleResizeStart = (e: React.PointerEvent) => {
    e.stopPropagation();
    resizeState.current = { startX: e.clientX, startY: e.clientY, startWidth: size.width, startHeight: size.height };
    setIsInteracting(true);
    window.addEventListener('pointermove', handleResizeMove);
    window.addEventListener('pointerup', handleResizeEnd);
  };

  if (!isOpen) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full border border-gray-200 bg-white px-4 py-3 shadow-lg hover:shadow-xl transition-shadow"
        title="Open CryptoBubbles mini window"
      >
        <Tv className="w-4 h-4 text-blue-600" />
        <span className="text-xs font-semibold text-gray-700">Bubbles</span>
      </button>
    );
  }

  return (
    <div
      className="fixed z-50 flex flex-col rounded-2xl border border-gray-200 bg-white shadow-2xl overflow-hidden"
      style={{
        left: position.x,
        top: position.y,
        width: size.width,
        height: isMinimized ? HEADER_HEIGHT : size.height,
        transition: isInteracting ? 'none' : 'height 150ms ease',
      }}
    >
      <div
        onPointerDown={handleDragStart}
        className="flex items-center justify-between px-3 shrink-0 bg-gray-900 text-white cursor-move select-none"
        style={{ height: HEADER_HEIGHT }}
      >
        <div className="flex items-center gap-2">
          <Tv className="w-3.5 h-3.5 text-blue-400" />
          <span className="text-xs font-semibold tracking-wide">
            {usingFallback ? 'BanterBubbles' : 'CryptoBubbles'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => window.open(activeUrl, '_blank', 'noopener,noreferrer')}
            className="p-1.5 rounded hover:bg-white/10"
            title="Open in new tab"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setMinimized(!isMinimized)}
            className="p-1.5 rounded hover:bg-white/10"
            title={isMinimized ? 'Restore' : 'Minimize'}
          >
            {isMinimized ? <Square className="w-3.5 h-3.5" /> : <Minus className="w-3.5 h-3.5" />}
          </button>
          <button onClick={() => setOpen(false)} className="p-1.5 rounded hover:bg-red-500/80" title="Close">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {!isMinimized && (
        <div className="relative flex-1">
          <iframe
            key={activeUrl}
            src={activeUrl}
            title={usingFallback ? 'BanterBubbles' : 'CryptoBubbles'}
            className="w-full h-full border-0"
            loading="lazy"
            onLoad={handleIframeLoad}
            onError={handleIframeError}
          />
          {isInteracting && <div className="absolute inset-0" />}

          <div
            onPointerDown={handleResizeStart}
            className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize"
            title="Resize"
          >
            <div className="absolute bottom-1 right-1 w-2 h-2 border-b-2 border-r-2 border-gray-400" />
          </div>
        </div>
      )}
    </div>
  );
}
