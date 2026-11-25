# ridewire-maps

Offline regions for the Ridewire app: map tiles, places to search, fixed speed cameras and
routing tiles, built from OpenStreetMap by GitHub Actions once a month and published as a
release. The app reads the list from
`https://github.com/AverageNattyTrenUser/ridewire-maps/releases/latest/download/manifest.json`.

## What a region holds

| File | What | Built with |
| --- | --- | --- |
| `<id>.pmtiles` | Vector map tiles (OpenMapTiles schema), zoom 0 to 14 | [Planetiler](https://github.com/onthegomap/planetiler) |
| `<id>.places.db` | Towns, fuel, viewpoints, passes and other places to search (SQLite FTS4), and fixed speed cameras | `scripts/places.py` |
| `*.rd5` | Routing tiles, 5 x 5 degrees, shared between regions | Mirrored from [brouter.de](https://brouter.de/brouter/segments4/) |
| `manifest.json` | The list the app reads: regions, file addresses and sizes | `scripts/manifest.py` |

## Adding a region

Add it to `regions.json`: an id, a name, the group it's listed under, its country code (for
the police and camera alert rules), the extract it's cut from (`sources`) and its bounding box
`[west, south, east, north]`. Then run the workflow (Actions › Build map regions › Run
workflow), or wait for the monthly run.

## Licence

Map data © OpenStreetMap contributors, available under the
[Open Database Licence](https://www.openstreetmap.org/copyright). The files in the releases
are derived from it and are offered under the same licence. Tiles use the
[OpenMapTiles](https://openmaptiles.org/) schema (© OpenMapTiles). Routing tiles come from
BRouter. The scripts in this repository are MIT-licensed.
