#!/usr/bin/env bash
PID=$(pgrep -f "sync_watcher.py")
if [ -z "$PID" ]; then
    echo "ℹ️ Nenhum sincronizador em execução."
else
    echo "🛑 Encerrando sincronizador (PID: $PID)..."
    kill $PID
    sleep 1
    echo "✅ Sincronizador encerrado com sucesso."
fi
