#!/usr/bin/env python3
"""Builds the site into public/.

    pip install -r requirements.txt
    python run.py            # download the latest data and build the site
    python run.py --serve    # build, then preview at http://localhost:8000

Options: --refresh (download even if the data is recent), --sims N, --out DIR, --data DIR
"""
import argparse
import functools
import http.server
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nbl.build import build  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Build the NBL Index site")
    parser.add_argument("--refresh", action="store_true", help="download the data even if it's recent")
    parser.add_argument("--sims", type=int, default=10000, help="number of simulated seasons (default 10000)")
    parser.add_argument("--out", default=os.path.join(HERE, "public"), help="output folder")
    parser.add_argument("--data", default=os.path.join(HERE, "data"), help="data folder")
    parser.add_argument("--serve", action="store_true", help="preview the site at http://localhost:8000")
    args = parser.parse_args()
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    started = time.time()
    build(cfg, args.data, args.out, refresh=args.refresh, n_sims=args.sims,
          ledger_path=os.path.join(args.data, "ledger.json"))
    print(f"Done in {time.time() - started:.0f}s.")
    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=args.out)
        print("Preview: http://localhost:8000  (Ctrl+C to stop)")
        http.server.ThreadingHTTPServer(("", 8000), handler).serve_forever()


if __name__ == "__main__":
    main()
