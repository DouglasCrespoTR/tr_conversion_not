#!/usr/bin/env python3
"""
Business Rules Importer - Converte documentos .docx da pasta Documento_Matriz
para Markdown na knowledge/business-rules/.

Fonte: SharePoint/OneDrive "Requisitos - Mastersaf DW TaxOne/Documento_Matriz/"
Destino: knowledge/business-rules/{modulo}/{submodulo-sanitizado}/{arquivo}.md

Uso:
  python scripts/business_rules_importer.py --source "C:/Users/.../Documento_Matriz"
  python scripts/business_rules_importer.py --source "..." --dry-run
  python scripts/business_rules_importer.py --source "..." --stats
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    import mammoth
except ImportError:
    print("ERROR: mammoth not installed. Run: pip install mammoth", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "knowledge" / "business-rules"

# Extensions we can convert
CONVERTIBLE = {".docx"}
# Extensions we skip silently
SKIP_SILENT = {".gitkeep", ".ini", ".lnk", ".css", ".js", ".bmpr",
               ".zip", ".rar", ".7z", ".png", ".jpg", ".jpeg",
               ".gif", ".bmp", ".ppt", ".pptx", ".xsd", ".xml",
               ".baixados", ".old", ".extra", ".msg", ".odt", ".rtf"}
# Extensions we note but skip
SKIP_NOTED = {".doc", ".pdf", ".xls", ".xlsx", ".xlsm", ".txt", ".html"}


def sanitize_filename(name: str) -> str:
    """Normaliza nome de arquivo para path seguro."""
    # NFD → NFC normalization
    name = unicodedata.normalize("NFC", name)
    # Replace problematic chars
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores/spaces
    name = re.sub(r'[_\s]+', '_', name)
    # Remove leading/trailing
    name = name.strip('_. ')
    return name[:200]  # max length


def sanitize_dirname(name: str) -> str:
    """Normaliza nome de diretorio."""
    name = sanitize_filename(name)
    # Keep shorter for dirs
    return name[:100]


def convert_docx_to_md(docx_path: Path) -> str | None:
    """Converte .docx para Markdown usando mammoth."""
    try:
        with open(docx_path, "rb") as f:
            result = mammoth.convert_to_markdown(f)
        md = result.value
        if not md or not md.strip():
            return None

        # Add frontmatter
        stat = docx_path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
        size_kb = stat.st_size / 1024

        header = (
            f"# {docx_path.stem}\n\n"
            f"- **Fonte:** {docx_path.name}\n"
            f"- **Modificado:** {modified}\n"
            f"- **Tamanho:** {size_kb:.0f} KB\n\n"
            f"---\n\n"
        )
        return header + md

    except Exception as e:
        return None


def import_all(source_dir: Path, output_dir: Path, dry_run: bool = False):
    """Importa todos os .docx da source para output como .md."""
    stats = {
        "converted": 0,
        "skipped_empty": 0,
        "skipped_error": 0,
        "skipped_ext": {},
        "total_files": 0,
        "total_size_mb": 0,
    }

    source_dir = Path(source_dir)
    output_dir = Path(output_dir)

    if not source_dir.exists():
        print(f"ERROR: Source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect all files
    all_files = list(source_dir.rglob("*"))
    all_files = [f for f in all_files if f.is_file()]
    stats["total_files"] = len(all_files)

    print(f"Source: {source_dir}")
    print(f"Output: {output_dir}")
    print(f"Total files found: {len(all_files)}")
    print()

    docx_files = [f for f in all_files if f.suffix.lower() in CONVERTIBLE]
    print(f"Convertible (.docx): {len(docx_files)}")

    for ext in SKIP_NOTED:
        count = sum(1 for f in all_files if f.suffix.lower() == ext)
        if count:
            stats["skipped_ext"][ext] = count
            print(f"  Skipped {ext}: {count}")

    print()

    if dry_run:
        print("[DRY RUN] Would convert:")
        for f in docx_files[:20]:
            rel = f.relative_to(source_dir)
            print(f"  {rel}")
        if len(docx_files) > 20:
            print(f"  ... and {len(docx_files) - 20} more")
        return stats

    # Convert
    start = time.time()
    for i, docx_path in enumerate(docx_files, 1):
        rel_path = docx_path.relative_to(source_dir)

        # Build output path: preserve directory structure, change extension
        parts = list(rel_path.parts)
        # Sanitize each part
        sanitized_parts = [sanitize_dirname(p) for p in parts[:-1]]
        sanitized_parts.append(sanitize_filename(rel_path.stem) + ".md")

        out_path = output_dir / Path(*sanitized_parts)

        # Skip if output already exists and is newer
        if out_path.exists():
            out_mtime = out_path.stat().st_mtime
            src_mtime = docx_path.stat().st_mtime
            if out_mtime >= src_mtime:
                continue

        # Convert
        md_content = convert_docx_to_md(docx_path)
        if md_content is None:
            stats["skipped_empty"] += 1
            continue

        if not md_content.strip():
            stats["skipped_empty"] += 1
            continue

        # Write
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md_content, encoding="utf-8")
        stats["converted"] += 1
        stats["total_size_mb"] += len(md_content) / (1024 * 1024)

        if i % 100 == 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  [{i}/{len(docx_files)}] {rate:.0f} files/s ...")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s")
    print(f"  Converted: {stats['converted']}")
    print(f"  Skipped (empty): {stats['skipped_empty']}")
    print(f"  Total MD size: {stats['total_size_mb']:.1f} MB")

    # Save metadata
    meta = {
        "source": str(source_dir),
        "last_import": datetime.now().isoformat(),
        "stats": stats,
    }
    meta_path = output_dir / "_import_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Import business rules documents (docx) to Markdown"
    )
    parser.add_argument(
        "--source", type=str, required=True,
        help="Source directory (Documento_Matriz path)"
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT),
        help=f"Output directory (default: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only, don't write files"
    )

    args = parser.parse_args()
    import_all(Path(args.source), Path(args.output), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
