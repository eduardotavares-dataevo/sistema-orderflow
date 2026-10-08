#!/usr/bin/env python3
"""
Sincronizador Automático em Tempo Real (Local Mac -> Docker VPS)
Espelha instantaneamente qualquer arquivo modificado na IDE para o ambiente online.
"""
import os
import sys
import time
import subprocess
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
VPS_HOST = os.getenv("VPS_HOST", "143.95.163.67")
VPS_PORT = os.getenv("VPS_PORT", "22022")
VPS_USER = os.getenv("VPS_USER", "root")
REMOTE_DIR = os.getenv("REMOTE_DIR", "/opt/orderflow/")

EXCLUDES = [
    ".git",
    ".venv",
    ".idea",
    "__pycache__",
    "*.pyc",
    "scratch",
    ".agents",
    "_pontos_restauracao",
    ".env",
    "*.db",
    "admin_data",
    "sync_watcher.log",
    "*.swp",
    "*.tmp"
]

class SyncHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.pending_changes = set()
        self.last_sync_time = 0
        self.debounce_seconds = 0.5
        self.has_py_changes = False

    def is_ignored(self, path):
        rel = os.path.relpath(path, LOCAL_DIR)
        parts = rel.split(os.sep)
        for part in parts:
            if part in [".git", ".venv", ".idea", "__pycache__", "scratch", ".agents", "_pontos_restauracao", "admin_data"]:
                return True
            if part.endswith(".pyc") or part.endswith(".db") or part == ".env" or part.startswith("."):
                return True
        return False

    def on_any_event(self, event):
        if event.is_directory:
            return
        if self.is_ignored(event.src_path):
            return

        rel_path = os.path.relpath(event.src_path, LOCAL_DIR)
        self.pending_changes.add(rel_path)
        if rel_path.endswith(".py") or rel_path.endswith(".html"):
            self.has_py_changes = True

    def process_sync(self):
        if not self.pending_changes:
            return

        now = time.time()
        if now - self.last_sync_time < self.debounce_seconds:
            return

        files = list(self.pending_changes)
        self.pending_changes.clear()
        py_reload = self.has_py_changes
        self.has_py_changes = False
        self.last_sync_time = now

        hora = datetime.now().strftime("%H:%M:%S")
        resumo = ", ".join(files[:3])
        if len(files) > 3:
            resumo += f" e mais {len(files)-3} arquivo(s)"

        print(f"[{hora}] ⚡ Sincronizando com VPS ({resumo})...", flush=True)

        # Executa rsync com exclusões
        exclude_args = []
        for exc in EXCLUDES:
            exclude_args.extend(["--exclude", exc])

        rsync_cmd = [
            "/usr/bin/rsync",
            "-rtz",
            "--no-owner",
            "--no-group",
            "-e", f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p {VPS_PORT}",
            *exclude_args,
            f"{LOCAL_DIR}/",
            f"{VPS_USER}@{VPS_HOST}:{REMOTE_DIR}"
        ]

        try:
            t0 = time.time()
            res = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=30)
            elapsed = time.time() - t0
            if res.returncode == 0:
                print(f"[{hora}] ✅ Arquivos espelhados na VPS com sucesso ({elapsed:.2f}s)!", flush=True)

                # Se houve alteração em arquivos Python, recarrega Gunicorn sem downtime
                if py_reload:
                    reload_cmd = [
                        "ssh", "-o", "BatchMode=yes", "-p", VPS_PORT,
                        f"{VPS_USER}@{VPS_HOST}",
                        "docker exec orderflow-base python -c 'import os, signal; os.kill(1, signal.SIGHUP)' 2>/dev/null; "
                        "docker exec orderflow-admin python -c 'import os, signal; os.kill(1, signal.SIGHUP)' 2>/dev/null"
                    ]
                    subprocess.run(reload_cmd, capture_output=True, text=True, timeout=15)
                    print(f"[{hora}] 🔄 Aplicações Docker recarregadas instantaneamente (Zero-Downtime)!", flush=True)
            else:
                print(f"[{hora}] ❌ Erro no rsync: {res.stderr.strip()}", flush=True)
        except Exception as err:
            print(f"[{hora}] ❌ Exceção ao sincronizar: {err}", flush=True)


def main():
    print("=" * 65)
    print("  🚀 ORDERFLOW — SINCRONIZADOR EM TEMPO REAL (MAC -> DOCKER VPS)")
    print("=" * 65)
    print(f"  Diretório Local : {LOCAL_DIR}")
    print(f"  Destino VPS     : {VPS_USER}@{VPS_HOST}:{VPS_PORT}{REMOTE_DIR}")
    print("  Status          : Observando alterações nos arquivos...")
    print("=" * 65, flush=True)

    handler = SyncHandler()
    observer = Observer()
    observer.schedule(handler, LOCAL_DIR, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(0.3)
            handler.process_sync()
    except KeyboardInterrupt:
        observer.stop()
        print("\nSincronizador encerrado.")
    observer.join()


if __name__ == "__main__":
    main()
