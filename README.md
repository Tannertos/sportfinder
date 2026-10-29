# Sportfinder

Eine Web-App, in der man eine Sportart wählt (Basketball, Yoga, Boxen …) und alle Möglichkeiten in der Nähe sieht: Vereine, freie Plätze, Studios und Hallen. Sie funktioniert in ganz Deutschland.

**Live:** https://tannertos.github.io/sportfinder/

## Woher die Daten kommen (alles kostenlos)

| Quelle | Was | Aktualisierung |
|---|---|---|
| OpenStreetMap (Geofabrik-Deutschlandpaket) | Plätze, Hallen, Bäder, teils Vereine | monatlich |
| Overture Maps Places | Studios, Vereine, Hallen (viel aus Facebook-Seiten) | monatlich |
| Foursquare OS Places | Studios und Sportstätten | monatlich, optional |

Die Daten liegen als Kacheln (je 0,5° × 0,5°) neben der App. Die App lädt nur die Kacheln rund um den gesuchten Ort. Für die Ortssuche nutzt sie Nominatim (OpenStreetMap). Routen öffnen sich in Google Maps oder Apple Karten.

## Wie es läuft

- Ein GitHub-Workflow (`.github/workflows/daten-update.yml`) läuft jeden Monat am 2., um 03:17 UTC. Er läuft außerdem, sobald sich etwas in `site/` ändert. Von Hand startest du ihn unter **Actions → „Daten-Update & Veröffentlichung“ → Run workflow**.
- Ein Lauf dauert etwa 7 Minuten. Danach ist die App mit den neuen Daten online.

## Optional: Foursquare zusätzlich nutzen

1. Lege ein kostenloses Konto auf huggingface.co an, öffne `foursquare/fsq-os-places` und akzeptiere die Bedingungen.
2. Erstelle unter **Settings → Access Tokens** einen Token mit Leserechten.
3. Trage ihn in GitHub ein: **Settings → Secrets and variables → Actions → New repository secret**, Name `HF_TOKEN`.

## Ordner

- `site/index.html`: die App
- `site/data/`: Overture-/Foursquare-Kacheln, dazu `kategorien.json` (Auswertung zum Nachschärfen der Zuordnung)
- `site/data/osm/`: OpenStreetMap-Kacheln
- `pipeline/build_places.py`: Import Overture/Foursquare
- `pipeline/build_osm.py`: Import OpenStreetMap

## Lizenzhinweise

- OpenStreetMap-Daten: © OpenStreetMap-Mitwirkende, ODbL (eigene Kacheln, getrennt gespeichert)
- Orte: © Overture Maps Foundation (CDLA Permissive 2.0), Foursquare OS Places (Apache 2.0)
- Nominatim ist für kleine Nutzerzahlen gedacht. Wenn die App wächst, sollten wir einen eigenen oder bezahlten Geocoder einplanen.
