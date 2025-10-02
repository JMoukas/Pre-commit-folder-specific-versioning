#!/usr/bin/env python3
# Simple two-stage hook script:
# - pre-commit (interactive when in a terminal): if catalog Python files changed, ask major/minor/patch and bump __version__ in each touched catalog's __init__.py
# - commit-msg (message-driven, works in GitHub Desktop): if catalog Python files changed, enforce custom commit schema and bump accordingly

import os, re, sys, subprocess, pathlib

# ====== CONFIG: which folders are catalogs and where their version lives ======
CATALOGS = {
    "catalogs/catalog_alpha": "catalogs/catalog_alpha/__init__.py",
    "catalogs/catalog_beta":  "catalogs/catalog_beta/__init__.py",
    "catalogs/catalog_gamma": "catalogs/catalog_gamma/__init__.py",
}

# Map accepted tokens to normalized semver levels
LEVEL_MAP = {
    "major": "major",
    "minor": "minor",
    "patch": "patch",
    "feat":  "minor",  # feat == minor
    "fix":   "patch",  # fix  == patch
}

# ====== regexes ======
PY              = re.compile(r".*\.py$")
VERSION_LINE    = re.compile(r'^__version__\s*=\s*["\'](\d+)\.(\d+)\.(\d+)["\']\s*$', re.M)

# ====== small helpers ======
def run(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()

def staged_files() -> list[str]:
    out = run("git diff --cached --name-only --diff-filter=ACMR")
    return [x for x in out.splitlines() if x]

def catalogs_with_python_changes(files: list[str]) -> list[tuple[str, str]]:
    touched = []
    for cat_dir, initp in CATALOGS.items():
        if any(f.startswith(cat_dir + "/") and PY.match(f) for f in files):
            touched.append((cat_dir, initp))
    return touched

def read_text(path: str) -> str:
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""

def write_text(path: str, content: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def get_version_from_init(initp: str, default: str = "0.1.0") -> tuple[tuple[int,int,int], str]:
    """
    Ensure __init__.py exists and has __version__="X.Y.Z".
    Return ((maj,min,pat), file_content).
    """
    txt = read_text(initp)
    if not txt:
        txt = f'__version__ = "{default}"\n'
        write_text(initp, txt)
        return (0,1,0), txt
    m = VERSION_LINE.search(txt)
    if not m:
        txt = txt.rstrip() + f'\n__version__ = "{default}"\n'
        write_text(initp, txt)
        return (0,1,0), txt
    return tuple(map(int, m.groups())), txt

def set_version_in_init(initp: str, content: str, version_str: str) -> None:
    if VERSION_LINE.search(content):
        new = VERSION_LINE.sub(f'__version__ = "{version_str}"', content)
    else:
        new = content.rstrip() + f'\n__version__ = "{version_str}"\n'
    write_text(initp, new)
    run(f"git add {initp}")

def bump_tuple(v: tuple[int,int,int], level: str) -> tuple[int,int,int]:
    """
    level normalized to: major|minor|patch
    """
    maj, minr, pat = v
    if level == "major":
        return (maj+1, 0, 0)
    if level == "minor":
        return (maj, minr+1, 0)
    return (maj, minr, pat+1)  # patch default

def tuple_to_str(t: tuple[int,int,int]) -> str:
    return f"{t[0]}.{t[1]}.{t[2]}"

def prompt(text: str, choices: list[str], default: str) -> str:
    if not sys.stdin.isatty():
        return default
    opts = "/".join(choices)
    ans = input(f"{text} [{opts}] (default: {default}): ").strip().lower()
    return ans if ans in choices else default

# ====== custom commit message parsing (your schema) ======
# Accept:
#   Single catalog changed:
#       Global : <level>
#       OR
#       "<catalog_name>" : <level>
#   Multiple catalogs changed (ALL required):
#       "<catalog_a>" : <level> ; "<catalog_b>" : <level> ; ...
#
# <level> in {major, minor, patch, feat, fix} (feat→minor, fix→patch)

def parse_global_level(msg: str) -> str | None:
    m = re.search(r'(?im)^\s*Global\s*:\s*(major|minor|patch|feat|fix)\s*$', msg)
    if m:
        return LEVEL_MAP[m.group(1).lower()]
    return None

def parse_per_catalog_levels(msg: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for cat, lvl in re.findall(r'(?i)"([^"]+)"\s*:\s*(major|minor|patch|feat|fix)', msg):
        result[cat] = LEVEL_MAP[lvl.lower()]
    return result

# ====== pre-commit stage (interactive if terminal) ======
def do_pre_commit() -> None:
    files = staged_files()
    touched = catalogs_with_python_changes(files)
    if not touched:
        sys.exit(0)  # nothing to enforce

    if not sys.stdin.isatty():   # GUI (Desktop) → handled in commit-msg
        sys.exit(0)

    print("python changes detected in:")
    for c,_ in touched:
        print("  -", c)

    same = prompt("apply same bump to all?", ["y","n"], "y")
    chosen = None

    for cat_dir, initp in touched:
        v, content = get_version_from_init(initp)
        cur = tuple_to_str(v)
        print(f"\n{cat_dir} current: {cur}")
        print(f"  major -> ({v[0]}+1).0.0")
        print(f"  minor -> {v[0]}.({v[1]}+1).0")
        print(f"  patch -> {v[0]}.{v[1]}.({v[2]}+1)")
        if same == "y" and chosen is None:
            chosen = prompt("select bump", ["major","minor","feat","patch","fix"], "patch")
        level_token = chosen if same == "y" else prompt("select bump", ["major","minor","feat","patch","fix"], "patch")
        level = LEVEL_MAP.get(level_token, "patch")
        newt = bump_tuple(v, level)
        newv = tuple_to_str(newt)
        print(f"  -> {cur} -> {newv}")
        set_version_in_init(initp, content, newv)

    sys.exit(0)

# ====== commit-msg stage (message-driven, enforces schema) ======
def do_commit_msg(msg_path: str) -> None:
    files = staged_files()
    touched = catalogs_with_python_changes(files)  # [(dir, init.py), ...]
    if not touched:
        sys.exit(0)  # nothing to enforce

    # Set of touched catalog "short names" (e.g., {'catalog_alpha', ...})
    touched_names = {cat_dir.split("/")[-1] for cat_dir, _ in touched}

    # Load commit message
    msg = pathlib.Path(msg_path).read_text(encoding="utf-8")

    # Parse mappings from message
    global_level = parse_global_level(msg)          # 'major'|'minor'|'patch'|None
    per_levels   = parse_per_catalog_levels(msg)    # {'catalog_alpha': 'minor', ...}
    per_level_names = set(per_levels.keys())

    # Resolve required level per touched catalog according to your rules
    levels_for: dict[str, str] = {}

    if len(touched_names) == 1:
        # Single catalog: allow either Global or per-catalog
        only = next(iter(touched_names))
        if only in per_levels:
            levels_for[only] = per_levels[only]
        elif global_level:
            levels_for[only] = global_level
        else:
            print(
                "❌ Missing semver mapping for the changed catalog.\n"
                "Use one of:\n"
                '  Global : <major|minor|patch|feat|fix>\n'
                f'  "{only}" : <major|minor|patch|feat|fix>\n'
            )
            sys.exit(1)
    else:
        # Multiple catalogs: per-catalog entries are REQUIRED for ALL touched catalogs
        missing = sorted(touched_names - per_level_names)
        extraneous = sorted(per_level_names - touched_names)
        if missing:
            print(
                "❌ Multiple catalogs changed. Provide a per-catalog mapping for ALL touched catalogs.\n"
                "Expected entries like:\n  " +
                " ; ".join([f'"{name}" : <major|minor|patch|feat|fix>' for name in sorted(touched_names)]) +
                "\nMissing mappings for: " + ", ".join(missing)
            )
            sys.exit(1)
        if extraneous:
            print("❌ Per-catalog mappings include unknown catalogs: " + ", ".join(extraneous))
            sys.exit(1)
        # Use provided levels
        levels_for = {name: per_levels[name] for name in touched_names}

    # Apply bumps
    bumped_names = []
    for cat_dir, initp in touched:
        name = cat_dir.split("/")[-1]
        level = levels_for[name]
        (maj, minr, pat), content = get_version_from_init(initp)
        newt = bump_tuple((maj, minr, pat), level)
        set_version_in_init(initp, content, tuple_to_str(newt))
        bumped_names.append(name)
        print(f"✅ {initp} bumped ({level})")

    # Append trailers (only when enforcement happened)
    if not msg.endswith("\n"):
        msg += "\n"
    if "Precommit-Run: true" not in msg:
        msg += "Precommit-Run: true\n"
    msg += "Semver-Bump: " + ",".join(sorted(bumped_names)) + "\n"
    # Echo the resolved levels for auditability
    if len(touched_names) == 1:
        only = next(iter(touched_names))
        msg += f"Semver-Level-{only}: {levels_for[only]}\n"
    else:
        for name in sorted(touched_names):
            msg += f"Semver-Level-{name}: {levels_for[name]}\n"

    pathlib.Path(msg_path).write_text(msg, encoding="utf-8")
    sys.exit(0)

# ====== entry point ======
if __name__ == "__main__":
    # If a single arg path is given, this is commit-msg; otherwise it's pre-commit.
    if len(sys.argv) == 2 and os.path.exists(sys.argv[1]):
        do_commit_msg(sys.argv[1])
    else:
        do_pre_commit()