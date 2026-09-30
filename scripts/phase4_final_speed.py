"""Phase 4 final pass, Stage 6: per-image and per-stage timing, run INSIDE the container (rule amendment 5).

    python /work/phase4_final_speed.py --images /work/dev --order /work/order.txt --out /work/out/rows.jsonl

It needs only the image's own code (/app/src) or a candidate tree given by PYTHONPATH. Timers
wrap the recognizer's calls to the first pass with F1v (read_with_second_pass), F8
(reread_if_invalid) and F9 (region_search.search) in this process only; the product code is
not changed to time itself. One JSON line per image, private: it holds the reading. The
file name is the key; no label is read here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if "/app" not in sys.path:
    sys.path.append("/app")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--order", required=True, help="file names, one per line, in development_pool() order")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    from src.service.runtime import configure_threads    # as src/service/__main__.py: before the recognizer

    configure_threads(int(os.environ.get("OCR_CPU_THREADS", "4")))
    from src.service import recognizer as rz
    spent: dict[str, float] = {}

    def timed(name, fn):
        def wrapper(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                spent[name] = spent.get(name, 0.0) + (time.perf_counter() - t0) * 1000.0
        return wrapper

    rz.sp.read_with_second_pass = timed("first_pass_and_f1v", rz.sp.read_with_second_pass)
    rz.vr.reread_if_invalid = timed("f8", rz.vr.reread_if_invalid)
    rz.rs.search = timed("f9", rz.rs.search)

    recognizer = rz.Recognizer()
    t0 = time.perf_counter()
    recognizer.load()
    load_ms = (time.perf_counter() - t0) * 1000.0
    names = [n.strip() for n in Path(args.order).read_text(encoding="utf-8").splitlines() if n.strip()]
    if args.limit:
        names = names[:args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"load_ms": round(load_ms, 1), "pythonpath": os.environ.get("PYTHONPATH", ""),
                                 "images": len(names)}) + "\n")
        for name in names:
            payload = (Path(args.images) / name).read_bytes()
            spent.clear()
            t0 = time.perf_counter()
            row = {"name": name}
            try:
                rec = recognizer.recognize(payload)
                row.update(outcome=rec.outcome, admitted=rec.admitted, reading=rec.reading,
                           margin=rec.margin, reason_code=rec.reason_code, fixes=list(rec.fixes))
            except Exception as exc:  # noqa: BLE001 - recorded, never raised
                row.update(outcome="error", error=type(exc).__name__)
            row["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
            row["stage_ms"] = {k: round(v, 1) for k, v in spent.items()}
            handle.write(json.dumps(row) + "\n")
            handle.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
