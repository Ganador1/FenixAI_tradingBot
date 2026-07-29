/**
 * SECURITY: the historical Express API is intentionally disabled.
 *
 * FenixAI's supported server is src.api.server:app_socketio. Keeping this
 * entrypoint non-runnable prevents old demo authentication and mock trading
 * routes from being exposed accidentally.
 */
throw new Error(
  'The legacy Express API has been retired. Start src.api.server:app_socketio instead.',
);
