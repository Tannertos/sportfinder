#!/usr/bin/env python3
"""
Sportfinder – OpenStreetMap-Sportorte für ganz Deutschland als Kacheln.

Ersetzt die Live-Abfrage der (oft überlasteten) öffentlichen Overpass-Server.
Eingabe ist eine mit osmium vorgefilterte GeoJSON-Sequenz (siehe Workflow):

  osmium tags-filter germany-latest.osm.pbf <Filter> -o sport.osm.pbf
  osmium export sport.osm.pbf -f geojsonseq --add-unique-id=type_id -o sport.geojsonseq
  python build_osm.py sport.geojsonseq --out site/data/osm

Lizenz der Ausgabe: © OpenStreetMap-Mitwirkende, ODbL. Bewusst getrennt von den
Overture/Foursquare-Kacheln gespeichert.
"""
import argparse, json, math, os, re, sys
from collections import defaultdict
from datetime import date

TILE = 0.5
BBOX = (5.86, 47.27, 15.05, 55.06)

# App-Sportart -> (Werte des OSM-Tags sport, Zusatzfilter {tag: wert|None}, Namensmuster)
SPORTS = {
    "basketball": ("basketball", [], None),
    "fussball": ("soccer", [], None),
    "volleyball": ("volleyball|beachvolleyball", [], None),
    "handball": ("handball", [], None),
    "hockey": ("field_hockey|hockey", [], None),
    "rugby": ("rugby|rugby_union|rugby_league", [], None),
    "football": ("american_football", [], None),
    "baseball": ("baseball|softball", [], None),
    "cricket": ("cricket", [], None),
    "tennis": ("tennis", [], None),
    "tischtennis": ("table_tennis", [], None),
    "badminton": ("badminton", [], None),
    "padel": ("padel", [], None),
    "squash": ("squash", [], None),
    "fitness": ("fitness|weightlifting|bodybuilding", [{"leisure": "fitness_centre"}], None),
    "yoga": ("yoga", [], "yoga"),
    "pilates": ("pilates", [], "pilates"),
    "crossfit": ("crossfit", [], "crossfit|functional"),
    "calisthenics": ("calisthenics|fitness_station", [{"leisure": "fitness_station"}], None),
    "tanzen": ("dance|ballet", [{"amenity": "dancing_school"}, {"leisure": "dance"}],
               "tanzschule|tanzstudio|dance studio|ballettschule|zumba"),
    "trampolin": ("trampoline", [{"leisure": "trampoline_park"}], None),
    "kampfsport": ("martial_arts|karate|judo|taekwondo|aikido|jiu-jitsu|ju-jitsu|kickboxing|boxing|wrestling|mma|"
                   "kung_fu|krav_maga|muay_thai|wing_chun|capoeira", [], "kampfsport|martial arts|dojo"),
    "boxen": ("boxing|kickboxing|muay_thai", [], "boxen|boxing|boxclub|boxgym"),
    "judo": ("judo", [], None),
    "karate": ("karate", [], None),
    "taekwondo": ("taekwondo", [], None),
    "fechten": ("fencing", [], None),
    "schwimmen": ("swimming", [{"leisure": "swimming_pool", "name": None}, {"leisure": "water_park"},
                               {"leisure": "bathing_place"}], None),
    "rudern": ("rowing", [], None),
    "kanu": ("canoe|kayak", [], None),
    "sup": ("stand_up_paddleboarding|paddle_boarding|sup", [], None),
    "segeln": ("sailing", [], None),
    "tauchen": ("scuba_diving|diving|free_diving", [], None),
    "laufen": ("running|athletics|jogging", [], None),
    "radsport": ("cycling|bmx|mtb|pumptrack", [], None),
    "skaten": ("skateboard|roller_skating|inline_skates|scooter", [], None),
    "klettern": ("climbing|bouldering|climbing_adventure", [], "boulder|kletter"),
    "golf": ("golf", [{"leisure": "golf_course"}], None),
    "minigolf": ("miniature_golf", [{"leisure": "miniature_golf"}], None),
    "discgolf": ("disc_golf", [], None),
    "reiten": ("equestrian", [{"leisure": "horse_riding"}], None),
    "eislaufen": ("ice_skating|ice_hockey", [{"leisure": "ice_rink"}], None),
    "bogen": ("archery", [], None),
    "schiessen": ("shooting", [], None),
    "bowling": ("bowling|10pin|9pin", [{"leisure": "bowling_alley"}], None),
    "boule": ("boules|petanque|bocce", [], None),
    "billard": ("billiards", [], None),
    "darts": ("darts", [], None),
    "schach": ("chess", [], None),
}
SPORT_VAL = {}
for key, (vals, _, _) in SPORTS.items():
    for v in vals.split("|"):
        SPORT_VAL.setdefault(v, set()).add(key)
NAME_RX = {k: re.compile(n, re.I) for k, (_, _, n) in SPORTS.items() if n}

KEEP = ["name", "operator", "leisure", "amenity", "club", "access", "fee", "surface", "lit", "hoops", "covered",
        "indoor", "addr:street", "addr:housenumber", "addr:postcode", "addr:city", "opening_hours", "phone",
        "contact:phone", "website", "contact:website", "url"]


def sports_of(t):
    found = set()
    for v in re.split(r"\s*;\s*", t.get("sport", "")):
        found |= SPORT_VAL.get(v, set())
    for key, (_, extras, _) in SPORTS.items():
        for f in extras:
            if all((t.get(k) is not None) if v is None else (t.get(k) == v) for k, v in f.items()):
                found.add(key)
        if key in NAME_RX and t.get("name") and (t.get("leisure") or t.get("amenity") or t.get("club")):
            if NAME_RX[key].search(t["name"]):
                found.add(key)
    return sorted(found)


def rings(geom):
    """Alle Außenringe eines (Multi-)Polygons, sonst leer."""
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Polygon":
        return [c[0]]
    if t == "MultiPolygon":
        return [p[0] for p in c]
    return []


def all_points(geom):
    out = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            out.append((c[0], c[1]))
        else:
            for x in c:
                walk(x)
    walk(geom["coordinates"])
    return out


def center(geom):
    pts = all_points(geom)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def inside(lon, lat, ring):
    """Punkt-in-Polygon (Ray Casting)."""
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            hit = not hit
        j = i
    return hit


def dist_m(lat1, lon1, lat2, lon2):
    dy = (lat1 - lat2) * 111320
    dx = (lon1 - lon2) * 111320 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


# Umfeld, in dem ein namenloser Platz liegen kann (Priorität: kleiner = wichtiger)
def context_kind(t):
    if t.get("club") or t.get("leisure") in ("sports_centre", "sports_hall", "stadium"):
        return "anlage", 0
    if t.get("amenity") in ("school", "college", "university"):
        return "schule", 1
    if t.get("leisure") == "playground":
        return "spielplatz", 2
    if t.get("leisure") in ("park", "recreation_ground", "garden"):
        return "park", 3
    return None, None


CELL = 0.01      # Raster für Umfeld-Polygone (~1 km)
SCELL = 0.0015   # Raster für Straßen (~150 m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonseq")
    ap.add_argument("--streets", help="GeoJSON-Sequenz benannter Straßen (optional)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    places = []                     # [lat, lon, sports, oid, tags]
    ctx_grid = defaultdict(list)    # Zelle -> [(prio, fläche, art, name, ringe, bbox)]
    club_grid = defaultdict(list)   # Zelle -> [(lat, lon, sports, name)] benannte Sportorte/Vereine
    n_in = 0
    w, s, e, n = BBOX
    with open(a.geojsonseq, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            n_in += 1
            try:
                feat = json.loads(line)
            except ValueError:
                continue
            t = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            # 1) Umfeld-Polygone merken
            kind, prio = context_kind(t)
            rs = rings(geom) if kind else []
            if rs:
                xs = [p[0] for r in rs for p in r]
                ys = [p[1] for r in rs for p in r]
                bb = (min(xs), min(ys), max(xs), max(ys))
                area = (bb[2] - bb[0]) * (bb[3] - bb[1])
                cells = [(cx, cy) for cx in range(math.floor(bb[0] / CELL), math.floor(bb[2] / CELL) + 1)
                         for cy in range(math.floor(bb[1] / CELL), math.floor(bb[3] / CELL) + 1)]
                if len(cells) <= 400:
                    item = (prio, area, kind, t.get("name") or "", rs, bb)
                    for c in cells:
                        ctx_grid[c].append(item)
            sp = sports_of(t)
            if not sp:
                continue
            if t.get("access") in ("private", "no") and not t.get("name") and not t.get("operator"):
                continue  # private Hinterhofplätze ohne Infos
            if t.get("leisure") == "swimming_pool" and not t.get("name"):
                continue  # Gartenpools
            try:
                lat, lon = center(geom)
            except Exception:
                continue
            if not (s <= lat <= n and w <= lon <= e):
                continue
            tags = {k: t[k] for k in KEEP if k in t}
            places.append([round(lat, 6), round(lon, 6), sp, str(feat.get("id") or ""), tags])
            nm = t.get("name") or t.get("operator")
            if nm:
                club_grid[(math.floor(lat / 0.005), math.floor(lon / 0.005))].append((lat, lon, set(sp), nm))

    # 2) Für namenlose Plätze: Umfeld + Verein in der Nähe
    unnamed = [p for p in places if not p[4].get("name") and not p[4].get("operator")]
    n_ctx = n_club = 0
    for p in unnamed:
        lat, lon, sp, tags = p[0], p[1], set(p[2]), p[4]
        best = None
        for (prio, area, kind, name, rs, bb) in ctx_grid.get((math.floor(lon / CELL), math.floor(lat / CELL)), []):
            if not (bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]):
                continue
            if any(inside(lon, lat, r) for r in rs):
                key = (prio, 0 if name else 1, area)
                if best is None or key < best[0]:
                    best = (key, kind, name)
        if best:
            tags["_ctxkind"] = best[1]
            if best[2]:
                tags["_ctx"] = best[2]
            n_ctx += 1
        near = None
        cy, cx = math.floor(lat / 0.005), math.floor(lon / 0.005)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for (la, lo, sps, nm) in club_grid.get((cy + dy, cx + dx), []):
                    if sps & sp:
                        d = dist_m(lat, lon, la, lo)
                        if d <= 250 and (near is None or d < near[0]):
                            near = (d, nm)
        if near:
            tags["_club"] = near[1]
            n_club += 1

    # 3) Nächste benannte Straße (nur für namenlose Plätze)
    n_street = 0
    if a.streets and os.path.exists(a.streets):
        need = defaultdict(list)
        for i, p in enumerate(unnamed):
            cy, cx = math.floor(p[0] / SCELL), math.floor(p[1] / SCELL)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    need[(cy + dy, cx + dx)].append(i)
        best = {}
        with open(a.streets, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                try:
                    feat = json.loads(line)
                except ValueError:
                    continue
                nm = (feat.get("properties") or {}).get("name")
                if not nm:
                    continue
                try:
                    pts = all_points(feat["geometry"])
                except Exception:
                    continue
                # Segmente auf ~40 m verdichten, damit lange Straßen nicht durchrutschen
                dense = []
                for (x1, y1), (x2, y2) in zip(pts, pts[1:] or pts):
                    steps = max(1, int(dist_m(y1, x1, y2, x2) / 40))
                    for k in range(steps):
                        dense.append((x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps))
                dense.append(pts[-1])
                for (x, y) in dense:
                    for i in need.get((math.floor(y / SCELL), math.floor(x / SCELL)), ()):
                        d = dist_m(unnamed[i][0], unnamed[i][1], y, x)
                        if d <= 200 and (i not in best or d < best[i][0]):
                            best[i] = (d, nm)
        for i, (d, nm) in best.items():
            unnamed[i][4]["_street"] = nm
            n_street += 1

    tiles = defaultdict(list)
    for p in places:
        tiles[f"{math.floor(p[0] / TILE)}_{math.floor(p[1] / TILE)}"].append(p)
    os.makedirs(os.path.join(a.out, "tiles"), exist_ok=True)
    for k, rows in tiles.items():
        with open(os.path.join(a.out, "tiles", f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    idx = {"updated": date.today().isoformat(), "tile": TILE, "count": len(places),
           "tiles": {k: len(v) for k, v in sorted(tiles.items())},
           "attribution": "© OpenStreetMap-Mitwirkende (ODbL)"}
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"OSM: {n_in} Objekte gelesen, {len(places)} Sportorte in {len(tiles)} Kacheln; "
          f"namenlos {len(unnamed)}: Umfeld {n_ctx}, Verein {n_club}, Straße {n_street}", flush=True)


if __name__ == "__main__":
    main()
