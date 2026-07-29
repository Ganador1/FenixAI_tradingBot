# Retired Express prototype

The former TypeScript/Express implementation has been removed. Its remaining
entrypoints are deliberately inert: the server entrypoints throw and the
serverless endpoint returns HTTP 410.

Do not add routes here or re-enable this service. The supported FastAPI
application owns authentication, authorization, trading safeguards, and audit
controls.

Run the supported API with:

```bash
uvicorn src.api.server:app_socketio --host 127.0.0.1 --port 8000
```
