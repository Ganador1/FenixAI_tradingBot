/**
 * SECURITY: the historical Express/Socket.IO server is intentionally disabled.
 *
 * It contained demo users and unauthenticated mock realtime channels. FenixAI
 * now exposes only src.api.server:app_socketio.
 */
throw new Error(
  'The legacy Express API has been retired. Start src.api.server:app_socketio instead.',
);
