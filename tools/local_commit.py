"""Stage the project in a local Git repository (no remote, no push).

Uses dulwich because git.exe is not installed on this machine. The file list is
explicit so that build output and private data can never be committed by
accident; a post-stage assertion fails closed if anything forbidden slips in.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dulwich import porcelain  # noqa: E402
from dulwich.repo import Repo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PREFIXES = (
    b"data/", b"shim/target/", b"__pycache__/", b"ui-shots/",
    b"backups/", b"exports/", b"screenshots/",
)
FORBIDDEN_SUFFIXES = (b".sqlite3", b".sqlite3-wal", b".zip", b".pyc")


def main(message: str) -> int:
    repo_dir = ROOT / ".git"
    repo = Repo(str(ROOT)) if repo_dir.exists() else Repo.init(str(ROOT))

    files: list[str] = []
    for pattern in (
        "flightdesk/**/*.py", "flightdesk/**/*.js", "flightdesk/**/*.css",
        "flightdesk/**/*.html", "tools/*", "tests/*", "fixtures/**/*.md",
        "shim/Cargo.toml", "shim/Cargo.lock", "shim/src/*.rs",
        "README.md", "BUILD-STATUS.md", "VERIFICATION.md", "OWNER-GUIDE.md",
        "CHECKPOINT.md", "REVIEW-BRIEF.md",
        "requirements.txt",
        ".gitignore", "FlightDesk.cmd", "FlightDesk-DeepSeek-Build.md",
    ):
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if "__pycache__" in rel or rel.startswith("shim/target"):
                continue
            files.append(rel)
    files = sorted(set(files))
    porcelain.add(str(ROOT), paths=files)

    index = repo.open_index()
    staged = sorted(index)
    bad = [
        p for p in staged
        if p.startswith(FORBIDDEN_PREFIXES) or p.endswith(FORBIDDEN_SUFFIXES)
    ]
    if bad:
        print("REFUSED_TO_COMMIT forbidden_paths=" + ",".join(p.decode() for p in bad))
        return 2

    markers = (b"BEGIN " + b"RSA", b"-----" + b"BEGIN")

    for rel in files:
        blob = (ROOT / rel).read_bytes()
        if any(marker in blob for marker in markers):
            print(f"REFUSED_TO_COMMIT secret_material_in={rel}")
            return 3

    try:
        porcelain.commit(
            str(ROOT),
            message=message.encode("utf-8"),
            author=b"Flight Desk <local@localhost>",
            committer=b"Flight Desk <local@localhost>",
        )
    except Exception as exc:  # nothing to commit
        print("commit_skipped=" + type(exc).__name__)
        return 0
    head = repo.head().decode()
    print(f"committed {head[:12]} files={len(staged)}")
    return 0


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Flight Desk: local checkpoint"
    raise SystemExit(main(msg))
