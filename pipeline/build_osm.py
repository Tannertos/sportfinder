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


def center(geom):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geom["coordinates"])
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonseq")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tiles = defaultdict(list)
    n_in = n_out = 0
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
            sp = sports_of(t)
            if not sp:
                continue
            if t.get("access") in ("private", "no") and not t.get("name") and not t.get("operator"):
                continue  # private Schul-/Hinterhofplätze ohne Infos
            if t.get("leisure") == "swimming_pool" and not t.get("name"):
                continue  # Gartenpools
            try:
                lat, lon = center(feat["geometry"])
            except Exception:
                continue
            w, s, e, n = BBOX
            if not (s <= lat <= n and w <= lon <= e):
                continue
            tags = {k: t[k] for k in KEEP if k in t}
            oid = str(feat.get("id") or "")
            tiles[f"{math.floor(lat / TILE)}_{math.floor(lon / TILE)}"].append(
                [round(lat, 6), round(lon, 6), sp, oid, tags])
            n_out += 1
    os.makedirs(os.path.join(a.out, "tiles"), exist_ok=True)
    for k, rows in tiles.items():
        with open(os.path.join(a.out, "tiles", f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    idx = {"updated": date.today().isoformat(), "tile": TILE, "count": n_out,
           "tiles": {k: len(v) for k, v in sorted(tiles.items())},
           "attribution": "© OpenStreetMap-Mitwirkende (ODbL)"}
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"OSM: {n_in} Objekte gelesen, {n_out} Sportorte in {len(tiles)} Kacheln", flush=True)


if __name__ == "__main__":
    main()
