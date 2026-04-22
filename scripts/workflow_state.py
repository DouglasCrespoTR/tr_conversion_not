#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Workflow State Manager - Estado persistente do pipeline dev-to-QA (14 fases).

Gerencia o progresso de uma WI atraves das fases do pipeline, com
persistencia em disco para suportar retomada (resume) e rastreabilidade.

State file: tests/{WI_ID}/workflow_state.json

Uso:
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --show
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --start P1
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --complete P1
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --complete P1 --meta verdict=SCOPE_CLEAR
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --fail P3 --error "Compilation failed"
  python workflow_state.py --wi-id 1078744 --pipeline taxone-auto-fix --skip P9 --reason "No suite configured"
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

VERSION = "1.0.0"

VALID_PHASES = [f"P{i}" for i in range(1, 15)]
VALID_STATUSES = ("PENDING", "IN_PROGRESS", "COMPLETE", "FAILED", "SKIPPED")
VALID_PIPELINES = ("taxone-auto-fix", "taxone-us-implement", "taxone-bug-fix")


class WorkflowState:
    """Persistent state manager for the 14-phase dev-to-QA pipeline."""

    def __init__(self, wi_id, pipeline_name, wi_title=""):
        """Load existing state or create new one.

        Args:
            wi_id: Work Item ID (e.g. "1078744")
            pipeline_name: One of taxone-auto-fix, taxone-us-implement, taxone-bug-fix
            wi_title: Optional WI title for reference
        """
        self.wi_id = str(wi_id)
        self.pipeline_name = pipeline_name
        self.state_dir = PROJECT_ROOT / "tests" / self.wi_id
        self.state_file = self.state_dir / "workflow_state.json"

        if pipeline_name not in VALID_PIPELINES:
            print(f"AVISO: pipeline '{pipeline_name}' nao reconhecido. "
                  f"Validos: {', '.join(VALID_PIPELINES)}", file=sys.stderr)

        if self.state_file.exists():
            self._load()
            print(f"[WorkflowState] Resuming WI {self.wi_id} — "
                  f"current phase: {self.data['current_phase']}, "
                  f"pipeline: {self.data['pipeline']}")
        else:
            self._create(wi_title)
            print(f"[WorkflowState] Created new state for WI {self.wi_id} — "
                  f"pipeline: {pipeline_name}")

    def _create(self, wi_title):
        """Initialize fresh state."""
        now = _now()
        self.data = {
            "version": VERSION,
            "wi_id": self.wi_id,
            "wi_title": wi_title,
            "pipeline": self.pipeline_name,
            "started_at": now,
            "updated_at": now,
            "current_phase": "P1",
            "current_env": "LOCAL",
            "phases": {},
            "loop_count": 0,
            "max_loops": 3,
            "environments_validated": [],
            "errors": [],
        }
        for pid in VALID_PHASES:
            self.data["phases"][pid] = {"status": "PENDING"}
        self.save()

    def _load(self):
        """Load state from disk."""
        raw = self.state_file.read_text(encoding="utf-8")
        self.data = json.loads(raw)

        # Ensure all phases exist (forward compatibility)
        for pid in VALID_PHASES:
            if pid not in self.data.get("phases", {}):
                self.data.setdefault("phases", {})[pid] = {"status": "PENDING"}

    # ----- Phase transitions -----

    def start_phase(self, phase_id):
        """Mark a phase as IN_PROGRESS.

        Args:
            phase_id: Phase ID (e.g. "P1")
        """
        _validate_phase(phase_id)
        phase = self.data["phases"][phase_id]
        phase["status"] = "IN_PROGRESS"
        phase["started_at"] = _now()
        self.data["current_phase"] = phase_id
        self.data["updated_at"] = _now()
        self.save()

    def complete_phase(self, phase_id, **metadata):
        """Mark a phase as COMPLETE with optional metadata.

        Args:
            phase_id: Phase ID (e.g. "P1")
            **metadata: Arbitrary key-value pairs stored in the phase dict
        """
        _validate_phase(phase_id)
        phase = self.data["phases"][phase_id]
        phase["status"] = "COMPLETE"
        phase["completed_at"] = _now()
        for k, v in metadata.items():
            phase[k] = v
        self.data["updated_at"] = _now()
        self.save()

    def fail_phase(self, phase_id, error_msg):
        """Mark a phase as FAILED and record the error.

        Args:
            phase_id: Phase ID (e.g. "P1")
            error_msg: Description of the failure
        """
        _validate_phase(phase_id)
        phase = self.data["phases"][phase_id]
        phase["status"] = "FAILED"
        phase["failed_at"] = _now()
        phase["error"] = error_msg
        self.data["errors"].append({
            "phase": phase_id,
            "error": error_msg,
            "timestamp": _now(),
        })
        self.data["updated_at"] = _now()
        self.save()

    def skip_phase(self, phase_id, reason):
        """Mark a phase as SKIPPED.

        Args:
            phase_id: Phase ID (e.g. "P1")
            reason: Why the phase was skipped
        """
        _validate_phase(phase_id)
        phase = self.data["phases"][phase_id]
        phase["status"] = "SKIPPED"
        phase["skipped_at"] = _now()
        phase["skip_reason"] = reason
        self.data["updated_at"] = _now()
        self.save()

    # ----- Queries -----

    def get_phase(self, phase_id):
        """Get phase status dict.

        Args:
            phase_id: Phase ID (e.g. "P1")

        Returns:
            dict with at least {"status": "..."}
        """
        _validate_phase(phase_id)
        return dict(self.data["phases"][phase_id])

    def get_current_phase(self):
        """Get the phase currently IN_PROGRESS, or current_phase field.

        Returns:
            str phase ID (e.g. "P7")
        """
        for pid in VALID_PHASES:
            if self.data["phases"][pid]["status"] == "IN_PROGRESS":
                return pid
        return self.data.get("current_phase", "P1")

    def get_next_pending(self):
        """Get the next PENDING phase in order.

        Returns:
            str phase ID or None if no pending phases remain
        """
        for pid in VALID_PHASES:
            if self.data["phases"][pid]["status"] == "PENDING":
                return pid
        return None

    # ----- Loop control -----

    def increment_loop(self):
        """Increment loop_count and return new value.

        Returns:
            int new loop_count
        """
        self.data["loop_count"] = self.data.get("loop_count", 0) + 1
        self.data["updated_at"] = _now()
        self.save()
        return self.data["loop_count"]

    def should_escalate(self):
        """Check if loop_count >= max_loops (escalation needed).

        Returns:
            bool True if should escalate
        """
        return self.data.get("loop_count", 0) >= self.data.get("max_loops", 3)

    # ----- Environment tracking -----

    def add_env_validated(self, env):
        """Add an environment to the validated list.

        Args:
            env: Environment name (e.g. "LOCAL", "AC", "RC")
        """
        envs = self.data.setdefault("environments_validated", [])
        if env not in envs:
            envs.append(env)
        self.data["updated_at"] = _now()
        self.save()

    # ----- Persistence -----

    def save(self):
        """Persist state to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ----- Display -----

    def to_summary(self):
        """Human-readable progress summary.

        Returns:
            str multiline summary
        """
        lines = []
        lines.append(f"=== Workflow State: WI {self.data['wi_id']} ===")
        if self.data.get("wi_title"):
            lines.append(f"Title: {self.data['wi_title']}")
        lines.append(f"Pipeline: {self.data['pipeline']}")
        lines.append(f"Started: {self.data['started_at']}")
        lines.append(f"Updated: {self.data['updated_at']}")
        lines.append(f"Current: {self.data['current_phase']} | "
                      f"Env: {self.data.get('current_env', 'LOCAL')}")
        lines.append(f"Loops: {self.data.get('loop_count', 0)}/{self.data.get('max_loops', 3)}")

        envs = self.data.get("environments_validated", [])
        if envs:
            lines.append(f"Envs validated: {', '.join(envs)}")

        lines.append("")
        lines.append("Phases:")

        status_icons = {
            "COMPLETE": "[OK]",
            "IN_PROGRESS": "[>>]",
            "FAILED": "[!!]",
            "SKIPPED": "[--]",
            "PENDING": "[  ]",
        }

        for pid in VALID_PHASES:
            phase = self.data["phases"].get(pid, {"status": "PENDING"})
            st = phase["status"]
            icon = status_icons.get(st, "[??]")
            extra = ""
            if st == "COMPLETE":
                # Show select metadata keys
                meta_keys = [k for k in phase if k not in
                             ("status", "started_at", "completed_at")]
                if meta_keys:
                    parts = [f"{k}={phase[k]}" for k in meta_keys]
                    extra = f"  ({', '.join(parts)})"
            elif st == "FAILED":
                extra = f"  ERROR: {phase.get('error', '?')}"
            elif st == "SKIPPED":
                extra = f"  reason: {phase.get('skip_reason', '?')}"

            lines.append(f"  {pid:>3} {icon} {st}{extra}")

        errors = self.data.get("errors", [])
        if errors:
            lines.append("")
            lines.append(f"Errors ({len(errors)}):")
            for e in errors[-5:]:  # show last 5
                lines.append(f"  {e['phase']} @ {e['timestamp']}: {e['error']}")

        return "\n".join(lines)


# ----- Helpers -----

def _now():
    """Current timestamp as ISO string (no timezone)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _validate_phase(phase_id):
    """Raise ValueError if phase_id is invalid."""
    if phase_id not in VALID_PHASES:
        raise ValueError(
            f"Invalid phase '{phase_id}'. Valid: {', '.join(VALID_PHASES)}"
        )


# ----- CLI -----

def main():
    parser = argparse.ArgumentParser(
        description="Workflow State Manager — persistent pipeline state for WIs"
    )
    parser.add_argument("--wi-id", required=True, help="Work Item ID")
    parser.add_argument("--pipeline", required=True,
                        help=f"Pipeline name: {', '.join(VALID_PIPELINES)}")
    parser.add_argument("--title", default="", help="WI title (used on creation)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="Show current state")
    group.add_argument("--start", metavar="PHASE", help="Start a phase (e.g. P1)")
    group.add_argument("--complete", metavar="PHASE", help="Complete a phase (e.g. P1)")
    group.add_argument("--fail", metavar="PHASE", help="Fail a phase (e.g. P3)")
    group.add_argument("--skip", metavar="PHASE", help="Skip a phase (e.g. P9)")

    parser.add_argument("--meta", nargs="*", default=[],
                        help="Metadata key=value pairs (for --complete)")
    parser.add_argument("--error", default="", help="Error message (for --fail)")
    parser.add_argument("--reason", default="", help="Skip reason (for --skip)")

    args = parser.parse_args()

    ws = WorkflowState(args.wi_id, args.pipeline, wi_title=args.title)

    if args.show:
        print(ws.to_summary())

    elif args.start:
        ws.start_phase(args.start)
        print(f"Phase {args.start} -> IN_PROGRESS")

    elif args.complete:
        metadata = {}
        for item in args.meta:
            if "=" in item:
                k, v = item.split("=", 1)
                metadata[k] = v
            else:
                print(f"AVISO: ignorando meta sem '=': {item}", file=sys.stderr)
        ws.complete_phase(args.complete, **metadata)
        print(f"Phase {args.complete} -> COMPLETE")

    elif args.fail:
        if not args.error:
            print("ERRO: --error obrigatorio com --fail", file=sys.stderr)
            sys.exit(1)
        ws.fail_phase(args.fail, args.error)
        print(f"Phase {args.fail} -> FAILED")

    elif args.skip:
        if not args.reason:
            print("ERRO: --reason obrigatorio com --skip", file=sys.stderr)
            sys.exit(1)
        ws.skip_phase(args.skip, args.reason)
        print(f"Phase {args.skip} -> SKIPPED")


if __name__ == "__main__":
    main()
