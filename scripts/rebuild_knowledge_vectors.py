# -*- coding: utf-8 -*-
"""Backfill or rebuild the internal knowledge-base vector index.

Examples:
    python scripts/rebuild_knowledge_vectors.py
    python scripts/rebuild_knowledge_vectors.py --force
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", help="Override PORTAL_DATA_ROOT for this run")
    parser.add_argument("--force", action="store_true", help="Rebuild even unchanged reports")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.data_root:
        os.environ["PORTAL_DATA_ROOT"] = str(Path(args.data_root).resolve())

    from internal_knowledge_base.routes import rebuild_knowledge_vector_index

    def progress(done, total, report):
        title = str(report.get("title") or "未命名报告")[:60]
        print(f"[{done}/{total}] {title}", flush=True)

    result = rebuild_knowledge_vector_index(force=args.force, progress=progress)
    print(
        "[done] indexed_reports={reports} chunks={chunks} rebuilt={rebuilt} "
        "vector_bytes={vector_bytes} version={vectorVersion}".format(**result),
        flush=True,
    )


if __name__ == "__main__":
    main()
