# ridewire-maps

Offline regions for the Ridewire app: map tiles, places to search and show on the map, fixed
speed cameras and routing tiles, built by GitHub Actions once a month and published as a
release. The app reads the list from
`https://github.com/AverageNattyTrenUser/ridewire-maps/releases/latest/download/manifest.json`.

## What a region holds

| File | What | Built with |
| --- | --- | --- |
| `<id>.pmtiles` | Vector map tiles (OpenMapTiles schema), zoom 0 to 14 | [Planetiler](https://github.com/onthegomap/planetiler) |
| `<id>.places.db` | Places to search and show on the map (SQLite, FTS4), and fixed speed cameras | `scripts/places.py` |
| `*.rd5` | Routing tiles, 5 x 5 degrees, shared between regions | Mirrored from [brouter.de](https://brouter.de/brouter/segments4/) |
| `manifest.json` | The list the app reads: regions, file addresses, sizes and SHA-256s (an update on the phone skips files it already has) | `scripts/manifest.py` |

### The place list

`places.db` holds what the app searches offline and draws on the map when the region's tiles
are in use:

- From OpenStreetMap: towns and suburbs, every named shop, business and other place, named
  buildings, estates, malls and business parks, streets (one per suburb), addresses, fuel
  stations and fixed speed cameras.
- From [Overture Maps](https://overturemaps.org) places (`scripts/overture.py`): business
  listings from Meta, Microsoft, Foursquare, AllThePlaces and others that Overture is at least
  50% sure exist and that aren't marked closed. A place in both is listed once.

Each place has the line the rider reads under its name ("Supermarket · the mall · the
suburb"), an icon group, how much it counts, and whether the map shows it (shops and
businesses from zoom 14.5; Overture's only when it's at least 70% sure; named buildings from
zoom 17). The search index stores the words the way the app searches them (lower case, no
accents) and also finds a place by the mall it's in, its street and suburb, and what it is
("pizza", "petrol"). See the top of `scripts/places.py` for the format.

Tests: `python3 -m unittest discover -s tests` (they run before every build).

## Adding a region

Add it to `regions.json`: an id, a name, the group it's listed under, its country code (for
the police and camera alert rules), the extract it's cut from (`sources`) and its bounding box
`[west, south, east, north]`. Then run the workflow (Actions › Build map regions › Run
workflow), or wait for the monthly run.

A run by hand can build only some regions (`regions`), skip the map tiles (`tiles` off, much
quicker when only the place lists changed) and skip publishing (`publish` off: the files stay
on the run as an artifact for five days). Whatever a published run didn't build is copied from
the latest release.

## Licence

Map data © OpenStreetMap contributors, available under the
[Open Database Licence](https://www.openstreetmap.org/copyright). The files in the releases
are derived from it and are offered under the same licence. Tiles use the
[OpenMapTiles](https://openmaptiles.org/) schema (© OpenMapTiles). Routing tiles come from
BRouter.

Places also come from the [Overture Maps Foundation](https://overturemaps.org), under the
[Community Data License Agreement – Permissive 2.0](https://cdla.dev/permissive-2-0/),
including data from Foursquare under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) (Copyright 2024 Foursquare
Labs, Inc.) and from AllThePlaces (CC0 1.0). Only places whose every source is under one of
these licences are used.

The scripts in this repository are MIT-licensed.
