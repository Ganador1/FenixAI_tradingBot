import { io, Socket } from 'socket.io-client';

/**
 * Socket.io compartido para toda la app.
 *
 * Antes cada store (`systemStore`, `agentStore`) abría su propia conexión con
 * `io(...)`, lo que resultaba en 2 WebSockets por cliente contra el mismo
 * servidor. Este módulo expone una única instancia singleton y la comparte,
 * con reference counting para que la conexión solo se cierre cuando el último
 * consumidor la libera.
 */

let socket: Socket | null = null;
let refCount = 0;

const SOCKET_OPTS = {
  path: '/socket.io',
  transports: ['websocket', 'polling'],
};

/**
 * Devuelve el socket compartido, creándolo (y conectándolo) la primera vez.
 * Cada llamada incrementa el contador de referencias: emparejar siempre con
 * `releaseSocket()` cuando el consumidor deja de necesitarlo.
 */
export function getSocket(): Socket {
  if (!socket) {
    socket = io(window.location.origin, SOCKET_OPTS);

    // En desarrollo, exponer el socket ayuda a depurar eventos en tiempo real
    // desde la consola del navegador (window.__fenixSocket.on('trade:signal', …)).
    if (import.meta.env.DEV) {
      (window as unknown as { __fenixSocket?: Socket }).__fenixSocket = socket;
    }
  }
  refCount += 1;
  return socket;
}

/**
 * Libera una referencia al socket compartido. Cuando el contador llega a cero
 * la conexión se cierra de verdad. Es seguro llamarlo de más (no baja de 0).
 */
export function releaseSocket(): void {
  refCount = Math.max(0, refCount - 1);
  if (refCount === 0 && socket) {
    socket.disconnect();
    if (import.meta.env.DEV) {
      delete (window as unknown as { __fenixSocket?: Socket }).__fenixSocket;
    }
    socket = null;
  }
}

/** Acceso al socket actual sin afectar el contador (null si no hay conexión). */
export function peekSocket(): Socket | null {
  return socket;
}
