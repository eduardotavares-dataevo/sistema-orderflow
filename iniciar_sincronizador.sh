#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$DIR/.venv/bin/python"
elif [ -f "$DIR/venv/bin/python" ]; then
    PYTHON_EXEC="$DIR/venv/bin/python"
elif [ -f "/Users/eduardotavares/PycharmProjects/sistema-nova-loja/.venv/bin/python" ]; then
    PYTHON_EXEC="/Users/eduardotavares/PycharmProjects/sistema-nova-loja/.venv/bin/python"
else
    PYTHON_EXEC="$(which python3)"
fi

PID=$(pgrep -f "$DIR/sync_watcher.py")
if [ -n "$PID" ]; then
    echo "⚠️ O sincronizador já está rodando (PID: $PID)."
    exit 0
fi

echo "🚀 Iniciando sincronizador automático em segundo plano..."
nohup "$PYTHON_EXEC" "$DIR/sync_watcher.py" > "$DIR/sync_watcher.log" 2>&1 &
NEW_PID=$!
sleep 1

if ps -p $NEW_PID > /dev/null; then
    echo "✅ Sincronizador ativo com sucesso! (PID: $NEW_PID)"
    echo "📝 Logs em tempo real: tail -f $DIR/sync_watcher.log"
else
    echo "❌ Falha ao iniciar. Verifique $DIR/sync_watcher.log"
fi
