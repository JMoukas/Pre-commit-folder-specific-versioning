#!/usr/bin/env python3
import os, re, subprocess, sys, pathlib

# Map each catalog to the file that holds its version
CATALOGS = {
    "catalogs/catalog_alpha": "catalogs/catalog_alpha/__init__.py",
    "catalogs/catalog_beta" : "catalogs/catalog_beta/__init__.py",
    "catalogs/catalog_gamma": "catalogs/catalog_gamma/__init__.py",
}

PY_RE       = re.compile(r".*\.py$")
VERSION_RE  = re.compile(r'^__version__\s*=\s*["\'](\d+)\.(\d+)\.(\d+)["\']\s*$', re.MULTILINE)

def sh(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()

def staged_paths():
    out = sh("git diff --cached --name-only --diff-filter=ACMR")
    return [p for p in out.splitlines() if p]

def touched_catalogs(paths):
    touched = []
    for cat, init_path in CATALOGS.items():
        for p in paths:
            if p.startswith(cat + "/") and PY_RE.match(p):
                touched.append((cat, init_path))
                break
    return touched

def read_file(path):
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""

def write_file(path, content):
    p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def ensure_version_in_init(init_path, default_version="0.1.0"):
    content = read_file(init_path)
    if not content:
        content = f'"""Package init."""\n__version__ = "{default_version}"\n'
        write_file(init_path, content)
        maj, minr, pat = map(int, default_version.split("."))
        return (maj, minr, pat), content

    m = VERSION_RE.search(content)
    if not m:
        content = content.rstrip() + f'\n__version__ = "{default_version}"\n'
        write_file(init_path, content)
        maj, minr, pat = map(int, default_version.split("."))
        return (maj, minr, pat), content

    return tuple(map(int, m.groups())), content

def set_version_in_init(init_path, content, new_version):
    if VERSION_RE.search(content):
        new_content = VERSION_RE.sub(f'__version__ = "{new_version}"', content)
    else:
        new_content = content.rstrip() + f'\n__version__ = "{new_version}"\n'
    write_file(init_path, new_content)

def bumped_tuple(maj, minr, pat, level):
    level = level.lower().strip()
    if level in ("breaking","major","break","feat!"):
        return (maj+1, 0, 0)
    if level in ("feature","feat","minor"):
        return (maj, minr+1, 0)
    if level in ("fix","patch","chore","refactor","perf","docs","test","build","ci","style"):
        return (maj, minr, pat+1)
    raise ValueError("Unrecognized bump: major | minor/feat | patch/fix")

def fmt_ver(t): return ".".join(map(str, t))

def previews(maj, minr, pat):
    major = fmt_ver((maj+1, 0, 0))
    minor = fmt_ver((maj, minr+1, 0))
    patch = fmt_ver((maj, minr, pat+1))
    return (
        "\n".join([
            f"   - major      → ({maj}+1).0.0    = {major}",
            f"   - minor/feat → {maj}.({minr}+1).0 = {minor}",
            f"   - patch/fix  → {maj}.{minr}.({pat}+1) = {patch}",
        ])
    )

def prompt(q, default=None, options=None):
    if not sys.stdin.isatty():
        return os.environ.get("SEMVER_BUMP", default or "patch")
    while True:
        suffix = f" [{'/'.join(options)}]" if options else ""
        if default: suffix += f" (default: {default})"
        ans = input(f"{q}{suffix}: ").strip()
        if not ans and default: return default
        if not options or ans in options: return ans
        print(f"Expected one of: {options}")

def do_precommit():
    paths = staged_paths()
    cats = touched_catalogs(paths)
    if not cats:
        sys.exit(0)  # no relevant python changes

    # if only the __init__.py files are staged, allow commit (fixups)
    init_paths = [init for (_, init) in cats]
    if all(p in init_paths for p in paths):
        sys.exit(0)

    print("🧭 Python changes detected in:")
    for c, _ in cats: print(f"  • {c}")

    same = prompt("Apply the same bump to all touched catalogs?", default="y", options=["y","n"])
    chosen = None

    for cat, init_path in cats:
        (maj, minr, pat), content = ensure_version_in_init(init_path)
        current = f"{maj}.{minr}.{pat}"
        print(f"\n📦 {cat}")
        print(f"   current: {current}")
        print("   Which number will change?")
        print(previews(maj, minr, pat))

        if same == "y" and chosen is None:
            chosen = prompt("Select bump", default="patch",
                            options=["major","minor","feat","patch","fix"])
        level = chosen if same == "y" else prompt("Select bump", default="patch",
                                                  options=["major","minor","feat","patch","fix"])

        new_tuple = bumped_tuple(maj, minr, pat, level)
        new_version = fmt_ver(new_tuple)
        print(f"   → {current}  ➜  {new_version}")

        set_version_in_init(init_path, content, new_version)
        sh(f"git add {init_path}")
        print(f"✅ {init_path} updated")

    sys.exit(0)

def do_commit_msg(commit_msg_path):
    msg_path = pathlib.Path(commit_msg_path)
    msg = msg_path.read_text(encoding="utf-8")

    paths = staged_paths()
    bumped = []
    for name, initp in {
        "catalog_alpha": CATALOGS["catalogs/catalog_alpha"],
        "catalog_beta" : CATALOGS["catalogs/catalog_beta"],
        "catalog_gamma": CATALOGS["catalogs/catalog_gamma"],
    }.items():
        if initp in paths:
            bumped.append(name)

    trailers = ["Precommit-Run: true"]
    if bumped:
        trailers.append("Semver-Bump: " + ",".join(bumped))

    # Append trailers if missing
    for t in trailers:
        if t not in msg:
            if not msg.endswith("\n"):
                msg += "\n"
            msg += t + "\n"

    msg_path.write_text(msg, encoding="utf-8")
    sys.exit(0)

if __name__ == "__main__":
    # If we received a single argument, pre-commit is invoking us as a commit-msg hook
    if len(sys.argv) == 2 and os.path.exists(sys.argv[1]):
        do_commit_msg(sys.argv[1])
    else:
        do_precommit()