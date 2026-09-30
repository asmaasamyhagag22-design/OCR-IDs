"""Evidence that the runtime guard refuses a forbidden read and permits a normal one.

The PreToolUse hook denies a *tool call* that names a blocked card. This script
exists to show the other half: a plain `python scripts/...` run, which the hook
cannot see inside, is still refused by the loader.

It takes a set name, never a card id, so that running it never puts a blocked id
into a shell command - which the hook would (correctly) deny before it ran.

    python scripts/phase2m_h_guard_demo.py --set audit         # must be refused
    python scripts/phase2m_h_guard_demo.py --set development   # must be permitted

Exit code 0 means the run did what the set name says it should. Exit code 1 means
the guard did the opposite, which is a governance failure, not a test failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.geometry import rectify  # noqa: E402
from src.governance import guard  # noqa: E402

IMAGES = ROOT / "data" / "official_eval" / "images"


def pick(set_name: str) -> str:
    """A card id from the named set, chosen here so no caller has to type one."""
    data = json.loads(guard.BLOCKLIST.read_text(encoding="utf-8"))
    blocked = set(data["blocked_card_ids"]["audit"]) | set(
        data["blocked_card_ids"]["internal_validation"])
    if set_name == "audit":
        return sorted(data["blocked_card_ids"]["audit"])[0]
    if set_name == "internal_validation":
        return sorted(data["blocked_card_ids"]["internal_validation"])[0]
    # A development card: present on disk, in neither forbidden set.
    for path in sorted(IMAGES.glob("*.*")):
        stem = path.stem.split("_")[0]
        if stem not in blocked:
            return stem
    raise SystemExit("no development card is available in this worktree")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="set_name", required=True,
                        choices=("audit", "internal_validation", "development"))
    args = parser.parse_args()

    card_id = pick(args.set_name)
    candidates = [IMAGES / f"{card_id}{suffix}" for suffix in (".jpg", ".png")]
    path = next((c for c in candidates if c.is_file()), candidates[0])
    must_be_refused = args.set_name != "development"

    # The id itself is never printed: it identifies a card, and for the forbidden
    # sets naming one is exactly what the governance rules are about.
    print(f"set:      {args.set_name}")
    print(f"loader:   src.geometry.rectify.load_rgb")
    print(f"expected: {'refused' if must_be_refused else 'permitted'}")
    try:
        image = rectify.load_rgb(path)
    except guard.GovernanceViolation as exc:
        print(f"result:   REFUSED - {str(exc).splitlines()[0]}")
        return 0 if must_be_refused else 1
    except (FileNotFoundError, OSError) as exc:
        print(f"result:   the guard permitted the read; the file itself failed ({type(exc).__name__})")
        return 1 if must_be_refused else 0
    print(f"result:   PERMITTED - decoded {image.shape[1]}x{image.shape[0]} RGB")
    return 1 if must_be_refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
