#!/usr/bin/env python3
"""
Sportfinder – Datenimport für ganz Deutschland.

Holt Sport-relevante Orte (Studios, Vereine, Hallen, Plätze) aus zwei offenen,
kostenlosen Datensätzen und schreibt sie als kleine JSON-Kacheln, die die App lädt:

  1. Overture Maps Places  (CDLA Permissive 2.0 / Apache 2.0)  – immer
  2. Foursquare OS Places  (Apache 2.0)                        – optional, braucht HF_TOKEN

OpenStreetMap wird NICHT hier verarbeitet – die App fragt OSM live ab. So bleiben
die Datenbestände getrennt (keine ODbL-Pflichten für diese Kacheln).

Aufruf:
  python build_places.py --out ../site/data                    # lädt Overture selbst
  python build_places.py --overture places_de.parquet --out ... # vorhandene Datei nutzen
  HF_TOKEN=... python build_places.py --out ...                 # inkl. Foursquare
"""
import argparse, gzip, json, math, os, re, struct, subprocess, sys, time, unicodedata
from collections import defaultdict
from datetime import date

import duckdb

# Deutschland (grob, mit etwas Rand)
BBOX = (5.86, 47.27, 15.05, 55.06)  # west, south, east, north
TILE = 0.5                           # Kachelgröße in Grad (~55 x 35 km)

# --------------------------------------------------------------------------
# Kategorien -> Sportarten der App. Wird auf Kategorie-Text (englisch, aus
# Overture/Foursquare) angewendet. Reihenfolge egal, ein Ort kann mehrere haben.
# --------------------------------------------------------------------------
CAT_RULES = [
    ("yoga",        r"yoga"),
    ("pilates",     r"pilates"),
    ("tanzen",      r"danc|ballet|ballroom|zumba"),
    ("boxen",       r"boxing|kickbox|muay"),
    ("judo",        r"judo"),
    ("karate",      r"karate"),
    ("taekwondo",   r"taekwondo"),
    ("kampfsport",  r"martial|karate|judo|taekwondo|aikido|jiu|ju_?jutsu|kickbox|boxing|mma|kung|krav|self.?defen|wing.?chun|capoeira|muay"),
    ("fechten",     r"fencing"),
    ("crossfit",    r"crossfit|functional.?training|bootcamp"),
    ("fitness",     r"\bgym\b|gyms\b|fitness|health.?club|weight.?lift|bodybuild|personal.?train"),
    ("calisthenics",r"calisthenic|outdoor.?gym|fitness.?trail"),
    ("trampolin",   r"trampoline"),
    ("schwimmen",   r"swim|aquatic|public.?bath|water.?park|lido|thermal.?bath"),
    ("klettern",    r"climb|boulder|rope.?course|high.?ropes"),
    ("tischtennis", r"table.?tennis|ping.?pong"),
    ("tennis",      r"tennis"),
    ("badminton",   r"badminton"),
    ("squash",      r"squash"),
    ("padel",       r"padel"),
    ("minigolf",    r"mini.?golf|miniature.?golf|adventure.?golf|putt"),
    ("golf",        r"golf"),
    ("bowling",     r"bowling|kegel|skittle"),
    ("billard",     r"billiard|pool.?hall|snooker"),
    ("darts",       r"\bdarts?\b"),
    ("reiten",      r"horse|equestrian|riding.?school|stable"),
    ("eislaufen",   r"ice.?skat|ice.?rink|ice.?hockey|skating.?rink"),
    ("skaten",      r"skate.?park|skateboard|roller|inline"),
    ("fussball",    r"soccer|futsal|football"),
    ("football",    r"american.?football"),
    ("basketball",  r"basketball"),
    ("volleyball",  r"volleyball"),
    ("handball",    r"handball"),
    ("hockey",      r"hockey"),
    ("rugby",       r"rugby"),
    ("baseball",    r"baseball|softball"),
    ("cricket",     r"cricket"),
    ("laufen",      r"running|athletic|track.?and.?field|jogging"),
    ("radsport",    r"cycling|bike.?park|bmx|pump.?track|velodrome|mountain.?bik"),
    ("rudern",      r"rowing"),
    ("kanu",        r"canoe|kayak"),
    ("sup",         r"paddle.?board|stand.?up.?paddl|\bsup\b"),
    ("segeln",      r"sailing|yacht.?club"),
    ("tauchen",     r"scuba|diving"),
    ("bogen",       r"archery"),
    ("schiessen",   r"shooting|gun.?club|rifle"),
    ("boule",       r"boule|bocce|petanque|boccia"),
    ("schach",      r"chess"),
    ("discgolf",    r"disc.?golf|frisbee"),
]
CAT_RULES = [(k, re.compile(rx, re.I)) for k, rx in CAT_RULES]
# Vor dem Prüfen dieser Sportarten werden verwechselbare Begriffe entfernt
STRIP = {
    "tennis": re.compile(r"table.?tennis", re.I),
    "golf": re.compile(r"mini.?golf|miniature.?golf|adventure.?golf|disc.?golf", re.I),
    "hockey": re.compile(r"ice.?hockey", re.I),
    "fussball": re.compile(r"american.?football", re.I),
}

# Kategorien, die nach Sport klingen, aber Läden etc. sind -> raus
EXCLUDE = re.compile(r"store|shop|retail|wholesale|supplier|supply|manufactur|repair|rental|dealer|"
                     r"clothing|apparel|equipment|bar\b|pub\b|restaurant|cafe|hotel|school_district|"
                     r"physician|doctor|clinic|hospital|pharmacy|veterinar|tattoo|nail|hair|beauty|"
                     r"real.?estate|insurance|bank|attorney|lawyer|car\b|automotive|gas.?station", re.I)

# Allgemeine Sport-Kategorien: Sportart dann aus dem Namen erraten
GENERIC = re.compile(r"sports?.?club|sports?.?(and|&).?recreation|sports?.?centre|sports?.?center|"
                     r"recreation.?cent|stadium|arena|sports?.?complex|sports?.?field|athletic.?club|"
                     r"gymnasium|sport", re.I)
NAME_RULES = [
    ("fussball", r"fußball|fussball|\bfc\b|\bsv\b.*\b(19|20)\d\d|kicker"),
    ("tennis", r"(?<!tisch)tennis|\btc\b|\bthc\b"),
    ("tischtennis", r"tischtennis|\bttc\b|\bttv\b|\btt-?"),
    ("handball", r"handball|\bhc\b|\bhsg\b"),
    ("basketball", r"basketball|\bbbc\b|baskets"),
    ("volleyball", r"volleyball|\bvc\b"),
    ("hockey", r"hockey|\bhtc\b"),
    ("badminton", r"badminton|\bbc\b"),
    ("schwimmen", r"schwimm|hallenbad|freibad|\bbad\b|therme|aquapark"),
    ("tanzen", r"tanz|ballett|dance"),
    ("kampfsport", r"karate|judo|taekwondo|kampfsport|kickbox|boxen|boxclub|jiu|aikido|kung|dojo"),
    ("boxen", r"boxen|boxclub|boxing|kickbox"),
    ("judo", r"judo"), ("karate", r"karate"), ("taekwondo", r"taekwondo"),
    ("yoga", r"yoga"), ("pilates", r"pilates"),
    ("klettern", r"kletter|boulder"),
    ("reiten", r"reit|pferd|reiter"),
    ("schiessen", r"schütze|schuetze|schieß|schiess"),
    ("rudern", r"ruder"), ("kanu", r"kanu|kajak"), ("segeln", r"segel|yacht"),
    ("golf", r"(?<!mini)golf"), ("minigolf", r"minigolf"),
    ("kegeln", r"kegel"), ("bowling", r"bowling|kegel"),
    ("schach", r"schach"), ("fechten", r"fecht"), ("radsport", r"radsport|\brv\b|radfahrer|bmx"),
    ("laufen", r"leichtathletik|lauftreff|\blg\b"),
    ("fitness", r"fitness|\bgym\b|kraftsport"),
]
NAME_RULES = [(k, re.compile(rx, re.I)) for k, rx in NAME_RULES]
APP_SPORTS = {k for k, _ in CAT_RULES}

CLUB_CAT = re.compile(r"club|league|association|team|verein|federation", re.I)
CLUB_NAME = re.compile(r"\be\.\s?v\.|verein|\b(tus|sv|tv|sc|djk|bc|ttc|tc|vfl|vfb|spvgg|fc|tsv|ssv|bv|sg|tg|ttv|bsv|esv|htc|thc|sf|ksv|asv|psv|hsv|rsv|tsg)\b", re.I)
FIELD_CAT = re.compile(r"court|field|pitch|skate.?park|outdoor|trail|park\b|playground|beach|lake|bathing", re.I)


def sports_for(cat_text, name):
    cat_text = cat_text or ""
    if EXCLUDE.search(cat_text):
        return []
    found = [k for k, rx in CAT_RULES if rx.search(STRIP[k].sub(" ", cat_text) if k in STRIP else cat_text)]
    if not found and GENERIC.search(cat_text):
        found = [k for k, rx in NAME_RULES if rx.search(name or "") and k in APP_SPORTS]
    # Kampfsportschulen: Disziplin aus dem Namen ("Judo Club Essen" -> auch judo)
    if "kampfsport" in found:
        found += [k for k, rx in NAME_RULES if k in ("judo", "karate", "taekwondo", "boxen") and rx.search(name or "")]
    # "Climbing Gym" o. Ä. ist kein Fitnessstudio
    if "fitness" in found and len(found) > 1 and not re.search(r"fitness|health.?club|weight|bodybuild", cat_text, re.I):
        found.remove("fitness")
    return sorted(set(found))


def type_for(cat_text, name):
    if CLUB_CAT.search(cat_text or "") or CLUB_NAME.search(name or ""):
        return "verein"
    if FIELD_CAT.search(cat_text or ""):
        return "platz"
    return "buchbar"


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\b(e\.?\s?v\.?|gmbh|ug|ohg|kg|studio|essen|berlin)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def wkb_point(b):
    """Liest Länge/Breite aus einem WKB-Punkt (ohne Spatial-Extension)."""
    if b is None:
        return None
    b = bytes(b)
    bo = "<" if b[0] == 1 else ">"
    gtype = struct.unpack(bo + "I", b[1:5])[0]
    off = 5
    if gtype & 0x20000000:  # EWKB mit SRID
        off += 4
    x, y = struct.unpack(bo + "dd", b[off:off + 16])
    return x, y


def first(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


# --------------------------------------------------------------------------
# Quelle 1: Overture Maps
# --------------------------------------------------------------------------
def download_overture(path):
    """Lädt den Deutschland-Ausschnitt mit dem offiziellen overturemaps-Tool (neueste Version)."""
    w, s, e, n = BBOX
    cmd = ["overturemaps", "download", f"--bbox={w},{s},{e},{n}", "-f", "geoparquet", "--type=place", "-o", path]
    print("> " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def cat_expr(con, src):
    """Baut einen Kategorie-Text aus den Spalten, die es in dieser Overture-Version gibt."""
    cols = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    parts = []
    if "basic_category" in cols:
        parts.append("CAST(basic_category AS VARCHAR)")
    if "taxonomy" in cols:
        parts.append("CAST(taxonomy AS VARCHAR)")
    if "categories" in cols:
        parts.append("CAST(categories AS VARCHAR)")
    if not parts:
        raise SystemExit("Keine Kategorie-Spalte gefunden – hat sich das Overture-Schema geändert?")
    return " || ' ' || ".join(f"COALESCE({p},'')" for p in parts), cols


def read_overture(path, min_conf):
    con = duckdb.connect()
    src = f"read_parquet('{path}')"
    cexpr, cols = cat_expr(con, src)
    # Grober Vorfilter in DuckDB (schnell). Bewusst mit Wortteilen, die nicht in
    # Allerweltswörtern stecken ("ice" wäre auch in "office"/"service").
    broad = "|".join(["yoga", "pilates", "danc", "ballet", "boxing", "kickbox", "martial", "karate", "judo",
                      "taekwondo", "aikido", "jiu", "fencing", "gym", "fitness", "crossfit", "bootcamp", "trampolin",
                      "swim", "aquatic", "public_bath", "water_park", "climb", "boulder", "tennis", "badminton", "squash",
                      "padel", "golf", "bowling", "billiard", "snooker", "darts", "equestrian", "horse_riding",
                      "riding_school", "ice_rink", "ice_skat", "hockey", "skate", "roller", "soccer", "football",
                      "basketball", "volleyball", "handball", "rugby", "baseball", "softball", "cricket", "running",
                      "athletic", "cycling", "bike_park", "bmx", "pump_track", "rowing", "canoe", "kayak", "paddle",
                      "sailing", "diving", "scuba", "archery", "shooting_range", "gun_club", "boule", "bocce",
                      "petanque", "chess", "disc_golf", "sports_club", "sport_club", "sports_center", "sports_centre",
                      "sports_complex", "sports_field", "sports_and_recreation", "stadium", "arena", "recreation_center", "calisthenic"])
    conf = f"AND COALESCE(confidence,1) >= {min_conf}" if "confidence" in cols else ""
    has_bbox = "bbox" in cols
    lonlat = "bbox.xmin AS lon, bbox.ymin AS lat, NULL AS geom" if has_bbox else "NULL AS lon, NULL AS lat, geometry AS geom"
    addr = "addresses" if "addresses" in cols else "NULL"
    q = f"""
      SELECT id, names.primary AS name, {cexpr} AS cat, {lonlat},
             websites, phones, {addr} AS addresses, socials
      FROM {src}
      WHERE regexp_matches(lower({cexpr}), '{broad}') {conf}
    """
    if "socials" not in cols:
        q = q.replace("socials\n", "NULL AS socials\n")
    out = []
    for row in con.execute(q).fetchall():
        oid, name, cat, lon, lat, geom, webs, phones, addrs, socials = row
        if lon is None and geom is not None:
            lon, lat = wkb_point(geom)
        if lon is None or not name:
            continue
        a = first(addrs)
        addr_s = None
        if isinstance(a, dict):
            addr_s = ", ".join(x for x in [a.get("freeform"), " ".join(x for x in [a.get("postcode"), a.get("locality")] if x)] if x) or None
            if a.get("country") and a.get("country") != "DE":
                continue
        out.append(dict(name=name, cat=cat, lat=lat, lon=lon, web=first(webs) or first(socials),
                        tel=first(phones), addr=addr_s, src="ov"))
    print(f"Overture: {len(out)} Kandidaten", flush=True)
    return out


# --------------------------------------------------------------------------
# Quelle 2: Foursquare OS Places (Hugging Face, kostenloser Account + Token)
# --------------------------------------------------------------------------
def read_foursquare(local_glob=None):
    con = duckdb.connect()
    if local_glob:
        src = f"read_parquet('{local_glob}')"
    else:
        token = os.environ.get("HF_TOKEN")
        if not token:
            print("Foursquare: übersprungen (kein HF_TOKEN gesetzt)", flush=True)
            return []
        from huggingface_hub import list_repo_files
        files = list_repo_files("foursquare/fsq-os-places", repo_type="dataset", token=token)
        dts = sorted({m.group(1) for f in files if (m := re.search(r"release/dt=([\d-]+)/places/", f))})
        if not dts:
            print("Foursquare: keine Release gefunden – übersprungen", flush=True)
            return []
        dt = dts[-1]
        print(f"Foursquare: Release {dt}", flush=True)
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(f"CREATE SECRET hf (TYPE HUGGINGFACE, TOKEN '{token}')")
        src = f"read_parquet('hf://datasets/foursquare/fsq-os-places/release/dt={dt}/places/parquet/*.parquet')"
    w, s, e, n = BBOX
    q = f"""
      SELECT name, fsq_category_labels AS cat, latitude, longitude,
             website, tel, address, postcode, locality, instagram, facebook_id
      FROM {src}
      WHERE country = 'DE' AND date_closed IS NULL
        AND latitude BETWEEN {s} AND {n} AND longitude BETWEEN {w} AND {e}
        AND regexp_matches(lower(array_to_string(fsq_category_labels, ' ')),
            'sport|recreation|gym|studio|yoga|pilates|danc|martial|box|swim|pool|climb|tennis|golf|bowling|skat|court|field|stadium|club')
    """
    out = []
    for name, cat, lat, lon, web, tel, adr, pc, loc, insta, fb in con.execute(q).fetchall():
        if not name or lat is None:
            continue
        # nur die genaueste Stufe nutzen ("... > Gym and Studio > Yoga Studio" -> "Yoga Studio")
        cat = " | ".join(str(l).split(">")[-1].strip() for l in (cat or []))
        web = web or (f"https://instagram.com/{insta}" if insta else None) or (f"https://facebook.com/{fb}" if fb else None)
        addr_s = ", ".join(x for x in [adr, " ".join(x for x in [pc, loc] if x)] if x) or None
        out.append(dict(name=name, cat=cat, lat=lat, lon=lon, web=web, tel=tel, addr=addr_s, src="fsq"))
    print(f"Foursquare: {len(out)} Kandidaten", flush=True)
    return out


# --------------------------------------------------------------------------
# Zusammenführen + Kacheln schreiben
# --------------------------------------------------------------------------
def dist_m(a, b):
    dy = (a["lat"] - b["lat"]) * 111320
    dx = (a["lon"] - b["lon"]) * 111320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def build(records):
    items = []
    for r in records:
        sp = sports_for(r["cat"], r["name"])
        if not sp:
            continue
        r["s"] = sp
        r["t"] = type_for(r["cat"], r["name"])
        r["k"] = norm(r["name"])
        items.append(r)
    # Duplikate: gleicher (normalisierter) Name im Umkreis von 150 m
    grid = defaultdict(list)
    merged = []
    for r in items:
        gk = (round(r["lat"] * 500), round(r["lon"] * 300))  # ~200 m Zellen
        dup = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for o in grid.get((gk[0] + dy, gk[1] + dx), []):
                    if o["k"] and o["k"] == r["k"] and dist_m(o, r) < 150:
                        dup = o; break
                if dup: break
            if dup: break
        if dup:
            dup["s"] = sorted(set(dup["s"]) | set(r["s"]))
            for f in ("web", "tel", "addr"):
                dup[f] = dup.get(f) or r.get(f)
            if r["src"] not in dup["src"]:
                dup["src"] += "," + r["src"]
            if dup["t"] != "verein" and r["t"] == "verein":
                dup["t"] = "verein"
            continue
        grid[gk].append(r)
        merged.append(r)
    print(f"Nach Zuordnung: {len(items)}, nach Duplikat-Entfernung: {len(merged)}", flush=True)
    return merged


def tile_key(lat, lon):
    return f"{math.floor(lat / TILE)}_{math.floor(lon / TILE)}"


def write_tiles(items, out_dir):
    os.makedirs(os.path.join(out_dir, "tiles"), exist_ok=True)
    tiles = defaultdict(list)
    for r in items:
        w, s, e, n = BBOX
        if not (s <= r["lat"] <= n and w <= r["lon"] <= e):
            continue
        # kompakt: [name, lat, lon, sportarten, typ, web, tel, adresse, quelle]
        tiles[tile_key(r["lat"], r["lon"])].append([
            r["name"], round(r["lat"], 6), round(r["lon"], 6), r["s"], r["t"],
            r.get("web"), r.get("tel"), r.get("addr"), r["src"]])
    for k, rows in tiles.items():
        with open(os.path.join(out_dir, "tiles", f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    index = {"updated": date.today().isoformat(), "tile": TILE, "count": sum(len(v) for v in tiles.values()),
             "tiles": {k: len(v) for k, v in sorted(tiles.items())},
             "attribution": "Orte: © Overture Maps Foundation (CDLA Permissive 2.0) · Foursquare OS Places (Apache 2.0)"}
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"{index['count']} Orte in {len(tiles)} Kacheln geschrieben -> {out_dir}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Zielordner, z. B. site/data")
    ap.add_argument("--overture", help="Vorhandene Overture-Datei (GeoParquet); sonst wird geladen")
    ap.add_argument("--fsq", help="Lokale Foursquare-Parquet-Dateien (Glob), sonst Hugging Face")
    ap.add_argument("--min-confidence", type=float, default=0.5)
    args = ap.parse_args()
    t0 = time.time()
    ov_path = args.overture or "places_de.parquet"
    if not args.overture:
        download_overture(ov_path)
    recs = read_overture(ov_path, args.min_confidence)
    try:
        recs += read_foursquare(args.fsq)
    except Exception as ex:  # Foursquare ist optional – Fehler stoppen den Lauf nicht
        print(f"Foursquare: Fehler, übersprungen ({ex})", file=sys.stderr)
    write_tiles(build(recs), args.out)
    print(f"Fertig in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
