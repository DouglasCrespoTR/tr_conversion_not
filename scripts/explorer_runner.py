#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Explorer Runner - Wrapper Python para o screen_explorer.ts

Gerencia uma sessao long-running do Playwright para teste exploratorio.
O agente taxone-explorer invoca este script via Bash.

Uso:
  python explorer_runner.py start                              # Inicia sessao
  python explorer_runner.py cmd login --empresa "077"          # Login
  python explorer_runner.py cmd navigate --menu-path "ICMS > APURACAO"
  python explorer_runner.py cmd screenshot --output "tests/123/explore_01.png"
  python explorer_runner.py cmd inspect                        # Lista elementos
  python explorer_runner.py cmd click --selector "#btn-pesquisar"
  python explorer_runner.py cmd click --text "Pesquisar"
  python explorer_runner.py cmd fill --selector "#input-data" --value "01/01/2026"
  python explorer_runner.py cmd fill --label "Data Inicial" --value "01/01/2026"
  python explorer_runner.py cmd select --selector "#sel-tipo" --value "ICMS"
  python explorer_runner.py cmd grid-read
  python explorer_runner.py cmd validate
  python explorer_runner.py stop                               # Fecha sessao

  # Modo multi-command (le comandos JSON do stdin, uma linha por comando):
  python explorer_runner.py multi < commands.jsonl
"""

import argparse
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


# === Configuracao ===
IS_WINDOWS = platform.system() == "Windows"
from env_loader import get_repo_path
PLAYWRIGHT_REPO = Path(get_repo_path("PLAYWRIGHT_REPO"))
TAXONE_SUPPORT = Path(os.environ.get("TAXONE_SUPPORT_DIR", str(Path(__file__).resolve().parent.parent)))
EXPLORER_SCRIPT = "scripts/screen_explorer.ts"

# PID file para rastrear o processo long-running
PID_FILE = TAXONE_SUPPORT / ".explorer_session.json"

# Timeout padrao para leitura de resposta (segundos)
DEFAULT_READ_TIMEOUT = 120

# Tempo de espera para o ts-node compilar e iniciar (segundos)
STARTUP_WAIT = 15


class ExplorerSession:
    """Gerencia o processo long-running do screen_explorer.ts.

    Uses a dedicated reader thread + queue to safely read stdout lines
    without race conditions from multiple threads doing readline().
    """

    def __init__(self):
        self.process = None
        self._line_queue = queue.Queue()
        self._reader_thread = None
        self._reader_alive = False

    def _stdout_reader(self):
        """Dedicated thread that reads stdout lines into a queue."""
        try:
            while self._reader_alive and self.process and self.process.poll() is None:
                line = self.process.stdout.readline()
                if not line:
                    break
                self._line_queue.put(line)
        except Exception:
            pass
        self._reader_alive = False

    def start(self, quiet=False):
        """Inicia o processo screen_explorer.ts."""
        if not PLAYWRIGHT_REPO.exists():
            self._print({"status": "error",
                          "message": f"Playwright repo nao encontrado: {PLAYWRIGHT_REPO}"}, quiet)
            return False

        script_path = PLAYWRIGHT_REPO / EXPLORER_SCRIPT
        if not script_path.exists():
            self._print({"status": "error",
                          "message": f"screen_explorer.ts nao encontrado: {script_path}"}, quiet)
            return False

        if not (PLAYWRIGHT_REPO / "node_modules").exists():
            self._print({"status": "error",
                          "message": f"node_modules nao encontrado. Executar: cd {PLAYWRIGHT_REPO} && npm ci"}, quiet)
            return False

        try:
            env_vars = os.environ.copy()
            # Respect 'environment' if already set, default to 'qa'
            if "environment" not in env_vars:
                env_vars["environment"] = "qa"
            env_vars["DOTENV_CONFIG_QUIET"] = "true"

            self.process = subprocess.Popen(
                ["npx", "ts-node", EXPLORER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(PLAYWRIGHT_REPO),
                shell=IS_WINDOWS,
                encoding="utf-8",
                errors="replace",
                env=env_vars,
            )

            # Start dedicated reader thread
            self._reader_alive = True
            self._reader_thread = threading.Thread(target=self._stdout_reader, daemon=True)
            self._reader_thread.start()

            # Salvar PID
            session_info = {
                "pid": self.process.pid,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            PID_FILE.write_text(json.dumps(session_info), encoding="utf-8")

            # Wait for ts-node to compile and start
            _print_stderr(f"Waiting {STARTUP_WAIT}s for ts-node compilation...")
            time.sleep(STARTUP_WAIT)

            if self.process.poll() is not None:
                stderr_output = self.process.stderr.read() if self.process.stderr else ""
                self._print({"status": "error",
                              "message": f"Processo encerrou prematuramente. stderr: {stderr_output[:500]}"}, quiet)
                return False

            # Drain any non-JSON lines from startup (dotenv, npm warnings)
            self._drain_startup_lines()

            self._print({"status": "ok",
                          "message": "Explorer session started",
                          "pid": self.process.pid}, quiet)
            return True

        except Exception as e:
            self._print({"status": "error",
                          "message": f"Falha ao iniciar processo: {str(e)}"}, quiet)
            return False

    def _print(self, obj, quiet=False):
        if not quiet:
            print(json.dumps(obj, ensure_ascii=False))

    def _drain_startup_lines(self):
        """Drain non-JSON lines that were emitted during startup."""
        while True:
            try:
                line = self._line_queue.get_nowait()
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                    # Unexpected JSON during startup — put it back
                    # (this shouldn't happen, but just in case)
                    _print_stderr(f"Unexpected JSON during drain: {line[:100]}")
                except json.JSONDecodeError:
                    _print_stderr(f"Drained non-JSON startup line: {line[:100]}")
            except queue.Empty:
                break

    def send_command(self, action, **kwargs):
        """Envia comando JSON para o processo e retorna a resposta."""
        if not self.process or self.process.poll() is not None:
            return {"status": "error", "message": "Nenhuma sessao ativa."}

        cmd = {"action": action}
        cmd.update({k: v for k, v in kwargs.items() if v is not None})

        try:
            cmd_json = json.dumps(cmd) + "\n"
            self.process.stdin.write(cmd_json)
            self.process.stdin.flush()

            # Read JSON response from queue, skipping non-JSON lines
            return self._read_json_response(DEFAULT_READ_TIMEOUT)

        except Exception as e:
            return {"status": "error", "message": f"Erro ao enviar comando: {str(e)}"}

    def _read_json_response(self, timeout_s):
        """Read lines from queue until a valid JSON line is found."""
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                line = self._line_queue.get(timeout=remaining)
            except queue.Empty:
                return {"status": "error", "message": f"Timeout ({timeout_s}s) aguardando resposta"}

            line = line.strip()
            if not line:
                continue

            try:
                return json.loads(line)
            except json.JSONDecodeError:
                _print_stderr(f"Skipped non-JSON: {line[:200]}")
                continue

        return {"status": "error", "message": f"Timeout ({timeout_s}s) sem resposta JSON valida"}

    def stop(self):
        """Encerra a sessao do explorer."""
        self._reader_alive = False
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(json.dumps({"action": "quit"}) + "\n")
                self.process.stdin.flush()
                self.process.wait(timeout=15)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                except Exception:
                    pass

        if PID_FILE.exists():
            PID_FILE.unlink()

        self.process = None
        print(json.dumps({"status": "ok", "message": "Explorer session stopped"}))


def _print_stderr(msg):
    print(f"[explorer_runner] {msg}", file=sys.stderr)


def kill_stale_session():
    """Mata sessao orfã pelo PID file."""
    if not PID_FILE.exists():
        return
    try:
        session_info = json.loads(PID_FILE.read_text(encoding="utf-8"))
        pid = session_info.get("pid")
        if pid:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 9)
    except Exception:
        pass
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()


def check_session_alive():
    """Verifica se existe sessao ativa pelo PID file."""
    if not PID_FILE.exists():
        return None
    try:
        session_info = json.loads(PID_FILE.read_text(encoding="utf-8"))
        pid = session_info.get("pid")
        if not pid:
            return None
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            if str(pid) not in result.stdout:
                PID_FILE.unlink()
                return None
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                PID_FILE.unlink()
                return None
        return session_info
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Explorer Runner - Teste Exploratorio")
    subparsers = parser.add_subparsers(dest="mode", help="Modo de operacao")

    subparsers.add_parser("start", help="Iniciar sessao do explorer")
    subparsers.add_parser("stop", help="Encerrar sessao do explorer")
    subparsers.add_parser("status", help="Verificar status da sessao")
    subparsers.add_parser("multi", help="Modo multi-command: le comandos JSON do stdin")

    cmd_parser = subparsers.add_parser("cmd", help="Enviar comando para a sessao")
    cmd_parser.add_argument("action", help="Acao: login, navigate, screenshot, inspect, click, fill, select, grid-read, validate")
    cmd_parser.add_argument("--empresa", help="Empresa para login")
    cmd_parser.add_argument("--estabelecimento", help="Estabelecimento para login")
    cmd_parser.add_argument("--agrupamento", help="Agrupamento para login")
    cmd_parser.add_argument("--modulo", help="Modulo para login")
    cmd_parser.add_argument("--menu-path", dest="menuPath", help="Caminho do menu")
    cmd_parser.add_argument("--output", help="Caminho de saida (screenshot)")
    cmd_parser.add_argument("--selector", help="CSS selector do elemento")
    cmd_parser.add_argument("--text", help="Texto do elemento para click")
    cmd_parser.add_argument("--label", help="Label do campo para fill")
    cmd_parser.add_argument("--value", help="Valor para preencher")
    cmd_parser.add_argument("--index", type=int, help="Indice do elemento")

    args = parser.parse_args()

    if not args.mode:
        parser.print_help()
        sys.exit(1)

    session = ExplorerSession()

    if args.mode == "start":
        kill_stale_session()
        session.start()

    elif args.mode == "stop":
        info = check_session_alive()
        if info:
            pid = info.get("pid")
            if pid:
                try:
                    if IS_WINDOWS:
                        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                                       capture_output=True, timeout=10)
                    else:
                        os.kill(pid, 9)
                except Exception:
                    pass
            if PID_FILE.exists():
                PID_FILE.unlink()
            print(json.dumps({"status": "ok", "message": f"Session PID {pid} terminated"}))
        else:
            print(json.dumps({"status": "ok", "message": "No active session found"}))

    elif args.mode == "status":
        info = check_session_alive()
        if info:
            print(json.dumps({"status": "ok", "session": info, "message": "Session active"}))
        else:
            print(json.dumps({"status": "ok", "message": "No active session"}))

    elif args.mode == "cmd":
        # Each 'cmd' invocation starts a fresh process (can't reconnect to
        # another process's stdin/stdout across Python invocations)
        kill_stale_session()

        if not session.start(quiet=True):
            print(json.dumps({"status": "error", "message": "Falha ao iniciar sessao do explorer"}))
            sys.exit(1)

        kwargs = {}
        for key in ["empresa", "estabelecimento", "agrupamento", "modulo",
                     "menuPath", "output", "selector", "text", "label",
                     "value", "index"]:
            val = getattr(args, key, None)
            if val is not None:
                kwargs[key] = val

        result = session.send_command(args.action, **kwargs)
        print(json.dumps(result, ensure_ascii=False))

        if args.action == "quit":
            session.stop()

    elif args.mode == "multi":
        kill_stale_session()
        if not session.start(quiet=True):
            print(json.dumps({"status": "error", "message": "Falha ao iniciar sessao"}))
            sys.exit(1)

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"status": "error", "message": f"JSON invalido: {line[:100]}"}))
                continue

            action = cmd.pop("action", None)
            if not action:
                print(json.dumps({"status": "error", "message": "Campo 'action' obrigatorio"}))
                continue

            result = session.send_command(action, **cmd)
            print(json.dumps(result, ensure_ascii=False))
            sys.stdout.flush()

            if action == "quit":
                break

        session.stop()


if __name__ == "__main__":
    main()
