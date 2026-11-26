#!/usr/bin/env python3
"""Writes manifest.json, the region list the app reads.

Usage: manifest.py regions.json dist_dir tag built_date > manifest.json
dist_dir holds <id>.pmtiles, <id>.places.db and the *.rd5 routing tiles, as published. Each
region file is listed with its size and SHA-256.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from segments import tiles_for  # noqa: E402


def sha256(path):
    """The file's fingerprint: an update on the phone skips files it already has."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(regions_file, dist, tag, built):
    cfg = json.load(open(regions_file))
    base = f"https://github.com/{cfg['repository']}/releases/download/{tag}/"
    segs = {}
    for f in sorted(os.listdir(dist)):
        if f.endswith(".rd5"):
            segs[f[:-4]] = os.path.getsize(os.path.join(dist, f))
    regions = []
    for r in cfg["regions"]:
        files = []
        for name, asset in (("map.pmtiles", r["id"] + ".pmtiles"), ("places.db", r["id"] + ".places.db")):
            path = os.path.join(dist, asset)
            if not os.path.exists(path):
                break
            files.append({"name": name, "url": base + asset, "bytes": os.path.getsize(path), "sha256": sha256(path)})
        if len(files) != 2:
            print(f"skipping {r['id']}: files missing", file=sys.stderr)
            continue
        regions.append({
            "id": r["id"], "name": r["name"], "group": r["group"], "country": r["country"],
            "bbox": r["bbox"], "updated": built, "files": files,
            "segments": [t for t in tiles_for(r["bbox"]) if t in segs],
        })
    json.dump({"version": 1, "built": built, "segmentBase": base, "segments": segs, "regions": regions}, sys.stdout, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:5])
