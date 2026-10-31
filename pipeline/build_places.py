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
    ("crossfit",    r"crossfit|functional.?training|bootcamp|hyrox|\bhiit\b"),
    ("fitness",     r"(^|[^a-z])gyms?([^a-z]|$)|fitness.?(cent|studio|club)|health.?club|weight.?lift|bodybuild"),
    ("calisthenics",r"calisthenic|outdoor.?gym|fitness.?trail"),
    ("trampolin",   r"trampoline"),
    ("schwimmen",   r"swim|aquatic|public.?bath(?!room)|water.?park|lido|thermal.?bath"),
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
    ("darts",       r"(^|[^a-z])darts?([^a-z]|$)"),
    ("reiten",      r"horse|equestrian|riding.?school"),
    ("eislaufen",   r"ice.?skat|ice.?rink|ice.?hockey|skating.?rink"),
    ("skaten",      r"skate.?park|skateboard|roller|inline"),
    ("fussball",    r"soccer|futsal|football|beach.?soccer"),
    ("football",    r"american.?football"),
    ("basketball",  r"basketball"),
    ("volleyball",  r"volleyball"),
    ("handball",    r"handball"),
    ("hockey",      r"hockey"),
    ("rugby",       r"rugby"),
    ("baseball",    r"baseball|softball"),
    ("cricket",     r"cricket"),
    ("laufen",      r"running|athletic|track.?and.?field|jogging"),
    ("radsport",    r"(?<!re)(?<!re_)cycling|bike.?park(?!ing)|bmx|pump.?track|velodrome|mountain.?bik"),  # nicht: recycling, bike_parking
    ("rudern",      r"rowing"),
    ("kanu",        r"canoe|kayak"),
    ("sup",         r"paddle.?board|stand.?up.?paddl|\bsup\b"),
    ("segeln",      r"sailing|yacht.?club"),
    ("tauchen",     r"scuba|(?<!sky)(?<!sky_)(?<!sky )diving"),  # nicht: skydiving
    ("bogen",       r"archery"),
    ("schiessen",   r"shooting|gun.?club|rifle"),
    ("boule",       r"boule|bocce|petanque|boccia"),
    ("schach",      r"chess"),
    ("discgolf",    r"disc.?golf"),
    # --- Ergänzung Okt. 2026 (siehe neue_sportarten.py) ---
    ("floorball", r"floorball|unihockey"),
    ("ultimate", r"ultimate"),
    ("lacrosse", r"lacrosse"),
    ("faustball", r"faustball|fistball"),
    ("teqball", r"teqball"),
    ("roundnet", r"roundnet|spikeball"),
    ("footgolf", r"foot.?golf"),
    ("pickleball", r"pickleball"),
    ("poledance", r"pole.?danc|pole.?fitness"),
    ("aerial", r"aerial"),
    ("barre", r"\bbarre\b"),
    ("indoorcycling", r"spin.?class|spinning|indoor.?cycl|cycling.?studio|cycling.?class"),
    ("ems", r"\bems\b|electro.?muscle"),
    ("jumping", r"jumping.?fitness"),
    ("aquafitness", r"aqua.?(fit|aerob|gym|cycl)|water.?aerobic"),
    ("turnen", r"gymnastic"),
    ("cheerleading", r"cheerlead"),
    ("akrobatik", r"acrobat|circus.?school"),
    ("taichi", r"tai.?chi|qi.?gong|qigong"),
    ("kraftsport", r"powerlift|weightlift|strongman"),
    ("parkour", r"parkour|ninja|obstacle.?course"),
    ("ringen", r"wrestl"),
    ("mma", r"\bmma\b|mixed.?martial|brazilian.?jiu|\bbjj\b|grappl"),
    ("selbstverteidigung", r"krav|self.?defen|wing.?(chun|tsun)"),
    ("jujutsu", r"ju.?jutsu|ju.?jitsu|(?<!brazilian )jiu.?jitsu"),
    ("wasserball", r"water.?polo"),
    ("surfen", r"(?<!wind)(?<!kite)surf(ing|.?school|.?park|.?club)"),
    ("windkite", r"windsurf|kite.?surf|kite.?board"),
    ("wakeboard", r"wakeboard|water.?ski|cable.?park"),
    ("triathlon", r"triathlon"),
    ("wintersport", r"\bski(ing|.?resort|.?area|.?school|.?hall|.?club)?\b|snowboard|biathlon"),
    ("rodeln", r"toboggan|sled|bobsleigh|luge|alpine.?coaster"),
    ("slackline", r"slackline"),
    ("motorsport", r"\bkart|go.?kart|motocross|race.?track|racetrack|motorsport"),
    ("luftsport", r"paraglid|hang.?glid|gliding|skydiv|parachut|flying.?club|aero.?club|wind.?tunnel"),
    ("curling", r"curling|ice.?stock"),
    ("hundesport", r"dog.?(agility|sport|training)"),
    ("lasertag", r"laser.?tag"),
    ("paintball", r"paintball|airsoft"),
    ("tischfussball", r"foosball|table.?football|table.?soccer"),
    ("cornhole", r"cornhole"),
]
CAT_RULES = [(k, re.compile(rx, re.I)) for k, rx in CAT_RULES]
# Vor dem Prüfen dieser Sportarten werden verwechselbare Begriffe entfernt
STRIP = {
    "tennis": re.compile(r"table.?tennis", re.I),
    "golf": re.compile(r"mini.?golf|miniature.?golf|adventure.?golf|disc.?golf|foot.?golf", re.I),
    "hockey": re.compile(r"ice.?hockey", re.I),
    "fussball": re.compile(r"american.?football|flag.?football|table.?football|table.?soccer|foot.?golf", re.I),
    "radsport": re.compile(r"indoor.?cycl|cycling.?studio|cycling.?class|spin.?class", re.I),
    "fitness": re.compile(r"pole.?fitness|aqua.?fit\w*|jumping.?fitness", re.I),
    "yoga": re.compile(r"aerial.?yoga|acro.?yoga", re.I),
    "kampfsport": re.compile(r"brazilian.?jiu.?jitsu", re.I),
}

# Kategorien, die nach Sport klingen, aber Läden etc. sind -> raus
EXCLUDE = re.compile(r"store|shop|retail|wholesale|supplier|supply|manufactur|repair|rental|dealer|"
                     r"clothing|apparel|equipment|bar\b|pub\b|restaurant|cafe|hotel|school_district|"
                     r"physician|doctor|clinic|hospital|pharmacy|veterinar|tattoo|nail|hair|beauty|"
                     r"real.?estate|insurance|bank|attorney|lawyer|car\b|automotive|gas.?station|"
                     r"recycl|parking|waste|disposal|scrap|contractor|construction|toilet|restroom|climbing_service", re.I)

# Namen, die nie ein Sportangebot sind (Fundstücke aus der Prüfung Okt. 2026)
EXCLUDE_NAME = re.compile(
    r"\badfc\b|fahrrad.?club|recycling|entsorgung|schrott|containerdienst|wertstoff|altmetall|metallhandel|"
    r"abstell|stellplatz|fahrradstation|radstation|fahrradbox|fahrradgarage|fahrradparkhaus|ladestation|ladesäule|"
    r"kletterladen|bergsport.?laden|\bshop\b|decathlon|intersport|sport.?scheck|anlagenbau|"
    r"schreibbüro|kopier|lektorat|poolbau|schwimmbadbau|schwimmbadtechnik|desjoyaux|\bpools? gmbh|sanifair|"
    r"physio|ergotherap|\bpraxis\b|beckenboden|partytreff|party.?arena|diskothek|\bdisco\b|nachtclub|"
    r"klettergerüst|kletterspielplatz|spielplatz", re.I)
# Tanz: "dance_club" ist bei Overture meist eine Disco, "opera_and_ballet" ein Opernhaus
NIGHT_CAT = re.compile(r"dance.?club|night.?club|disco|opera", re.I)
DANCE_NAME = re.compile(r"tanz|dance.?(studio|school|academy|center|centre|company|crew)|ballett|ballet.?school|\btsc\b|\btsa\b|\btsg\b.*tanz|salsa|tango|zumba|hip.?hop|latin", re.I)
# Bäder: "public_bath_house" sind oft Toiletten-/Sanitäranlagen
BATH_NAME = re.compile(r"bad\b|bäder|therme|schwimm|sauna|hallen|frei|see\b|aqua|spa\b|baths?\b", re.I)

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
    ("fitness", r"fitness|\bgym\b"),
    ("floorball", r"floorball|unihockey"),
    ("ultimate", r"ultimate|frisbee"),
    ("lacrosse", r"lacrosse"),
    ("faustball", r"faustball"),
    ("teqball", r"teqball"),
    ("roundnet", r"roundnet|spikeball"),
    ("footgolf", r"footgolf|fußballgolf|fussballgolf"),
    ("pickleball", r"pickleball"),
    ("poledance", r"pole.?dance|pole.?fitness|pole.?sport"),
    ("aerial", r"aerial|luftakrobatik|vertikaltuch"),
    ("barre", r"\bbarre\b"),
    ("indoorcycling", r"spinning|indoor.?cycling|cycle.?studio"),
    ("ems", r"\bems\b|bodystreet|xbody"),
    ("jumping", r"jumping"),
    ("aquafitness", r"aqua.?fit|aquafitness|aqua.?gym|wassergymnastik"),
    ("turnen", r"turn(verein|gemeinde|erbund|erschaft|abteilung|en)|gymnastik"),
    ("cheerleading", r"cheer"),
    ("akrobatik", r"akrobatik|artistik|zirkus"),
    ("taichi", r"tai.?chi|qi.?gong"),
    ("kraftsport", r"gewichtheb|kraftdreikampf|powerlifting|kraftsport"),
    ("parkour", r"parkour|ninja"),
    ("ringen", r"ringer|ringen\b|wrestling"),
    ("mma", r"\bmma\b|bjj|grappling"),
    ("selbstverteidigung", r"krav|selbstverteidigung|wing.?(chun|tsun)"),
    ("jujutsu", r"ju.?jutsu|ju.?jitsu"),
    ("wasserball", r"wasserball"),
    ("surfen", r"surf"),
    ("windkite", r"windsurf|kite"),
    ("wakeboard", r"wasserski|wakeboard"),
    ("triathlon", r"triathlon|tri-?team"),
    ("wintersport", r"ski(club|verein|-club|zunft)|snowboard"),
    ("rodeln", r"rodel|bob(sport|club)"),
    ("slackline", r"slackline"),
    ("motorsport", r"motorsport|\bkart|\bmsc\b|\bamc\b|motocross"),
    ("luftsport", r"luftsport|segelflug|flieger|aero.?club|gleitschirm|paraglid"),
    ("curling", r"curling|eisstock|stockschütz"),
    ("hundesport", r"hundesport|hundeverein|agility|gebrauchshund"),
    ("lasertag", r"laser.?tag"),
    ("paintball", r"paintball|airsoft"),
    ("tischfussball", r"tischfußball|tischfussball|tischkicker"),
    ("cornhole", r"cornhole"),
    # --- Ergänzung Okt. 2026: Sportarten, die Overture nur als "gym"/"sports club" führt ---
    ("padel", r"padel"),
    ("squash", r"squash"),
    ("crossfit", r"cross.?fit|hyrox|functional.?(training|fitness|gym)|\bbox\b.*fit"),
    ("calisthenics", r"calisthenics|street.?workout"),
    ("darts", r"\bdarts?\b|dart.?(club|verein|sport|liga)|steeldart|e-?dart"),
    ("pickleball", r"pickleball"),
]
NAME_RULES = [(k, re.compile(rx, re.I)) for k, rx in NAME_RULES]
APP_SPORTS = {k for k, _ in CAT_RULES}
STUDIO_SPECIALS = {"poledance", "aerial", "barre", "indoorcycling", "ems", "jumping", "aquafitness", "kraftsport", "parkour", "taichi",
                   "crossfit", "calisthenics", "padel", "squash"}

CLUB_CAT = re.compile(r"club|league|association|team|verein|federation", re.I)
CLUB_NAME = re.compile(r"\be\.\s?v\.|verein|\b(tus|sv|tv|sc|djk|bc|ttc|tc|vfl|vfb|spvgg|fc|tsv|ssv|bv|sg|tg|ttv|bsv|esv|htc|thc|sf|ksv|asv|psv|hsv|rsv|tsg)\b", re.I)
FIELD_CAT = re.compile(r"court|field|pitch|skate.?park|outdoor|trail|park\b|playground|beach|lake|bathing|climbing.?spot|crag|climbing.?area|bike.?park", re.I)


def sports_for(cat_text, name):
    cat_text = cat_text or ""
    if EXCLUDE.search(cat_text) or EXCLUDE_NAME.search(name or ""):
        return []
    found = [k for k, rx in CAT_RULES if rx.search(STRIP[k].sub(" ", cat_text) if k in STRIP else cat_text)]
    if not found and GENERIC.search(cat_text):
        found = [k for k, rx in NAME_RULES if rx.search(name or "") and k in APP_SPORTS]
    # Kampfsportschulen: Disziplin aus dem Namen ("Judo Club Essen" -> auch judo)
    if "kampfsport" in found:
        found += [k for k, rx in NAME_RULES if k in ("judo", "karate", "taekwondo", "boxen", "ringen", "mma",
                                                    "selbstverteidigung", "jujutsu") and rx.search(name or "")]
    # Studios mit Spezialangebot, die nur als "Gym"/"Tanz"/"Yoga" einsortiert sind ("Pole Fitness Ruhr")
    if found and set(found) <= {"fitness", "tanzen", "yoga", "pilates", "crossfit"}:
        found += [k for k, rx in NAME_RULES if k in STUDIO_SPECIALS and rx.search(name or "")]
    # Falsch als "gym" einsortiert, Name verrät aber eine andere Sportart ("Schachclub ...")
    if found == ["fitness"]:
        by_name = [k for k, rx in NAME_RULES if k != "fitness" and k in APP_SPORTS and rx.search(name or "")]
        if by_name and not re.search(r"fitness|gym|kraft", name or "", re.I):
            found = by_name
    # Tennis-/Sportanlagen mit Padel/Squash/Pickleball im Namen ("Tennis & Padel Club")
    if found and not EXCLUDE_NAME.search(name or ""):
        found += [k for k, rx in NAME_RULES if k in ("padel", "squash", "pickleball") and rx.search(name or "")]
    # Discos und Opernhäuser sind keine Tanzschulen
    if "tanzen" in found and NIGHT_CAT.search(cat_text) and not DANCE_NAME.search(name or ""):
        found.remove("tanzen")
    # Sanitäranlagen ("public bath house") nur, wenn der Name nach Bad klingt
    if "schwimmen" in found and re.search(r"public.?bath", cat_text, re.I) and not re.search(r"swim|aquatic|water.?park|lido", cat_text, re.I) \
            and not BATH_NAME.search(name or ""):
        found.remove("schwimmen")
    # Yoga-, Pilates-, Tanz- und Kampfsportstudios, die zusätzlich als "gym" geführt werden, sind kein Fitnessstudio
    if "fitness" in found and not re.search(r"fitness|gym|kraft|muckibude", name or "", re.I):
        spec = [k for k, rx in NAME_RULES if k in ("yoga", "pilates", "tanzen", "kampfsport", "boxen", "klettern", "poledance",
                                                   "aerial", "barre", "ems", "taichi", "crossfit", "schwimmen") and rx.search(name or "")]
        if spec:
            found = [k for k in found if k != "fitness"] + spec
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


def main_cat_expr(cols):
    """Nur die genaueste Kategorie (z. B. 'yoga_studio'). Grobe Kategorien wie
    basic_category ('fitness_center' o. Ä.) nur, wenn es keine genauere gibt –
    sonst würden Yoga-Studios und Tanzschulen zusätzlich als Fitnessstudio gelten."""
    order = []
    if "taxonomy" in cols and "primary" in str(cols["taxonomy"]).lower():
        order.append("NULLIF(CAST(taxonomy.primary AS VARCHAR),'')")
    if "categories" in cols and "primary" in str(cols["categories"]).lower():
        order.append("NULLIF(CAST(categories.primary AS VARCHAR),'')")
    if "basic_category" in cols:
        order.append("NULLIF(CAST(basic_category AS VARCHAR),'')")
    if not order:
        return None
    return "COALESCE(" + ", ".join(order) + ", '')"


def read_overture(path, min_conf):
    con = duckdb.connect()
    src = f"read_parquet('{path}')"
    cexpr, cols = cat_expr(con, src)
    mexpr = main_cat_expr(cols) or cexpr
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
      SELECT id, names.primary AS name, {mexpr} AS cat, {lonlat},
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


CAT_STATS = defaultdict(lambda: defaultdict(int))  # Sportart -> Kategorie -> Anzahl (zum Nachschärfen)


def build(records):
    items = []
    for r in records:
        sp = sports_for(r["cat"], r["name"])
        if not sp:
            continue
        for k in sp:
            CAT_STATS[k][f'{r["src"]}: {r["cat"]}'[:120]] += 1
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
    # Auswertung: welche Kategorien landen bei welcher Sportart (Top 40)
    stats = {k: dict(sorted(v.items(), key=lambda x: -x[1])[:40]) for k, v in sorted(CAT_STATS.items())}
    with open(os.path.join(args.out, "kategorien.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print(f"Fertig in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
