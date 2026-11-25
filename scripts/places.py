#!/usr/bin/env python3
"""Builds a region's place list for Ridewire: towns and places to search, and speed cameras.

Input: GeoJSON sequence from `osmium export` of the filtered region (see the workflow).
Output: an SQLite file with
  place(id, name, kind, sub, lat, lon, rank)   -- what search shows
  places_fts(name, alt)                        -- FTS4 index, docid = place.id
  camera(lat, lon, maxspeed)                   -- fixed speed cameras

Usage: places.py places.geojsonseq places.db
"""
import json
import sqlite3
import sys

PLACE_RANK = {"city": 50, "town": 40, "village": 30, "suburb": 20, "hamlet": 10, "locality": 5, "neighbourhood": 5}
WORDS = {
    "city": "City", "town": "Town", "village": "Village", "suburb": "Suburb", "hamlet": "Hamlet",
    "locality": "Locality", "neighbourhood": "Neighbourhood", "fuel": "Fuel", "viewpoint": "Viewpoint",
    "attraction": "Attraction", "museum": "Museum", "camp_site": "Camp site", "picnic_site": "Picnic site",
    "peak": "Peak", "mountain_pass": "Pass", "restaurant": "Restaurant", "cafe": "Cafe",
    "fast_food": "Fast food", "pub": "Pub", "motorcycle": "Motorcycle shop", "beach": "Beach",
}


def kind_of(tags):
    """The kind the app knows (it picks icons from these), or None to skip."""
    if tags.get("place") in PLACE_RANK:
        return tags["place"]
    if tags.get("amenity") in ("fuel", "restaurant", "cafe", "fast_food", "pub"):
        return tags["amenity"]
    if tags.get("tourism") in ("viewpoint", "attraction", "museum", "camp_site", "picnic_site"):
        return tags["tourism"]
    if tags.get("natural") in ("peak", "beach"):
        return tags["natural"]
    if tags.get("mountain_pass") == "yes":
        return "mountain_pass"
    if tags.get("shop") == "motorcycle":
        return "motorcycle"
    return None


def speed(v):
    """maxspeed in km/h, or 0: "60" -> 60, "40 mph" -> 64."""
    if not v:
        return 0
    v = v.strip().lower()
    try:
        if v.endswith("mph"):
            return round(float(v[:-3].strip()) * 1.609)
        return int(float(v.split(";")[0]))
    except ValueError:
        return 0


def main(src, dst):
    db = sqlite3.connect(dst)
    db.executescript(
        """
        PRAGMA page_size = 4096;
        DROP TABLE IF EXISTS place;
        DROP TABLE IF EXISTS places_fts;
        DROP TABLE IF EXISTS camera;
        CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, sub TEXT,
                            lat REAL NOT NULL, lon REAL NOT NULL, rank INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE places_fts USING fts4(name, alt);
        CREATE TABLE camera (lat REAL NOT NULL, lon REAL NOT NULL, maxspeed INTEGER NOT NULL DEFAULT 0);
        """
    )
    places = cameras = 0
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feat = json.loads(line)
            geom = feat.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            lon, lat = geom["coordinates"][:2]
            tags = feat.get("properties") or {}
            if tags.get("highway") == "speed_camera" or tags.get("enforcement") == "maxspeed":
                db.execute("INSERT INTO camera (lat, lon, maxspeed) VALUES (?, ?, ?)", (lat, lon, speed(tags.get("maxspeed"))))
                cameras += 1
                continue
            name = tags.get("name:en") or tags.get("name") or tags.get("brand")
            kind = kind_of(tags)
            if not name or not kind:
                continue
            word = WORDS.get(kind, kind.replace("_", " ").capitalize())
            if kind == "fuel" and tags.get("brand") and tags["brand"] not in name:
                word += " · " + tags["brand"]
            town = tags.get("addr:city") or tags.get("is_in:town") or tags.get("is_in")
            sub = word + (" · " + town if town and town != name else "")
            rank = PLACE_RANK.get(kind, 0)
            try:
                rank += min(49, int(tags.get("population", "0").replace(" ", "").replace(",", "")) // 20000)
            except ValueError:
                pass
            alt = " ".join(v for k, v in tags.items() if k in ("name", "alt_name", "old_name", "short_name", "brand", "official_name") and v != name)
            cur = db.execute("INSERT INTO place (name, kind, sub, lat, lon, rank) VALUES (?, ?, ?, ?, ?, ?)", (name, kind, sub, lat, lon, rank))
            db.execute("INSERT INTO places_fts (docid, name, alt) VALUES (?, ?, ?)", (cur.lastrowid, name, alt))
            places += 1
    db.execute("CREATE INDEX camera_lat ON camera (lat)")
    db.execute("INSERT INTO places_fts (places_fts) VALUES ('optimize')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"{dst}: {places} places, {cameras} cameras")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
