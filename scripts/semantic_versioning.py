
#!/usr/bin/env python3
"""
- Triggers only when staged Python files exist under catalogs/<sub_folder>/...
- Treat every first-level dir under catalogs/ as a dynamic catalog scope.
- Enforce commit messages composed of semicolon-separated segments like:
      <major|minor|patch|feat|fix|na> sub_folder : file.py, other.py;
"""

import argparse
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Set, Tuple

# ---- LEVEL MAP (kept as requested) ------------------------------------------
LEVEL_MAP: Dict[str, str] = {
    "major": "major",
    "minor": "minor",
    "patch": "patch",
    "feat":  "minor",  # feat == minor
    "fix":   "patch",  # fix  == patch
    "NA":    "NA",     # for things like New lines or spaces
}
# Accept tokens case-insensitively
VALID_LEVEL_TOKENS: Set[str] = {k.lower() for k in LEVEL_MAP.keys()} | {"na"}

CATALOGS_DIR = pathlib.Path("catalogs")

# ---- git helpers -------------------------------------------------------------

def _run(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()

def get_staged_paths() -> List[pathlib.Path]:
    """Return staged paths (index) for A/C/M/R/T changes."""
    try:
        out = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    except subprocess.CalledProcessError:
        return []
    return [pathlib.Path(p) for p in out.splitlines() if p]

# ---- catalog helpers ---------------------------------------------------------

def is_catalog_python(path: pathlib.Path) -> bool:
    """
    True if path matches catalogs/<catalog>/** and endswith .py
    """
    parts = path.as_posix().split("/")
    return len(parts) >= 3 and parts[0] == "catalogs" and path.suffix == ".py"

def catalog_name_for(path: pathlib.Path) -> str:
    """catalogs/<catalog>/... -> <catalog>"""
    return path.as_posix().split("/")[1]

def group_changed_python_by_catalog(staged: List[pathlib.Path]) -> Dict[str, List[pathlib.Path]]:
    groups: Dict[str, List[pathlib.Path]] = {}
    for p in staged:
        if is_catalog_python(p):
            groups.setdefault(catalog_name_for(p), []).append(p)
    return groups

# ---- commit message parsing & validation -------------------------------------

# <level> <catalog> : file1.py, file2.py;
SEGMENT_RE = re.compile(
    r"""
    ^\s*
    (?P<level>major|minor|patch|feat|fix|na)   # level token
    \s+
    (?P<catalog>[A-Za-z0-9._\-]+)              # catalog name
    \s*:\s*
    (?P<files>.+?)                             # comma-separated files
    \s*;?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

def parse_commit_segments(msg_text: str) -> List[Tuple[str, str, List[str]]]:
    """
    Split message by ';' and parse segments.
    Returns list of (level_lower, catalog_exact, [file_basenames_lower]).
    """
    # Normalize whitespace; allow multi-line message content
    compact = " ".join(line.strip() for line in msg_text.splitlines() if line.strip())
    raw_segments = [s for s in (seg.strip() for seg in compact.split(";")) if s]
    parsed: List[Tuple[str, str, List[str]]] = []
    for seg in raw_segments:
        m = SEGMENT_RE.match(seg)
        if not m:
            continue
        level = m.group("level").lower()
        catalog = m.group("catalog")
        files = [f.strip().lower() for f in m.group("files").split(",") if f.strip()]
        parsed.append((level, catalog, files))
    return parsed

def build_guidance(groups: Dict[str, List[pathlib.Path]]) -> str:
    lines = []
    lines.append("Missing semver mapping for the changed catalog.")
    lines.append("Use one of:")
    segments = []
    for catalog in sorted(groups.keys()):
        files = sorted({p.name for p in groups[catalog]})
        files_part = ", ".join(files) if files else ""
        segments.append(f"  <major|minor|patch|feat|fix> {catalog} : {files_part};")
    lines.extend(segments)
    return "\n".join(lines)

def validate_message_for_groups(msg_text: str, groups: Dict[str, List[pathlib.Path]]) -> Tuple[bool, str]:
    """
    Requirements:
      - If Python files changed under catalogs/, the message must include
        at least one valid segment per changed catalog.
      - Token must be in {major,minor,patch,feat,fix,na} (case-insensitive).
      - Every changed file basename for that catalog must be listed in its segment.
    """
    segments = parse_commit_segments(msg_text)
    if not groups:
        return True, ""
    if not segments:
        return False, build_guidance(groups)

    # Build lookup by catalog → listed files (lower)
    listed_by_catalog: Dict[str, Set[str]] = {}
    valid_tokens_all = True

    for level, catalog, files in segments:
        if level.lower() not in VALID_LEVEL_TOKENS:
            valid_tokens_all = False
        listed_by_catalog.setdefault(catalog, set()).update(files)

    if not valid_tokens_all:
        return False, build_guidance(groups)

    # Validate coverage for each changed catalog
    for catalog, paths in groups.items():
        if catalog not in listed_by_catalog:
            return False, build_guidance(groups)
        expected_files = {p.name.lower() for p in paths}
        listed = listed_by_catalog.get(catalog, set())
        if not expected_files.issubset(listed):
            return False, build_guidance(groups)

    return True, ""

# ---- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Catalog commit message guard (no bumping)")
    ap.add_argument("msg_file", nargs="?", help="Path to COMMIT_EDITMSG (commit-msg stage)")
    args = ap.parse_args()

    # Determine if this commit touches any Python files under catalogs/
    staged = get_staged_paths()
    groups = group_changed_python_by_catalog(staged)
    if not groups:
        sys.exit(0)

    # Must have a commit message file (commit-msg stage)
    if not args.msg_file:
        sys.exit(0)

    msg_path = pathlib.Path(args.msg_file)
    if not msg_path.exists():
        print("commit message file not found.", file=sys.stderr)
        sys.exit(1)

    msg_text = msg_path.read_text(encoding="utf-8")
    ok, error = validate_message_for_groups(msg_text, groups)
    if not ok:
        print(error, file=sys.stderr)
        sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
