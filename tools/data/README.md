# tools/data

## tur_boundary.json: Türkiye geofence

**TR** · `gt.py validate` bu dosyayla Türkiye kara toprakları, iç suları ve 12 deniz mili içindeki koordinatları yakalar (Türk kuvvetleri kapısı).
**EN** · `gt.py validate` uses this file to catch coordinates on Türkiye's land, in its internal waters, or within 12 nautical miles of its coast (Turkish forces gate).

- **Source:** [Natural Earth](https://www.naturalearthdata.com/) 1:50m Admin 0 – Countries, as packaged in
  [world-atlas](https://github.com/topojson/world-atlas) `countries-50m.json` (Türkiye = feature id `792`), taken from the copy vendored in
  [Greater-Turkiye/platform](https://github.com/Greater-Turkiye/platform/blob/b8e3af32eaab3a7f4eeb541e61696b2a2d6ddaab/apps/web/assets/data/countries-50m.json) (commit `b8e3af3`).
- **License:** Natural Earth data is **public domain** ([terms of use](https://www.naturalearthdata.com/about/terms-of-use/)).
  No attribution is required; we credit it anyway.
- **Regenerate:** `python tools/data/make_tur_boundary.py path/to/countries-50m.json`

### Contents / İçerik

| key | what |
|---|---|
| `polygons` | Türkiye's land rings, plain `[lon, lat]` (Anatolia, Thrace, Gökçeada), ~520 points |
| `internal_waters` | hand-drawn box over the Sea of Marmara and the Straits (its centre is > 12 nm from both coasts) |
| `coast` / `border` | the same outline split into coastline and land-border lines. A TopoJSON arc shared with a neighbour is a land border; every other arc is coast |
| `foreign_land` | neighbours' land rings (Greece, Bulgaria, Georgia, Syria) near the Turkish coast, clipped to that area, ~175 points |

Simplification is Douglas–Peucker per arc: `coast_tolerance_km` = 1 km and `border_tolerance_km` = 0.25 km. The validator widens its
buffers by these tolerances, so the simplified outline never under-flags compared with the Natural Earth line.

### What the check flags / Neyi yakalar

A point is tested in this order:

1. inside a Turkish land ring or the Marmara box → **flagged**
2. within 0.25 km of a **land border** → **flagged** (simplification slack only; there is no 12 nm buffer on land, so north Syria and Iraq stay in scope)
3. on another state's land (e.g. Lesbos, Samos, Alexandroupoli) → **not flagged**: the 12 nm buffer is sea
4. within 12 nm + 1 km (≈ 23.2 km) of the Turkish **coast** → **flagged** (territorial sea)

For a Polygon geometry, the check tests every vertex, a point every 5 km along each edge, and whether the polygon encloses a Turkish ring.

Distances use a local equirectangular projection centred on the tested point (`x = Δlon·cos(lat)·111.195 km`, `y = Δlat·111.195 km`).
Over ~25 km this is well under 1% off the great-circle distance, much smaller than the error of the 1:50m generalisation itself.

### Limitations / Sınırlamalar

- 12 nm is applied on every coast, including the Aegean, where Türkiye's territorial sea is 6 nm. Narrow straits between Greek islands and
  Anatolia are flagged entirely; there is no median line.
- Islands missing from the 1:50m data (e.g. Kastellorizo/Meis, Bozcaada) are treated as sea, so points there are flagged if near the Turkish coast.
- Neighbouring coastal points within about 1 km of their own simplified coastline (e.g. central Batumi) can fall "in the sea" and be flagged.
- Border towns within about 1 km of the line (e.g. Tal Abyad, Kobani) are flagged. Record them without `geometry`, at `admin1` precision.
