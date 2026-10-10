#!/bin/bash
# Development tests only: make third_party/DocCornerNet-CoordClass a git checkout of the pinned
# upstream revision, so the frozen detector's provenance checks (src/geometry/doccornernet.py)
# can run `git rev-parse HEAD` and `git show <revision>:model.py` there.
#   bash scripts/fetch_doccornernet_upstream.sh
# The image and the service never need this: they use the four committed files listed in
# models/MODELS.sha256. Only the upstream git history is fetched (one commit, shallow), and the
# committed files are left byte for byte as they are. The committed copies came from a Windows
# checkout and carry CRLF line endings; the script refuses unless they hash to the values pinned in
# docker/model_files.sha256 and, with CRLF read as LF (the comparison doccornernet.py makes),
# equal upstream's model.py and LICENSE at the pinned revision.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=third_party/DocCornerNet-CoordClass
URL=https://github.com/mapo80/DocCornerNet-CoordClass
REV=d877dc382c4ec56299d2b2662b979581bae9c8cd
lf_sha256() { tr -d '\r' | sha256sum | cut -c1-64; }

sha256sum --strict --quiet --check <(sed -n "s#  /app/\($DIR/.*\)\$#  \1#p" docker/model_files.sha256)
if [ -e "$DIR/.git" ]; then
  [ "$(git -C "$DIR" rev-parse HEAD)" = "$REV" ] || { echo "FETCH_BLOCKED:$DIR/.git is at another revision"; exit 1; }
  echo "already at $REV"; exit 0
fi
git init -q "$DIR"
git -C "$DIR" fetch -q --depth 1 "$URL" "$REV"
for f in model.py LICENSE; do
  if [ "$(git -C "$DIR" show "$REV:$f" | lf_sha256)" != "$(lf_sha256 < "$DIR/$f")" ]; then
    rm -rf "$DIR/.git"
    echo "FETCH_BLOCKED:upstream $f at $REV differs from the committed copy"; exit 1
  fi
done
# point HEAD at the revision without touching the working files (the committed copies stay)
git -C "$DIR" update-ref HEAD "$REV"
git -C "$DIR" read-tree "$REV"
echo "$DIR: upstream $REV fetched, model.py and LICENSE hash-verified; committed files unchanged"
