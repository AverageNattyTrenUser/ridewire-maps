#!/usr/bin/env python3
"""Builds a region's place list for Ridewire: what search finds and the map shows offline, and
fixed speed cameras.

Inputs:
  places.geojsonseq  OpenStreetMap features of the region from `osmium export` (see the workflow):
                     everything named, addresses, fuel stations and speed cameras
  overture.jsonl     optional: Overture Maps places in the region (scripts/overture.py)

Output: an SQLite file in format 2 (PRAGMA user_version = 2) with
  place(id, name, kind, sub, lat, lon, rank, grp, show)
      name  what the rider reads: "FreshCo Harbour View"
      kind  the OpenStreetMap or Overture category, "supermarket" (older apps pick icons from it)
      sub   the second line, what and where: "Supermarket · Harbour View · Seaside"
      rank  how much it counts: towns by size (5 to 99, like the online search's importance),
            others 0 to 9 (the map shows the higher ones first when it can't show them all)
      grp   the icon group: town fuel food shop lodging health money moto sight building
            address street other
      show  on the map: 1 shops and other places (from zoom 14.5), 2 named buildings (from
            zoom 17), 0 never (towns, streets, addresses and areas, which the map already names)
  places_fts(name, alt, ctx)  FTS4 index, contentless, docid = place.id. The text is stored the way
            the app searches it: lower case, without accents, words split on anything that isn't
            a letter or digit. alt: other names (the brand, old and short names, the Afrikaans
            name). ctx: words that find it without being its name (the mall, the street, the
            suburb and town, what it is: "pizza", "petrol").
  camera(lat, lon, maxspeed)  fixed speed cameras

Usage: places.py places.geojsonseq places.db [overture.jsonl]
"""
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict

# ------------------------------------------------------------------------------------ words


def norm(s):
    """As the app searches: lower case, no accents, words split on anything not a letter or digit."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def toks(s):
    return norm(s).split()


def word(v):
    """"car_repair" as a rider reads it: "Car repair"."""
    v = (v or "").replace("_", " ").strip()
    return v[:1].upper() + v[1:]


def join_words(parts):
    """The parts' words once each, in order, for the search index."""
    seen, out = set(), []
    for p in parts:
        for t in toks(p):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return " ".join(out)


# Words a rider might search for a kind of place, besides its name.
SYNONYMS = {
    "fuel": "fuel petrol garage filling station diesel",
    "supermarket": "supermarket groceries grocery",
    "pharmacy": "pharmacy chemist",
    "bank": "bank atm cash",
    "atm": "atm cash bank",
    "hospital": "hospital clinic medical emergency",
    "clinic": "clinic medical doctor",
    "mall": "mall shopping centre center",
    "coffee": "coffee cafe",
    "lodging": "accommodation stay",
    "motorcycle": "motorcycle motorbike bike",
    "tyres": "tyres tires tyre",
    "repair": "mechanic repair workshop",
    "police": "police station",
}

# Groups never merged with another place of the same name: "Main Road" the street isn't a shop.
PLAIN = {"street", "address", "town"}

GENERIC_NAMES = {"parking", "car park", "toilets", "toilet", "shop", "store", "building", "house", "garage",
                 "entrance", "gate", "bus stop", "unnamed", "no name", "yes"}

# ---------------------------------------------------------------- OpenStreetMap: what it is


PLACE_RANK = {"city": 80, "town": 55, "village": 35, "suburb": 25, "quarter": 15, "neighbourhood": 15,
              "hamlet": 10, "locality": 5, "isolated_dwelling": 2, "farm": 2}
TOWN_KINDS = {"city", "town", "village", "hamlet"}
SUBURB_KINDS = {"suburb", "quarter", "neighbourhood"}
FOOD = {"restaurant", "cafe", "fast_food", "pub", "bar", "biergarten", "ice_cream", "food_court"}
HEALTH = {"hospital", "clinic", "doctors", "dentist", "pharmacy", "veterinary"}
MONEY = {"bank", "atm", "bureau_de_change"}
MOTO_SHOPS = {"motorcycle", "motorcycle_repair", "tyres", "car_repair", "car_parts", "car"}
LODGING = {"hotel", "motel", "guest_house", "hostel", "apartment", "chalet", "camp_site", "caravan_site",
           "alpine_hut", "wilderness_hut"}
SIGHTS = {"viewpoint", "attraction", "museum", "gallery", "zoo", "theme_park", "aquarium", "picnic_site",
          "artwork"}
AMENITY_SKIP = {"parking", "parking_space", "parking_entrance", "bench", "waste_basket", "waste_disposal",
                "recycling", "toilets", "drinking_water", "bicycle_parking", "vending_machine", "telephone",
                "post_box", "shelter", "hunting_stand", "clock", "bbq", "loading_dock", "grit_bin", "water_point",
                "watering_place", "fountain", "letter_box", "give_box", "lounger", "payment_terminal",
                "ticket_validator", "bicycle_repair_station", "compressed_air", "vacuum_cleaner", "trolley_bay",
                "smoking_area", "shower", "photo_booth", "kneipp_water_cure", "dressing_room", "table"}
LEISURE_SIGHTS = {"park", "nature_reserve", "garden", "marina", "beach_resort", "water_park", "bird_hide"}
LEISURE_PLACES = {"stadium", "sports_centre", "golf_course", "fitness_centre", "swimming_pool", "ice_rink",
                  "horse_riding", "dance", "bowling_alley", "miniature_golf", "amusement_arcade", "escape_game",
                  "track", "sports_hall", "trampoline_park"}
NATURAL_SIGHTS = {"peak", "beach", "bay", "cape", "cave_entrance", "spring", "volcano", "hot_spring", "arch",
                  "rock", "saddle", "dune", "gorge", "valley", "cliff"}
LANDUSE_WORDS = {"retail": "Shopping area", "commercial": "Business park", "industrial": "Industrial area",
                 "residential": "Residential area", "farmyard": "Farm", "farmland": "Farm", "vineyard": "Vineyard",
                 "orchard": "Orchard", "cemetery": "Cemetery", "military": "Military area"}
BUILDING_WORDS = {"yes": "Building", "apartments": "Apartments", "residential": "Residential building",
                  "house": "House", "detached": "House", "semidetached_house": "House", "terrace": "Terraced houses",
                  "commercial": "Commercial building", "office": "Office building", "retail": "Retail building",
                  "industrial": "Industrial building", "warehouse": "Warehouse", "dormitory": "Residence",
                  "public": "Public building", "civic": "Civic building", "government": "Government building",
                  "garage": "Garage", "garages": "Garages", "train_station": "Station", "transportation": "Station"}
KIND_WORDS = {"fuel": "Fuel station", "charging_station": "Charging station", "convenience": "Convenience store",
              "doityourself": "Hardware store", "hardware": "Hardware store", "alcohol": "Liquor store",
              "beverages": "Drinks store", "mobile_phone": "Phone shop", "clothes": "Clothing store",
              "shoes": "Shoe store", "beauty": "Beauty salon", "car_repair": "Car repair", "car_parts": "Car parts",
              "car": "Car dealer", "motorcycle": "Motorcycle shop", "motorcycle_repair": "Motorcycle repair",
              "tyres": "Tyres", "mall": "Mall", "department_store": "Department store", "chemist": "Chemist",
              "doctors": "Doctor", "dentist": "Dentist", "atm": "ATM", "bureau_de_change": "Money exchange",
              "fast_food": "Fast food", "ice_cream": "Ice cream", "place_of_worship": "Place of worship",
              "guest_house": "Guest house", "camp_site": "Camp site", "caravan_site": "Caravan park",
              "picnic_site": "Picnic site", "mountain_pass": "Pass", "aerodrome": "Airport",
              "station": "Station", "halt": "Station", "wine_cellar": "Wine cellar", "rest_area": "Rest area",
              "services": "Services", "townhall": "Town hall", "car_wash": "Car wash", "car_rental": "Car rental",
              "post_office": "Post office", "fire_station": "Fire station", "police": "Police",
              "kindergarten": "Pre-school", "community_centre": "Community centre", "sports_centre": "Sports centre",
              "fitness_centre": "Gym", "nature_reserve": "Nature reserve", "golf_course": "Golf course",
              "water": "Water", "reservoir": "Dam"}


def kind_word(v):
    return KIND_WORDS.get(v) or word(v)


def classify(tags):
    """What an OpenStreetMap feature is to a rider: (group, kind, word, show, rank), or None to skip it."""
    t = tags.get
    place = t("place")
    if place in PLACE_RANK:
        if place in ("farm", "isolated_dwelling"):
            return "other", place, word(place), 0, 2
        rank = PLACE_RANK[place]
        try:
            rank += min(19, int(str(t("population", "0")).replace(" ", "").replace(",", "")) // 100000)
        except ValueError:
            pass
        return "town", place, word(place), 0, rank
    a = t("amenity")
    if a in ("fuel", "charging_station"):
        return "fuel", a, kind_word(a), 1, 9
    if a and a not in AMENITY_SKIP:
        if a in FOOD:
            return "food", a, kind_word(a), 1, 5
        if a in HEALTH:
            return "health", a, kind_word(a), 1, 7 if a == "hospital" else 5
        if a in MONEY:
            return "money", a, kind_word(a), 1, 4
        if a in ("car_wash", "vehicle_inspection", "motorcycle_rental"):
            return "moto", a, kind_word(a), 1, 4
        if a == "marketplace":
            return "shop", a, kind_word(a), 1, 6
        return "other", a, kind_word(a), 1, 3
    s = t("shop")
    if s and s not in ("no", "vacant"):
        if s in MOTO_SHOPS:
            return "moto", s, kind_word(s), 1, 7 if s.startswith("motorcycle") else 5
        if s == "mall":
            return "shop", s, kind_word(s), 1, 8
        if s in ("supermarket", "convenience"):
            return "shop", s, kind_word(s), 1, 6 if s == "supermarket" else 5
        return "shop", s, kind_word(s), 1, 4
    tour = t("tourism")
    if tour:
        if tour in LODGING:
            return "lodging", tour, kind_word(tour), 1, 4
        if tour in SIGHTS:
            return "sight", tour, kind_word(tour), 1, 6
        if tour == "wine_cellar":
            return "food", tour, kind_word(tour), 1, 5
        if tour == "information" and t("information") == "office":
            return "other", tour, "Tourist information", 1, 4
        return None
    if t("healthcare"):
        v = t("healthcare")
        return "health", v, kind_word(v), 1, 5
    lei = t("leisure")
    if lei in LEISURE_SIGHTS:
        return "sight", lei, kind_word(lei), 1, 5
    if lei in LEISURE_PLACES:
        return "other", lei, kind_word(lei), 1, 3
    if lei == "resort":
        return "lodging", lei, kind_word(lei), 1, 4
    if t("historic") and t("historic") not in ("boundary_stone", "milestone"):
        v = t("historic")
        return "sight", v, kind_word(v), 1, 4
    if t("mountain_pass") == "yes":
        return "sight", "mountain_pass", "Pass", 1, 7
    nat = t("natural")
    if nat in NATURAL_SIGHTS:
        return "sight", nat, kind_word(nat), 1, 5
    if t("waterway") == "waterfall":
        return "sight", "waterfall", "Waterfall", 1, 6
    if t("man_made") == "lighthouse":
        return "sight", "lighthouse", "Lighthouse", 1, 5
    if t("aeroway") == "aerodrome":
        return "other", "aerodrome", "Airport", 1, 6
    if t("railway") in ("station", "halt"):
        return "other", t("railway"), "Station", 1, 3
    if t("highway") in ("rest_area", "services"):
        v = t("highway")
        return "fuel" if v == "services" else "other", v, kind_word(v), 1, 6
    if t("office"):
        v = t("office")
        return "other", "office", "Office" if v == "yes" else word(v) + " office" if not v.endswith("office") else word(v), 1, 2
    if t("craft"):
        v = t("craft")
        return "shop", v, kind_word(v), 1, 3
    if t("club"):
        return "other", "club", "Club", 1, 2
    if nat == "water" and t("water") in ("reservoir", "lake", None):
        v = t("water") or "water"
        return "sight", v, "Dam" if v == "reservoir" else "Lake", 0, 3
    lu = t("landuse")
    if lu in LANDUSE_WORDS:
        return ("shop" if lu == "retail" else "other"), lu, LANDUSE_WORDS[lu], 0, 2
    b = t("building")
    if b and b not in ("no", "roof", "carport", "construction"):
        return "building", b, BUILDING_WORDS.get(b) or (word(b) + " building"), 2, 1
    return None


STREETS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
           "living_street", "pedestrian", "road", "busway"}


def street_word(hw):
    if hw in ("motorway", "trunk"):
        return "Highway"
    if hw in ("primary", "secondary"):
        return "Main road"
    return "Street"


# ------------------------------------------------------------------------------- geometry

KM_PER_DEG = 111.32


def dist_km(lon1, lat1, lon2, lat2):
    x = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, lat2 - lat1) * KM_PER_DEG


def ring_centroid(ring):
    """(area, x, y) of a closed ring; the area is in square degrees."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    if abs(a) < 1e-15:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return abs(a) / 2, cx / (3 * a), cy / (3 * a)


def polygons(geom):
    t = geom.get("type")
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return geom["coordinates"]
    return []


def rep_point(geom):
    """One point for a feature: the point, a line's middle vertex, a polygon's centroid (its largest part's)."""
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Point":
        return c[0], c[1]
    if t == "LineString":
        m = c[len(c) // 2]
        return m[0], m[1]
    best = None
    for poly in polygons(geom):
        if poly and len(poly[0]) >= 4:
            area, x, y = ring_centroid(poly[0])
            if best is None or area > best[0]:
                best = (area, x, y)
    return (best[1], best[2]) if best else None


def area_of(geom):
    return sum(ring_centroid(p[0])[0] for p in polygons(geom) if p and len(p[0]) >= 4)


def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def in_polys(x, y, polys):
    for poly in polys:
        if poly and in_ring(x, y, poly[0]) and not any(in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


def bbox_of(polys):
    xs = [p[0] for poly in polys for p in poly[0]]
    ys = [p[1] for poly in polys for p in poly[0]]
    return min(xs), min(ys), max(xs), max(ys)


class Areas:
    """Named areas a place can be in: suburbs and towns, and malls, business parks and estates."""
    CELL = 0.05

    def __init__(self):
        self.grid = defaultdict(list)

    def add(self, name, kind, geom):
        polys = polygons(geom)
        if not polys:
            return
        x0, y0, x1, y1 = bbox_of(polys)
        if (x1 - x0) > 1.5 or (y1 - y0) > 1.5:
            return  # a province or a district: says nothing a suburb or town doesn't
        rec = (area_of(geom), name, kind, (x0, y0, x1, y1), polys)
        for cx in range(int(math.floor(x0 / self.CELL)), int(math.floor(x1 / self.CELL)) + 1):
            for cy in range(int(math.floor(y0 / self.CELL)), int(math.floor(y1 / self.CELL)) + 1):
                self.grid[(cx, cy)].append(rec)

    def find(self, x, y, kind, not_named=None):
        """The smallest area of this kind around the point, or None."""
        best = None
        for rec in self.grid.get((int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL))), ()):
            area, name, k, (x0, y0, x1, y1), polys = rec
            if k != kind or not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            if not_named and norm(name) == not_named:
                continue
            if (best is None or area < best[0]) and in_polys(x, y, polys):
                best = (area, name)
        return best[1] if best else None


class Points:
    """Named points (suburb and town centres), for the nearest one."""
    CELL = 0.1

    def __init__(self):
        self.grid = defaultdict(list)

    def add(self, x, y, name, reach_km):
        self.grid[(int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL)))].append((x, y, name, reach_km))

    def nearest(self, x, y):
        cx, cy = int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL))
        best = None
        for i in (-2, -1, 0, 1, 2):
            for j in (-2, -1, 0, 1, 2):
                for px, py, name, reach in self.grid.get((cx + i, cy + j), ()):
                    d = dist_km(x, y, px, py)
                    if d <= reach and (best is None or d < best[0]):
                        best = (d, name)
        return best[1] if best else None


TOWN_REACH = {"city": 20.0, "town": 8.0, "village": 4.0, "hamlet": 2.0}
SUBURB_REACH = 2.5


class Where:
    """Where a point is: its suburb, its town, and the mall, business park or estate it's in."""

    def __init__(self):
        self.areas = Areas()
        self.suburbs = Points()
        self.towns = Points()

    def suburb(self, x, y):
        return self.areas.find(x, y, "suburb") or self.suburbs.nearest(x, y)

    def town(self, x, y):
        return self.areas.find(x, y, "town") or self.towns.nearest(x, y)

    def complex(self, x, y, own_name):
        return self.areas.find(x, y, "complex", not_named=norm(own_name))


def gather_where(path):
    """First pass: the suburbs, towns, malls and estates, to say where everything else is."""
    w = Where()
    for feat in read_seq(path):
        tags = feat.get("properties") or {}
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        geom = feat.get("geometry") or {}
        place = tags.get("place")
        area = geom.get("type") in ("Polygon", "MultiPolygon")
        if place in SUBURB_KINDS or place in TOWN_KINDS:
            kind = "suburb" if place in SUBURB_KINDS else "town"
            if area:
                w.areas.add(name, kind, geom)
            elif geom.get("type") == "Point":
                x, y = geom["coordinates"][:2]
                if kind == "suburb":
                    w.suburbs.add(x, y, name, SUBURB_REACH)
                else:
                    w.towns.add(x, y, name, TOWN_REACH[place])
        elif area and (tags.get("shop") == "mall" or tags.get("amenity") in ("marketplace", "university", "hospital")
                       or tags.get("landuse") in ("retail", "commercial", "residential", "industrial")
                       or tags.get("tourism") in ("theme_park", "zoo") or tags.get("leisure") == "park"):
            w.areas.add(name, "complex", geom)
    return w


def read_seq(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if line:
                yield json.loads(line)


# ------------------------------------------------------------------------------ the list


class Entry:
    __slots__ = ("name", "kind", "grp", "sub", "x", "y", "rank", "show", "alt", "ctx", "osm")

    def __init__(self, name, kind, grp, sub, x, y, rank, show, alt, ctx, osm):
        self.name, self.kind, self.grp, self.sub = name, kind, grp, sub
        self.x, self.y, self.rank, self.show = x, y, rank, show
        self.alt, self.ctx, self.osm = alt, ctx, osm


class Index:
    """Entries by position, to find the same place twice."""
    CELL = 0.002  # about 200 m

    def __init__(self):
        self.grid = defaultdict(list)

    def key(self, x, y):
        return int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL))

    def add(self, e):
        self.grid[self.key(e.x, e.y)].append(e)

    def near(self, x, y, km):
        cx, cy = self.key(x, y)
        r = int(math.ceil(km / (self.CELL * KM_PER_DEG * 0.8)))
        for i in range(-r, r + 1):
            for j in range(-r, r + 1):
                for e in self.grid.get((cx + i, cy + j), ()):
                    if dist_km(x, y, e.x, e.y) <= km:
                        yield e


def sub_line(*parts):
    out = []
    for p in parts:
        p = (p or "").strip()
        if p and norm(p) not in {norm(o) for o in out}:
            out.append(p)
    return " · ".join(out[:3])


def same_place(a_name, a_ctx, b_name, b_ctx):
    """The same shop under two names: equal, or one is the other plus words from where it is
    ("FreshCo" with branch Harbour View and "FreshCo Harbour View")."""
    an, bn = toks(a_name), toks(b_name)
    if an == bn:
        return True
    sa, sb = set(an), set(bn)
    if not sa or not sb:
        return False
    if sa < sb:
        return (sb - sa) <= set(a_ctx.split())
    if sb < sa:
        return (sa - sb) <= set(b_ctx.split())
    return False


class Builder:
    def __init__(self, where):
        self.w = where
        self.entries = []
        self.index = Index()
        self.streets = defaultdict(list)
        self.towns = {}
        self.fuel_brands = set()
        self.cameras = []
        self.merged = 0

    def where(self, x, y, own):
        return self.w.complex(x, y, own), self.w.suburb(x, y), self.w.town(x, y)

    def add(self, e):
        """Adds [e] unless it's already listed nearby, in which case the two become one: the same
        name within 250 m (a supermarket and Overture's "supermarket" ATM in it), or a name plus
        words from where it is within 150 m, if they're the same kind of place."""
        if e.grp == "town":
            return self.add_town(e)
        for o in self.index.near(e.x, e.y, 0.25):
            if self.same(o, e):
                if norm(e.name) != norm(o.name):
                    o.alt = join_words([o.alt, e.name])
                o.ctx = join_words([o.ctx, e.ctx])
                if e.show and (not o.show or e.show < o.show):
                    o.show = e.show
                if e.grp != o.grp and o.grp in ("other", "building"):
                    o.grp, o.kind, o.sub = e.grp, e.kind, e.sub
                o.rank = max(o.rank, e.rank)
                self.merged += 1
                return o
        self.entries.append(e)
        self.index.add(e)
        return e

    @staticmethod
    def same(o, e):
        if o.grp in PLAIN or e.grp in PLAIN:
            return False
        if toks(o.name) == toks(e.name):
            return True
        if dist_km(e.x, e.y, o.x, o.y) > 0.15:
            return False
        if o.grp != e.grp and not {o.grp, e.grp} & {"other", "building"}:
            return False
        return same_place(o.name, o.ctx, e.name, e.ctx)

    def add_town(self, e):
        """A town or suburb is often both a point (its centre) and an area: listed once, at the centre."""
        reach = 8.0 if e.kind in ("city", "town") else 3.0
        same = self.towns.setdefault(norm(e.name), [])
        for o in same:
            if dist_km(e.x, e.y, o.x, o.y) <= reach:
                o.rank = max(o.rank, e.rank)
                o.alt = join_words([o.alt, e.alt])
                self.merged += 1
                return o
        same.append(e)
        self.entries.append(e)
        return e

    # ------------------------------------------------------------- OpenStreetMap

    def osm(self, feat):
        tags = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        gt = geom.get("type")
        t = tags.get
        if gt == "Point" and (t("highway") == "speed_camera" or t("enforcement") == "maxspeed"):
            x, y = geom["coordinates"][:2]
            self.cameras.append((y, x, speed(t("maxspeed"))))
            return
        if gt == "LineString":
            if t("highway") in STREETS and (t("name:en") or t("name")):
                self.street(tags, geom)
            return
        if gt not in ("Point", "Polygon", "MultiPolygon"):
            return
        p = rep_point(geom)
        if not p:
            return
        x, y = p
        what = classify(tags)
        name = t("name:en") or t("name")
        if what and what[0] == "fuel" and t("amenity") == "fuel" and t("brand"):
            self.fuel_brands.add(norm(t("brand")))
        if what and what[0] == "fuel" and not name:
            name = t("brand") or t("operator") or "Fuel station"
        if not name and what and t("brand"):
            name = t("brand")
        branch = t("branch")
        if name and branch and norm(branch) not in norm(name):
            name = f"{name} {branch}"
        number, street = t("addr:housenumber"), t("addr:street")
        address = f"{number} {street}" if number and street else None
        if not name or norm(name) in GENERIC_NAMES or len(norm(name)) < 2:
            # Not named: an address is still worth finding.
            housename = t("addr:housename")
            if housename and len(norm(housename)) >= 2:
                name, what = housename, ("building", "address", "Building", 2 if gt != "Point" else 0, 1)
            elif address:
                self.address(address, tags, x, y)
                return
            else:
                return
        if not what:
            if address:
                what = ("address", "address", "Address", 0, 0)
            else:
                return
        grp, kind, kword, show, rank = what
        if t("brand") or t("brand:wikidata"):
            rank = min(9, rank + 1) if grp != "town" else rank
        cplx, suburb, town = self.where(x, y, name)
        if grp == "town":
            sub = sub_line(kword, town if kind in SUBURB_KINDS else None)
            ctx_where = [town] if kind in SUBURB_KINDS else []
        else:
            sub = sub_line(kword, cplx or (address if grp in ("building", "address") or not cplx else None),
                           suburb or town)
            ctx_where = [cplx, suburb, town, street, t("addr:suburb"), t("addr:city")]
        alt = join_words([t("name") if t("name") != name else None, t("alt_name"), t("old_name"), t("short_name"),
                          t("official_name"), t("loc_name"), t("name:af"), t("brand"), branch])
        cuisine = (t("cuisine") or "").replace(";", " ")
        syn = synonyms(grp, kind, kword)
        ctx = join_words(ctx_where + [kword, syn, cuisine, address])
        self.add(Entry(name, kind, grp, sub, x, y, rank, show, alt, ctx, True))

    def address(self, address, tags, x, y):
        _, suburb, town = self.where(x, y, address)
        e = Entry(address, "address", "address", sub_line("Address", suburb or town), x, y, 0, 0, "",
                  join_words([suburb, town, tags.get("addr:suburb"), tags.get("addr:city")]), True)
        for o in self.index.near(x, y, 0.03):
            if o.grp == "address" and norm(o.name) == norm(address):
                return
        self.entries.append(e)
        self.index.add(e)

    def street(self, tags, geom):
        name = tags.get("name:en") or tags.get("name")
        c = geom["coordinates"]
        xs = [p[0] for p in c]
        ys = [p[1] for p in c]
        mx, my = rep_point(geom)
        length = sum(dist_km(a[0], a[1], b[0], b[1]) for a, b in zip(c, c[1:]))
        suburb = self.w.suburb(mx, my)
        town = self.w.town(mx, my)
        self.streets[(norm(name), norm(suburb or town or ""))].append(
            (name, tags.get("highway"), mx, my, length, (min(xs), min(ys), max(xs), max(ys)), suburb, town,
             tags.get("name:af"), tags.get("alt_name"), tags.get("old_name")))

    def finish_streets(self):
        """One entry per street in each suburb (a street is many pieces of road, and a dual
        carriageway two), at its longest piece."""
        pad = 0.003
        for pieces in self.streets.values():
            pieces.sort(key=lambda q: -q[4])
            parent = list(range(len(pieces)))

            def root(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i, a in enumerate(pieces):
                ax0, ay0, ax1, ay1 = a[5]
                for j in range(i + 1, len(pieces)):
                    bx0, by0, bx1, by1 = pieces[j][5]
                    if ax0 - pad <= bx1 and bx0 <= ax1 + pad and ay0 - pad <= by1 and by0 <= ay1 + pad:
                        ri, rj = root(i), root(j)
                        if ri != rj:
                            parent[max(ri, rj)] = min(ri, rj)
            groups = defaultdict(list)
            for i, p in enumerate(pieces):
                groups[root(i)].append(p)  # in length order: the first is the longest
            for ps in groups.values():
                name, hw, x, y, _, _, suburb, town, af, alt_name, old = ps[0]  # the longest piece
                kinds = {q[1] for q in ps}
                hw = next((k for k in ("motorway", "trunk", "primary", "secondary") if k in kinds), hw)
                e = Entry(name, hw, "street", sub_line(street_word(hw), suburb or town), x, y, 0, 0,
                          join_words([af, alt_name, old]), join_words([suburb, town]), True)
                self.entries.append(e)
                self.index.add(e)

    # ------------------------------------------------------------------- Overture

    def overture(self, r):
        name = (r.get("name") or "").strip()
        conf = r.get("conf") or 0.0
        if not name or conf < MIN_CONFIDENCE:
            return
        hier = (r.get("hier") or "").split("/") if r.get("hier") else []
        if any(h in OVERTURE_SKIP for h in hier):
            return
        what = classify_overture(hier, r.get("cat"))
        if norm(name) in self.fuel_brands and what[0] != "fuel":
            what = ("fuel", "fuel", "Fuel station", 1, 9)  # "Engen" filed as a grocery is the Engen garage
        grp, kind, kword, show, rank = what
        if grp == "lodging":
            name = room_free(name)
        name = without_where(name, [r.get("locality")] + list(self.where(r["lon"], r["lat"], name)))
        if len(norm(name)) < 2 or norm(name) in GENERIC_NAMES:
            return
        if conf < SHOW_CONFIDENCE:
            show = 0
        if r.get("brand"):
            rank = min(9, rank + 1)
        if conf >= 0.9:
            rank = min(9, rank + 1)
        x, y = r["lon"], r["lat"]
        street = r.get("street") if norm(r.get("street")) not in ("unnamed road", "none", "") else None
        cplx, suburb, town = self.where(x, y, name)
        sub = sub_line(kword, cplx, suburb or town or r.get("locality"))
        ctx = join_words([cplx, suburb, town, street, r.get("locality"), kword, synonyms(grp, kind, kword)])
        alt = join_words([r.get("brand")]) if r.get("brand") and norm(r["brand"]) not in norm(name) else ""
        self.add(Entry(name, kind, grp, sub, x, y, rank, show, alt, ctx, False))


MIN_CONFIDENCE = 0.5   # Overture's: how sure it is the place exists
SHOW_CONFIDENCE = 0.7  # surer still to be drawn on the map

# Overture categories not worth listing: holiday flats listed room by room.
OVERTURE_SKIP = {"holiday_rental_home", "private_lodging"}
# Overture categories that stay off the map (often a home office), but can be searched.
OVERTURE_QUIET = {"services_and_business", "education", "community_and_government", ""}
OVERTURE_SHOWN = {"atm", "bank", "bank_or_credit_union", "post_office", "car_rental_service", "laundromat",
                  "school", "high_school", "elementary_school", "college_university", "library", "police_station",
                  "fire_department", "hospital", "town_hall"}
OVERTURE_WORDS = {"gas_station": "Fuel station", "fueling_station": "Fuel station", "grocery_store": "Supermarket",
                  "automotive_repair": "Car repair", "auto_dealer": "Car dealer", "atm": "ATM",
                  "bank_or_credit_union": "Bank", "pharmacy_and_drug_store": "Pharmacy",
                  "shopping_mall": "Mall", "college_university": "University", "doctors_office": "Doctor",
                  "dental_clinic": "Dentist", "beer_wine_spirits_store": "Liquor store",
                  "tire_dealer_and_repair": "Tyres", "auto_parts_store": "Car parts",
                  "christian_place_of_worship": "Church", "muslim_place_of_worship": "Mosque",
                  "roman_catholic_place_of_worship": "Church", "anglican_or_episcopal_place_of_worship": "Church",
                  "shopping": "Shop", "food_and_drink": "Food", "lodging": "Accommodation",
                  "health_care": "Health care", "bed_and_breakfast": "B&B"}


def classify_overture(hier, cat):
    """(group, kind, word, show, rank) for an Overture place, from its category's family (top to leaf)."""
    leaf = hier[-1] if hier else (cat or "")
    top = hier[0] if hier else ""
    words = set(hier) | {cat or ""}
    kword = OVERTURE_WORDS.get(leaf) or word(leaf) or "Place"
    show = 0 if (top in OVERTURE_QUIET and leaf not in OVERTURE_SHOWN) or leaf in ("preschool", "day_care_preschool") else 1
    if words & {"gas_station", "fueling_station", "ev_charging_station", "truck_gas_station"}:
        return "fuel", leaf, kword, 1, 9
    if any("motorcycle" in h for h in hier):
        return "moto", leaf, kword, 1, 7
    if top == "food_and_drink":
        return "food", leaf, kword, show, 5
    if top == "lodging":
        return "lodging", leaf, kword, show, 4
    if top == "health_care" or "pharmacy" in leaf:
        return "health", leaf, kword, show, 7 if leaf == "hospital" else 5
    if words & {"atm", "bank", "bank_or_credit_union", "currency_exchange"}:
        return "money", leaf, kword, 1, 4
    if words & {"automotive_repair", "automotive_service", "auto_dealer", "auto_parts_store", "tire_dealer_and_repair",
                "car_wash", "auto_detailing", "auto_body_shop", "used_auto_dealer", "vehicle_parts_store"}:
        return "moto", leaf, kword, show, 5
    if top == "shopping":
        rank = 8 if leaf == "shopping_mall" else 6 if leaf == "grocery_store" else 4
        return "shop", leaf, kword, show, rank
    if top in ("arts_and_entertainment", "cultural_and_historic", "geographic_entities") or leaf in (
            "park", "national_park", "nature_reserve", "hiking_trail", "beach", "winery"):
        sight = top != "cultural_and_historic" or leaf in ("historic_site", "monument", "memorial", "museum")
        return ("sight" if sight else "other"), leaf, kword, show, 5 if sight else 3
    if top == "lifestyle_services":
        return "shop", leaf, kword, show, 3
    return "other", leaf, kword, show, 2


ROOM_WORDS = re.compile(r"\b(room|suite|apartment|studio|bedroom|cottage|chalet|villa|house|unit|tent|cabin|"
                        r"double|twin|single|family|deluxe|standard|superior|king|queen|bed)\b", re.I)


def room_free(name):
    """"Guest House - Deluxe Room (2 Adults)" is the guest house."""
    if " - " in name:
        head, tail = name.split(" - ", 1)
        if ROOM_WORDS.search(tail) and len(norm(head)) >= 3:
            return head.strip()
    return name


REGIONS = {"south africa", "western cape", "eastern cape", "northern cape", "free state", "kwazulu natal", "gauteng",
           "north west", "mpumalanga", "limpopo", "namibia", "botswana", "lesotho", "eswatini", "swaziland", "za",
           "rsa"}


def without_where(name, places):
    """"Harbour Mall, Western Cape, Port Example" is the Harbour Mall: the parts after a comma
    that only say where it is go (the line under the name says that)."""
    parts = [p.strip() for p in name.split(",")]
    if len(parts) < 2:
        return name
    known = set(REGIONS) | {norm(p) for p in places if p}
    keep = [parts[0]] + [p for p in parts[1:] if norm(p) not in known]
    return ", ".join(p for p in keep if p)


def synonyms(grp, kind, kword):
    words = []
    k = (kind or "") + " " + (kword or "")
    k = k.lower()
    if grp == "fuel":
        words.append(SYNONYMS["fuel"])
    for key in ("supermarket", "grocery", "pharmacy", "bank", "atm", "hospital", "clinic", "mall", "coffee",
                "motorcycle", "tyres", "repair", "police"):
        if key in k:
            words.append(SYNONYMS.get(key, key) if key != "grocery" else SYNONYMS["supermarket"])
    if grp == "lodging":
        words.append(SYNONYMS["lodging"])
    return " ".join(words)


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


# --------------------------------------------------------------------------------- write


def morton(x, y):
    """Nearby places get nearby ids, so a part of the map is a few pages of the file."""
    xi = int((x + 180.0) * 100) & 0xFFFF
    yi = int((y + 90.0) * 100) & 0xFFFF
    z = 0
    for i in range(16):
        z |= ((xi >> i) & 1) << (2 * i) | ((yi >> i) & 1) << (2 * i + 1)
    return z


def write(dst, entries, cameras):
    if os.path.exists(dst):
        os.remove(dst)
    db = sqlite3.connect(dst)
    db.executescript(
        """
        PRAGMA page_size = 4096;
        PRAGMA user_version = 2;
        CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, sub TEXT,
                            lat REAL NOT NULL, lon REAL NOT NULL, rank INTEGER NOT NULL DEFAULT 0,
                            grp TEXT NOT NULL DEFAULT 'other', show INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE places_fts USING fts4(content="", matchinfo=fts3, name, alt, ctx);
        CREATE TABLE camera (lat REAL NOT NULL, lon REAL NOT NULL, maxspeed INTEGER NOT NULL DEFAULT 0);
        """
    )
    entries.sort(key=lambda e: (morton(e.x, e.y), -e.rank))
    for i, e in enumerate(entries, 1):
        db.execute("INSERT INTO place (id, name, kind, sub, lat, lon, rank, grp, show) VALUES (?,?,?,?,?,?,?,?,?)",
                   (i, e.name, e.kind or e.grp, e.sub, round(e.y, 6), round(e.x, 6), e.rank, e.grp, e.show))
        db.execute("INSERT INTO places_fts (docid, name, alt, ctx) VALUES (?,?,?,?)",
                   (i, norm(e.name), e.alt, " ".join(t for t in e.ctx.split() if t not in set(toks(e.name)))))
    db.executemany("INSERT INTO camera (lat, lon, maxspeed) VALUES (?,?,?)", cameras)
    db.executescript(
        """
        CREATE INDEX place_map ON place (lat, lon) WHERE show > 0;
        CREATE INDEX camera_lat ON camera (lat);
        INSERT INTO places_fts (places_fts) VALUES ('optimize');
        """
    )
    db.commit()
    db.execute("VACUUM")
    db.close()


def main(src, dst, overture=None):
    where = gather_where(src)
    b = Builder(where)
    for feat in read_seq(src):
        b.osm(feat)
    b.finish_streets()
    osm_count = len(b.entries)
    ov = 0
    if overture and os.path.exists(overture):
        with open(overture, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        rows.sort(key=lambda r: -(r.get("conf") or 0))
        before = len(b.entries)
        for r in rows:
            b.overture(r)
        ov = len(b.entries) - before
    write(dst, b.entries, b.cameras)
    groups = defaultdict(int)
    for e in b.entries:
        groups[e.grp] += 1
    size = os.path.getsize(dst)
    print(f"{dst}: {len(b.entries)} places ({osm_count} from OpenStreetMap, {ov} more from Overture, "
          f"{b.merged} found twice), {len(b.cameras)} cameras, {size / 1e6:.1f} MB")
    print("  " + ", ".join(f"{g} {n}" for g, n in sorted(groups.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main(*sys.argv[1:4])
