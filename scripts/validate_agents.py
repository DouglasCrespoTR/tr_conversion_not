#!/usr/bin/env python3
"""
Validate Agents - Verifica integridade estrutural dos agentes Claude Code.

Checks:
  1. Agent frontmatter (name, description, model, tools)
  2. Command frontmatter (allowed-tools, description)
  3. Referenced knowledge files exist
  4. Referenced scripts exist
  5. Cross-references between agents/commands are valid
  6. Model values are consistent (inherit vs hardcoded)
  7. .env.example has all referenced env vars

Usage:
  python scripts/validate_agents.py          # Run all checks
  python scripts/validate_agents.py --fix    # Auto-fix trivial issues (model: sonnet -> inherit)
  python scripts/validate_agents.py --json   # Output as JSON
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# --- Configuration ---

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
COMMANDS_DIR = PROJECT_ROOT / ".claude" / "commands"
KNOWLEDGE_DIR = PROJECT_ROOT / ".claude" / "knowledge"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

VALID_MODELS = {"inherit", "sonnet", "opus", "haiku"}
VALID_TOOLS = {
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    "Agent", "AskUserQuestion",
}

# --- Result tracking ---

class ValidationResult:
    def __init__(self):
        self.errors = []    # Must fix
        self.warnings = []  # Should fix
        self.info = []      # FYI
        self.stats = {}

    def error(self, source, msg):
        self.errors.append({"source": source, "message": msg})

    def warn(self, source, msg):
        self.warnings.append({"source": source, "message": msg})

    def note(self, source, msg):
        self.info.append({"source": source, "message": msg})

    @property
    def ok(self):
        return len(self.errors) == 0


# --- Parsers ---

def parse_frontmatter(filepath: Path) -> dict:
    """Parse YAML-like frontmatter from markdown file."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    fm = {}
    if not text.startswith("---"):
        return fm

    end = text.find("---", 3)
    if end == -1:
        return fm

    block = text[3:end].strip()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Remove quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Parse JSON arrays
        if value.startswith("["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        fm[key] = value

    fm["_body"] = text[end + 3:].strip()
    fm["_path"] = str(filepath)
    return fm


def extract_references(text: str) -> dict:
    """Extract knowledge and script references from agent body."""
    refs = {
        "knowledge_files": set(),
        "scripts": set(),
        "env_vars": set(),
        "agent_refs": set(),
    }

    # Knowledge references: knowledge/{path}
    for m in re.finditer(r'knowledge/([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)', text):
        refs["knowledge_files"].add(m.group(0))

    # Script references: scripts/{name}.py or scripts/{name}.sh
    for m in re.finditer(r'scripts/([a-zA-Z0-9_\-]+\.(?:py|sh))', text):
        refs["scripts"].add(m.group(1))

    # Env var references: $VARNAME or ${VARNAME} or "$VARNAME"
    for m in re.finditer(r'\$\{?([A-Z][A-Z0-9_]+)\}?', text):
        var = m.group(1)
        # Skip CLAUDE_PLUGIN_ROOT (internal) and common shell vars
        if var not in ("CLAUDE_PLUGIN_ROOT", "HOME", "PATH", "PWD", "USER"):
            refs["env_vars"].add(var)

    # Agent references in commands: subagent_type or agent name
    for m in re.finditer(r'taxone-[a-z0-9\-]+', text):
        refs["agent_refs"].add(m.group(0))

    return refs


# --- Validators ---

def validate_agents(result: ValidationResult):
    """Validate all agent definition files."""
    if not AGENTS_DIR.exists():
        result.error("agents/", "Directory .claude/agents/ not found")
        return

    agents = sorted(AGENTS_DIR.glob("*.md"))
    result.stats["agent_count"] = len(agents)
    agent_names = set()

    for agent_file in agents:
        name = agent_file.stem
        agent_names.add(name)
        fm = parse_frontmatter(agent_file)

        # Required frontmatter fields
        if not fm.get("name"):
            result.error(name, "Missing 'name' in frontmatter")
        elif fm["name"] != name:
            result.warn(name, f"Frontmatter name '{fm['name']}' != filename '{name}'")

        if not fm.get("description"):
            result.error(name, "Missing 'description' in frontmatter")

        # Model validation
        model = fm.get("model", "")
        if not model:
            result.error(name, "Missing 'model' in frontmatter")
        elif model not in VALID_MODELS:
            result.error(name, f"Invalid model '{model}' (valid: {VALID_MODELS})")
        elif model != "inherit":
            result.warn(name, f"Uses model '{model}' instead of 'inherit'")

        # Tools validation
        tools = fm.get("tools", [])
        if not tools:
            result.warn(name, "No 'tools' defined in frontmatter")
        elif isinstance(tools, list):
            for tool in tools:
                if tool not in VALID_TOOLS:
                    result.warn(name, f"Unknown tool '{tool}' in frontmatter")

        # Body references
        body = fm.get("_body", "")
        refs = extract_references(body)

        # Check knowledge file references
        for kf in refs["knowledge_files"]:
            kpath = PROJECT_ROOT / ".claude" / kf
            if not kpath.exists():
                result.warn(name, f"Referenced knowledge file missing: {kf}")

        # Check script references
        for script in refs["scripts"]:
            spath = SCRIPTS_DIR / script
            if not spath.exists():
                result.error(name, f"Referenced script missing: scripts/{script}")

    result.stats["agent_names"] = sorted(agent_names)


def validate_commands(result: ValidationResult):
    """Validate all command definition files."""
    if not COMMANDS_DIR.exists():
        result.error("commands/", "Directory .claude/commands/ not found")
        return

    commands = sorted(COMMANDS_DIR.glob("*.md"))
    result.stats["command_count"] = len(commands)

    for cmd_file in commands:
        name = cmd_file.stem
        fm = parse_frontmatter(cmd_file)

        if not fm.get("description"):
            result.warn(f"cmd:{name}", "Missing 'description' in frontmatter")

        # Check agent references in body
        body = fm.get("_body", "")
        refs = extract_references(body)
        agent_names = result.stats.get("agent_names", [])

        command_names = {f.stem for f in COMMANDS_DIR.glob("*.md")} if COMMANDS_DIR.exists() else set()
        for ref in refs["agent_refs"]:
            if ref in agent_names or ref in command_names or ref == name:
                continue
            if ref.startswith("taxone-"):
                result.note(f"cmd:{name}", f"References '{ref}' (not an agent or command file)")


def validate_knowledge(result: ValidationResult):
    """Validate knowledge directory structure."""
    if not KNOWLEDGE_DIR.exists():
        result.error("knowledge/", "Directory .claude/knowledge/ not found (copy from taxone-support-dev)")
        return

    subdirs = sorted([d.name for d in KNOWLEDGE_DIR.iterdir() if d.is_dir()])
    result.stats["knowledge_dirs"] = subdirs

    expected_dirs = [
        "architecture", "conventions", "schema", "business-rules",
        "ado-fixes", "webhelp", "zendesk-patterns", "rule-extractor",
    ]
    for d in expected_dirs:
        if d not in subdirs:
            result.warn("knowledge/", f"Expected subdirectory missing: {d}")

    # Count files
    total = sum(1 for _ in KNOWLEDGE_DIR.rglob("*") if _.is_file())
    result.stats["knowledge_files"] = total


def validate_scripts(result: ValidationResult):
    """Validate scripts directory."""
    if not SCRIPTS_DIR.exists():
        result.error("scripts/", "Directory scripts/ not found")
        return

    scripts = sorted([f.name for f in SCRIPTS_DIR.iterdir()
                      if f.is_file() and f.suffix in (".py", ".sh")])
    result.stats["script_count"] = len(scripts)
    result.stats["scripts"] = scripts

    # Check each .py has valid syntax
    for script_name in scripts:
        if not script_name.endswith(".py"):
            continue
        script_path = SCRIPTS_DIR / script_name
        try:
            source = script_path.read_text(encoding="utf-8", errors="replace")
            compile(source, script_name, "exec")
        except SyntaxError as e:
            result.error(f"scripts/{script_name}", f"Python syntax error: {e.msg} (line {e.lineno})")

    # Check for hardcoded secrets in scripts
    secret_patterns = [
        (re.compile(r'password\s*=\s*"[^"]{3,}"', re.I), "hardcoded password"),
        (re.compile(r"password\s*=\s*'[^']{3,}'", re.I), "hardcoded password"),
        (re.compile(r'api_key\s*=\s*"[^"]{3,}"', re.I), "hardcoded API key"),
        (re.compile(r"api_key\s*=\s*'[^']{3,}'", re.I), "hardcoded API key"),
        (re.compile(r'token\s*=\s*"[A-Za-z0-9+/=_-]{20,}"'), "hardcoded token"),
        (re.compile(r"token\s*=\s*'[A-Za-z0-9+/=_-]{20,}'"), "hardcoded token"),
        (re.compile(r'PRIVATE KEY-----'), "private key"),
    ]
    # Files that legitimately reference password as variable names (not values)
    skip_scripts = {"validate_agents.py", "pre-commit-secrets.sh"}
    for script_name in scripts:
        if script_name in skip_scripts:
            continue
        script_path = SCRIPTS_DIR / script_name
        try:
            source = script_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(source.splitlines(), 1):
            # Skip comments and lines reading from env/config (not hardcoded)
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(safe in line for safe in [
                "environ.get", "config[", "cfg[", "env_values",
                ".get(", "getenv", "_get_env", "split(",
            ]):
                continue
            for pattern, desc in secret_patterns:
                if pattern.search(line):
                    result.error(
                        f"scripts/{script_name}:{lineno}",
                        f"Possible {desc} — use environment variables instead"
                    )


def validate_env(result: ValidationResult):
    """Validate .env.example completeness."""
    if not ENV_EXAMPLE.exists():
        result.error(".env.example", "File not found")
        return

    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    defined_vars = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            defined_vars.add(key)

    result.stats["env_vars_defined"] = sorted(defined_vars)

    # Check that key vars are present
    expected = ["TAXONE_DW_REPO", "QA_REPO", "ADO_PAT"]
    for var in expected:
        if var not in defined_vars:
            result.error(".env.example", f"Missing required variable: {var}")


def validate_cross_refs(result: ValidationResult):
    """Validate cross-references between components."""
    agent_names = set(result.stats.get("agent_names", []))
    command_names = {f.stem for f in COMMANDS_DIR.glob("*.md")} if COMMANDS_DIR.exists() else set()

    # Check that CLAUDE.md structure section matches reality
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        # Count agents mentioned
        mentioned = set(re.findall(r'taxone-[a-z0-9\-]+\.md', text))
        mentioned_names = {m.replace('.md', '') for m in mentioned}
        for mn in mentioned_names:
            if mn not in agent_names and mn not in command_names:
                result.note("CLAUDE.md", f"Mentions '{mn}' that doesn't exist as agent or command")


# --- Output ---

def print_report(result: ValidationResult, as_json: bool = False):
    """Print validation report."""
    if as_json:
        print(json.dumps({
            "ok": result.ok,
            "errors": result.errors,
            "warnings": result.warnings,
            "info": result.info,
            "stats": {k: v for k, v in result.stats.items()
                      if k not in ("agent_names", "scripts")},
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("  Agent Validation Report")
    print("=" * 60)

    # Stats
    print(f"\nAgents:    {result.stats.get('agent_count', '?')}")
    print(f"Commands:  {result.stats.get('command_count', '?')}")
    print(f"Scripts:   {result.stats.get('script_count', '?')}")
    print(f"Knowledge: {result.stats.get('knowledge_files', '?')} files "
          f"in {len(result.stats.get('knowledge_dirs', []))} dirs")
    print(f"Env vars:  {len(result.stats.get('env_vars_defined', []))}")

    # Errors
    if result.errors:
        print(f"\n{'ERRORS':=^60}")
        for e in result.errors:
            print(f"  [ERR]  {e['source']}: {e['message']}")
    else:
        print(f"\n  No errors found.")

    # Warnings
    if result.warnings:
        print(f"\n{'WARNINGS':=^60}")
        for w in result.warnings:
            print(f"  [WARN] {w['source']}: {w['message']}")

    # Info
    if result.info:
        print(f"\n{'INFO':=^60}")
        for i in result.info:
            print(f"  [INFO] {i['source']}: {i['message']}")

    # Summary
    print(f"\n{'SUMMARY':=^60}")
    status = "PASS" if result.ok else "FAIL"
    print(f"  Status:   {status}")
    print(f"  Errors:   {len(result.errors)}")
    print(f"  Warnings: {len(result.warnings)}")
    print(f"  Info:     {len(result.info)}")
    print("=" * 60)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Validate Claude Code agents setup")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--fix", action="store_true", help="Auto-fix trivial issues")
    args = parser.parse_args()

    result = ValidationResult()

    # Run all validators
    validate_agents(result)
    validate_commands(result)
    validate_knowledge(result)
    validate_scripts(result)
    validate_env(result)
    validate_cross_refs(result)

    # Auto-fix if requested
    if args.fix:
        fixed = 0
        for agent_file in AGENTS_DIR.glob("*.md"):
            text = agent_file.read_text(encoding="utf-8", errors="replace")
            if "\nmodel: sonnet\n" in text:
                text = text.replace("\nmodel: sonnet\n", "\nmodel: inherit\n")
                agent_file.write_text(text, encoding="utf-8")
                fixed += 1
                print(f"  [FIX] {agent_file.stem}: model sonnet -> inherit")
        if fixed:
            print(f"  Fixed {fixed} file(s). Re-run without --fix to verify.")
            return

    print_report(result, as_json=args.json)
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
