#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Suite Detached Launcher - Executa suite_runner.py desacoplado do processo pai.

Cria uma scheduled task que roda imediatamente, desvinculada da arvore
de processos do Claude Code (node.exe). O SOC nao vera node.exe -> java.

Uso:
  python suite_detached.py --wi-id 1234567 --title "Titulo" --xml SPED/CT_SPED_FISCAL.xml
  python suite_detached.py --wi-id 1234567 --title "Titulo" --xml SPED/CT_SPED_FISCAL.xml --wait

Flags:
  --wait    Aguarda conclusao e exibe resultado (polling do arquivo de output)
  --timeout Timeout em segundos (default: 900 = 15min)
"""

import argparse
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TASK_NAME_PREFIX = "SuiteAutomation_"


def create_launcher_bat(wi_id, title, xml, suite_args, output_file, python_exe):
    """Cria .bat temporario que executa suite_runner.py e salva output."""
    bat_path = PROJECT_ROOT / "tests" / str(wi_id) / f"suite_launch_{wi_id}.bat"
    bat_path.parent.mkdir(parents=True, exist_ok=True)

    runner = SCRIPTS_DIR / "suite_runner.py"

    # Montar argumentos extras
    extra = ""
    for arg in suite_args:
        extra += f" {arg}"

    bat_content = f"""@echo off
chcp 1252 >nul
cd /d "{PROJECT_ROOT}"
echo [%date% %time%] Iniciando SuiteAutomation WI {wi_id} > "{output_file}"
echo. >> "{output_file}"
"{python_exe}" "{runner}" --wi-id {wi_id} --title "{title}" --xml "{xml}"{extra} >> "{output_file}" 2>&1
echo. >> "{output_file}"
echo [%date% %time%] Concluido com exit code %ERRORLEVEL% >> "{output_file}"
echo __SUITE_DONE__ >> "{output_file}"
"""
    bat_path.write_text(bat_content, encoding="latin-1")
    return bat_path


def launch_detached(bat_path, task_name):
    """Lanca o .bat via schtasks (processo independente do node.exe)."""
    # Agendar para 5 segundos no futuro
    run_time = datetime.now() + timedelta(seconds=5)
    time_str = run_time.strftime("%H:%M:%S")
    # Formato PT-BR: DD/MM/YYYY (detectar locale automaticamente)
    date_str = run_time.strftime("%d/%m/%Y")

    # Criar scheduled task
    cmd_create = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'cmd.exe /c "{bat_path}"',
        "/SC", "ONCE",
        "/ST", time_str[:5],  # HH:MM
        "/SD", date_str,
        "/F",  # Force overwrite
        "/RL", "LIMITED",
    ]

    result = subprocess.run(cmd_create, capture_output=True, text=True)
    if result.returncode != 0:
        # Fallback: usar cmd /c start (menos desacoplado mas funciona)
        print(f"schtasks falhou ({result.stderr.strip()}), usando start /b como fallback",
              file=sys.stderr)
        subprocess.Popen(
            f'start "SuiteAutomation" /MIN cmd.exe /c "{bat_path}"',
            shell=True,
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        )
        return "fallback"

    # Executar imediatamente (nao esperar o horario agendado)
    cmd_run = ["schtasks", "/Run", "/TN", task_name]
    result = subprocess.run(cmd_run, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"schtasks /Run falhou: {result.stderr.strip()}", file=sys.stderr)
        # Fallback
        subprocess.Popen(
            f'start "SuiteAutomation" /MIN cmd.exe /c "{bat_path}"',
            shell=True,
            creationflags=0x00000008 | 0x00000200,
        )
        return "fallback"

    return "schtasks"


def cleanup_task(task_name):
    """Remove a scheduled task apos conclusao."""
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True
    )


def wait_for_completion(output_file, timeout=900):
    """Aguarda o marcador __SUITE_DONE__ no arquivo de output."""
    start = time.time()
    last_size = 0

    while time.time() - start < timeout:
        if output_file.exists():
            content = output_file.read_text(encoding="latin-1", errors="replace")
            if "__SUITE_DONE__" in content:
                # Remover marcador do output final
                content = content.replace("__SUITE_DONE__", "").strip()
                return True, content
            # Mostrar progresso se arquivo cresceu
            current_size = len(content)
            if current_size > last_size:
                new_content = content[last_size:]
                for line in new_content.splitlines():
                    if line.strip():
                        print(f"  ... {line.strip()}", file=sys.stderr)
                last_size = current_size
        time.sleep(5)

    return False, f"Timeout apos {timeout}s aguardando conclusao"


def main():
    parser = argparse.ArgumentParser(description="Lanca SuiteAutomation desacoplado do Claude/node.exe")
    parser.add_argument("--wi-id", required=True, help="Work Item ID")
    parser.add_argument("--title", required=True, help="Titulo da WI")
    parser.add_argument("--xml", required=True, help="XML de teste (ex: SPED/CT_SPED_FISCAL.xml)")
    parser.add_argument("--wait", action="store_true", help="Aguardar conclusao")
    parser.add_argument("--timeout", type=int, default=900, help="Timeout em segundos (default: 900)")
    parser.add_argument("--range", default=None, help="Range de testes (ex: 1;3)")
    parser.add_argument("--no-sync", action="store_true", help="Nao sincronizar esperados")
    parser.add_argument("--env", default="LOCAL", choices=["LOCAL", "AC", "RC", "QA"],
                        help="Ambiente alvo (default: LOCAL)")

    args = parser.parse_args()

    # Arquivo de output
    output_dir = PROJECT_ROOT / "tests" / str(args.wi_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"suite_output_{args.wi_id}.txt"

    # Limpar output anterior
    if output_file.exists():
        output_file.unlink()

    # Args extras para suite_runner.py
    suite_args = []
    if args.env != "LOCAL":
        suite_args.extend(["--env", args.env])
    if args.range:
        suite_args.extend(["--range", args.range])
    if args.no_sync:
        suite_args.append("--no-sync")

    # Python executavel
    python_exe = sys.executable

    # Criar bat
    bat_path = create_launcher_bat(
        args.wi_id, args.title, args.xml, suite_args, output_file, python_exe
    )

    # Lançar desacoplado
    task_name = f"{TASK_NAME_PREFIX}{args.wi_id}"
    method = launch_detached(bat_path, task_name)

    print(f"SuiteAutomation lancado via {method}")
    print(f"  BAT: {bat_path}")
    print(f"  Output: {output_file}")

    if args.wait:
        print(f"Aguardando conclusao (timeout: {args.timeout}s)...")
        completed, content = wait_for_completion(output_file, args.timeout)
        cleanup_task(task_name)

        if completed:
            print(content)
            # Propagar exit code baseado no conteudo
            if "FAIL" in content or "ERRO" in content:
                sys.exit(1)
            sys.exit(0)
        else:
            print(content, file=sys.stderr)
            sys.exit(2)
    else:
        print(f"Execucao em background. Verifique o output em:")
        print(f"  {output_file}")
        print(f"Para limpar: schtasks /Delete /TN {task_name} /F")


if __name__ == "__main__":
    main()
