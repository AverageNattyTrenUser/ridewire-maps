#!/usr/bin/env python3
"""Fetches Overture Maps places (business listings from Meta, Microsoft, Foursquare, AllThePlaces and
others) inside a bounding box, from Overture's latest release, as JSON lines for places.py.

Kept: places in the region's country (a region's box can reach over a border), that Overture is
at least 50% sure exist, not marked closed, and whose every source is under a licence the region
files can carry (CDLA-Permissive-2.0, Apache-2.0, CC0-1.0).

Each line: name, cat (basic category), hier (the category's family, top to leaf: "a/b/c"),
conf (0 to 1), lon, lat, street (the address as one line), locality, brand.

Usage: overture.py west,south,east,north out.jsonl [country code, e.g. ZA]
Needs: pip install duckdb
"""
import json
import re
import sys
import urllib.request

import duckdb

CATALOG = "https://stac.overturemaps.org/catalog.json"
LICENCES = ("CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0")
MIN_CONFIDENCE = 0.5


def latest_release():
    with urllib.request.urlopen(CATALOG, timeout=60) as r:
        return json.load(r)["latest"]


def main(bbox, out, country=None):
    w, s, e, n = (float(v) for v in bbox.split(","))
    if country and not re.fullmatch(r"[A-Za-z]{2}", country):
        raise SystemExit(f"Not a country code: {country}")
    in_country = f"coalesce(addresses[1].country, '{country.upper()}') = '{country.upper()}'" if country else "true"
    release = latest_release()
    src = f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}', hive_partitioning=1)").fetchall()}

    # The schema moves on between releases: take what this one has.
    if "taxonomy" in cols:
        cat, hier = "basic_category", "array_to_string(taxonomy.hierarchy, '/')"
    else:
        cat, hier = "categories.primary", "categories.primary"
    status = "coalesce(operating_status, 'open') = 'open'" if "operating_status" in cols else "true"
    licences = ", ".join(f"'{v}'" for v in LICENCES)

    con.execute(f"""
        COPY (
            SELECT names.primary AS name, {cat} AS cat, {hier} AS hier, round(confidence, 3) AS conf,
                   round((bbox.xmin + bbox.xmax) / 2, 6) AS lon, round((bbox.ymin + bbox.ymax) / 2, 6) AS lat,
                   addresses[1].freeform AS street, addresses[1].locality AS locality,
                   brand.names.primary AS brand
            FROM read_parquet('{src}', hive_partitioning=1)
            WHERE bbox.xmin BETWEEN {w} AND {e} AND bbox.ymin BETWEEN {s} AND {n}
              AND names.primary IS NOT NULL
              AND {in_country}
              AND confidence >= {MIN_CONFIDENCE}
              AND {status}
              AND len(list_filter(sources, lambda x: x.license NOT IN ({licences}))) = 0
        ) TO '{out}' (FORMAT JSON)
    """)
    count = sum(1 for _ in open(out, encoding="utf-8"))
    print(f"{out}: {count} Overture places from release {release}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
