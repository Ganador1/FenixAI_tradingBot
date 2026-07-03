#!/bin/bash
# Start Vite dev server in a way that survives terminal closure
cd "/Volumes/Ganador disk/Fenix unic agent/FenixAI/frontend"
exec npx vite --host 127.0.0.1 --port 5173 >> ../logs/dev_frontend.log 2>&1