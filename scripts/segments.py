#!/usr/bin/env python3
"""Lists, and downloads, the BRouter routing tiles (5 x 5 degrees) the regions need.

Usage:
  segments.py list regions.json            -> prints the tile names
  segments.py fetch regions.json out_dir   -> downloads them from brouter.de into out_dir
"""
import json
import math
import os
import sys
import time
import urllib.request

BROUTER = "https://brouter.de/brouter/segments4/"


def tile_name(lon, lat):
    lo = int(math.floor(lon / 5.0) * 5)
    la = int(math.floor(lat / 5.0) * 5)
    return ("W%d" % -lo if lo < 0 else "E%d" % lo) + "_" + ("S%d" % -la if la < 0 else "N%d" % la)


def tiles_for(bbox):
    west, south, east, north = bbox
    out = []
    lon = math.floor(west / 5.0) * 5.0
    while lon <= east:
        lat = math.floor(south / 5.0) * 5.0
        while lat <= north:
            out.append(tile_name(lon, lat))
            lat += 5.0
        lon += 5.0
    return out


def all_tiles(regions):
    names = []
    for r in regions:
        for t in tiles_for(r["bbox"]):
            if t not in names:
                names.append(t)
    return names


def fetch(names, out):
    os.makedirs(out, exist_ok=True)
    for t in names:
        dest = os.path.join(out, t + ".rd5")
        if os.path.exists(dest):
            continue
        req = urllib.request.Request(BROUTER + t + ".rd5", headers={"User-Agent": "ridewire-maps (GitHub Actions)"})
        try:
            with urllib.request.urlopen(req, timeout=300) as res, open(dest + ".part", "wb") as f:
                while True:
                    b = res.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"{t}: no routing data (sea)")
                continue
            raise
        os.replace(dest + ".part", dest)
        print(f"{t}: {os.path.getsize(dest)} bytes")
        time.sleep(1)  # be gentle with brouter.de


if __name__ == "__main__":
    regions = json.load(open(sys.argv[2]))["regions"]
    names = all_tiles(regions)
    if sys.argv[1] == "list":
        print("\n".join(names))
    else:
        fetch(names, sys.argv[3])
