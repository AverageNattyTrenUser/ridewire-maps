"""Tests for scripts/places.py on a small made-up town. Run: python3 -m unittest discover -s tests"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import places  # noqa: E402


def point(lon, lat, **tags):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": tags}


def square(lon, lat, half, **tags):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": tags}


def line(coords, **tags):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": tags}


# Port Example: the suburb Seaside, a mall with a supermarket, flats, a street in two pieces, fuel and a camera.
OSM = [
    point(20.00, -34.00, place="town", name="Port Example", population="40000"),
    square(20.01, -34.01, 0.02, place="suburb", name="Seaside"),
    square(20.012, -34.012, 0.001, shop="mall", name="Harbour View", building="yes"),
    point(20.0121, -34.0121, shop="supermarket", name="FreshCo", brand="FreshCo", branch="Harbour View", cuisine=None),
    point(20.0122, -34.0119, amenity="restaurant", name="Pizza Planet", cuisine="pizza;italian"),
    square(20.005, -34.005, 0.0002, building="apartments", name="Sunrise Court", **{"addr:housenumber": "7",
                                                                                     "addr:street": "Ocean Road"}),
    point(20.006, -34.006, **{"addr:housenumber": "12", "addr:street": "Ocean Road"}),
    # Ocean Road in three pieces: the two long ends only meet through the short middle one.
    line([[20.000, -34.004], [20.005, -34.004]], highway="residential", name="Ocean Road"),
    line([[20.0085, -34.004], [20.0135, -34.004]], highway="residential", name="Ocean Road"),
    line([[20.005, -34.004], [20.0085, -34.004]], highway="residential", name="Ocean Road"),
    square(20.02, -34.00, 0.01, place="town", name="Port Example"),  # the town's area as well as its centre
    square(20.0151, -34.0151, 0.0001, building="roof", name="Petrol Co"),  # the canopy over the pumps
    point(20.015, -34.015, amenity="fuel", brand="Petrol Co"),
    point(20.016, -34.016, highway="speed_camera", maxspeed="60"),
    point(20.017, -34.017, amenity="parking", name="Harbour View"),
    point(20.018, -34.018, amenity="bench", name="Memorial Bench"),
]

OVERTURE = [
    # The same supermarket as OpenStreetMap's, under Overture's name.
    {"name": "FreshCo Harbour View", "cat": "food_and_beverage_store", "hier": "shopping/food_and_beverage_store/grocery_store",
     "conf": 0.95, "lon": 20.0123, "lat": -34.0122, "street": "Shop 4, Harbour View, Ocean Road", "locality": "Port Example",
     "brand": "FreshCo"},
    {"name": "Corner Cafe", "cat": "cafe", "hier": "food_and_drink/casual_eatery/cafe", "conf": 0.8, "lon": 20.009,
     "lat": -34.009, "street": "3 Beach Lane", "locality": "Port Example", "brand": None},
    {"name": "Bay Lodge - Deluxe Room (2 Adults)", "cat": "hotel", "hier": "lodging/hotel", "conf": 0.9, "lon": 20.02,
     "lat": -34.02, "street": None, "locality": "Port Example", "brand": None},
    {"name": "Sea Breeze 4 - Two-Bedroom Apartment", "cat": "private_lodging",
     "hier": "lodging/private_lodging/holiday_rental_home", "conf": 0.9, "lon": 20.021, "lat": -34.021, "street": None,
     "locality": "Port Example", "brand": None},
    {"name": "Seaside Hardware, Western Cape, Port Example", "cat": "hardware_store",
     "hier": "shopping/specialty_store/hardware_store", "conf": 0.9, "lon": 20.003, "lat": -34.003, "street": None,
     "locality": "Port Example", "brand": None},
    # Overture's idea of what the fuel station is can be wrong: the name and place say it's the same.
    {"name": "Petrol Co", "cat": "grocery_store", "hier": "shopping/food_and_beverage_store/grocery_store",
     "conf": 0.8, "lon": 20.0165, "lat": -34.0155, "street": None, "locality": "Port Example", "brand": None},
    {"name": "Maybe Closed Plumbing", "cat": "home_service", "hier": "services_and_business/home_service", "conf": 0.3,
     "lon": 20.022, "lat": -34.022, "street": None, "locality": "Port Example", "brand": None},
    {"name": "Quiet Accountants", "cat": "accountant", "hier": "services_and_business/financial_service/accountant",
     "conf": 0.6, "lon": 20.023, "lat": -34.023, "street": "1 Main Road", "locality": "Port Example", "brand": None},
]


class PlacesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        src = os.path.join(cls.dir.name, "places.geojsonseq")
        ovt = os.path.join(cls.dir.name, "overture.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for feat in OSM:
                feat["properties"] = {k: v for k, v in feat["properties"].items() if v is not None}
                f.write("\x1e" + json.dumps(feat) + "\n")
        with open(ovt, "w", encoding="utf-8") as f:
            for r in OVERTURE:
                f.write(json.dumps(r) + "\n")
        cls.db_path = os.path.join(cls.dir.name, "places.db")
        places.main(src, cls.db_path, ovt)
        cls.db = sqlite3.connect(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.dir.cleanup()

    def find(self, query):
        """As the app searches: every word as a prefix."""
        match = " ".join(t + "*" for t in places.toks(query))
        return self.db.execute(
            "SELECT p.name, p.grp, p.sub, p.show FROM places_fts f JOIN place p ON p.id = f.docid "
            "WHERE places_fts MATCH ? ORDER BY p.rank DESC", (match,)).fetchall()

    def test_format_two(self):
        self.assertEqual(2, self.db.execute("PRAGMA user_version").fetchone()[0])

    def test_a_shop_in_a_mall_is_found_by_the_malls_name_and_listed_once(self):
        got = self.find("freshco harbour")
        self.assertEqual(1, len(got), got)
        name, grp, sub, show = got[0]
        self.assertEqual("FreshCo Harbour View", name)
        self.assertEqual("shop", grp)
        self.assertEqual("Supermarket · Harbour View · Seaside", sub)
        self.assertEqual(1, show)

    def test_a_named_building_is_found_and_drawn_close_up(self):
        got = self.find("sunrise court")
        self.assertEqual([("Sunrise Court", "building", "Apartments · 7 Ocean Road · Seaside", 2)], got)

    def test_addresses_and_streets(self):
        self.assertEqual([("12 Ocean Road", "address", "Address · Seaside", 0)], self.find("12 ocean"))
        streets = [g for g in self.find("ocean road") if g[1] == "street"]
        self.assertEqual([("Ocean Road", "street", "Street · Seaside", 0)], streets)  # one, however it's cut up

    def test_what_a_place_is_finds_it(self):
        self.assertIn("Pizza Planet", [g[0] for g in self.find("pizza")])
        fuel = self.find("petrol")
        self.assertEqual(["Petrol Co"], [g[0] for g in fuel])
        self.assertEqual("fuel", fuel[0][1])

    def test_towns_once_at_their_centre(self):
        got = [g for g in self.find("port example") if g[0] == "Port Example"]
        self.assertEqual([("Port Example", "town", "Town", 0)], got)
        lat, lon = self.db.execute("SELECT lat, lon FROM place WHERE name = 'Port Example'").fetchone()
        self.assertEqual((-34.0, 20.0), (lat, lon))
        self.assertEqual(("Seaside", "town", "Suburb · Port Example", 0), self.find("seaside")[0])

    def test_the_same_place_from_both_lists_once(self):
        got = self.find("petrol co")
        self.assertEqual([("Petrol Co", "fuel", "Fuel station · Seaside", 1)], got)

    def test_overture_names_without_where_they_are(self):
        self.assertEqual("Seaside Hardware", self.find("seaside hardware")[0][0])

    def test_overture_extras(self):
        self.assertEqual([("Corner Cafe", "food", "Cafe · Seaside", 1)], self.find("corner cafe"))
        self.assertEqual("Bay Lodge", self.find("bay lodge")[0][0])  # the room taken off the name
        self.assertEqual([], self.find("sea breeze"))  # holiday flats aren't listed
        self.assertEqual([], self.find("maybe closed"))  # too unsure
        self.assertEqual(0, self.find("quiet accountants")[0][3])  # found, but not drawn

    def test_skipped(self):
        self.assertEqual([], [g for g in self.find("harbour view") if g[1] == "other"])  # the car park
        self.assertEqual([], self.find("memorial bench"))

    def test_cameras(self):
        self.assertEqual([(-34.016, 20.016, 60)], self.db.execute("SELECT lat, lon, maxspeed FROM camera").fetchall())

    def test_the_map_index(self):
        plan = " ".join(r[3] for r in self.db.execute(
            "EXPLAIN QUERY PLAN SELECT name FROM place WHERE show > 0 AND show <= 2 AND lat BETWEEN -35 AND -33 "
            "AND lon BETWEEN 19 AND 21 ORDER BY rank DESC LIMIT 800"))
        self.assertIn("place_map", plan)

    def test_words(self):
        self.assertEqual("cafe roux s", places.norm("Café Roux's"))
        self.assertEqual("Bay Lodge", places.room_free("Bay Lodge - Deluxe Room (2 Adults)"))
        self.assertEqual("Dune - Bar", places.room_free("Dune - Bar"))
        self.assertTrue(places.same_place("FreshCo", "harbour view seaside", "FreshCo Harbour View", ""))
        self.assertFalse(places.same_place("FreshCo", "harbour view", "FreshCo Liquor Harbour View", ""))


if __name__ == "__main__":
    unittest.main()
