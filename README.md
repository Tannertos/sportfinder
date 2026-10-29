# Sportfinder

Eine Web-App, in der man eine Sportart wählt (Basketball, Yoga, Boxen …) und alle Möglichkeiten in der Nähe sieht: Vereine, freie Plätze, Studios und Hallen. Sie funktioniert in ganz Deutschland.

## Woher die Daten kommen (alles kostenlos)

| Quelle | Was | Wie |
|---|---|---|
| OpenStreetMap | Plätze, Hallen, Bäder, teils Vereine | live bei jeder Suche |
| Overture Maps Places | Studios, Vereine, Hallen (viel aus Facebook-Seiten) | monatlich als Kacheln |
| Foursquare OS Places | Studios und Sportstätten | monatlich, optional |

Zur Ortssuche nutzt die App Nominatim (OpenStreetMap). Die Routen öffnen sich in Google Maps oder Apple Karten.

## Einmalig einrichten (ca. 15 Minuten)

1. Lege ein kostenloses Konto auf github.com an und erstelle ein neues Repository, z. B. `sportfinder`.
2. Lade den Inhalt dieses Ordners hoch: auf github.com über **Add file → Upload files**, einfach alles hineinziehen, auch den Ordner `.github`.
3. Gehe zu **Settings → Pages** und stelle bei **Source** „GitHub Actions“ ein.
4. Optional für Foursquare: Lege ein kostenloses Konto auf huggingface.co an, öffne die Seite `foursquare/fsq-os-places` und akzeptiere dort die Bedingungen. Erstelle dann unter **Settings → Access Tokens** einen Token mit Leserechten. In GitHub trägst du ihn unter **Settings → Secrets and variables → Actions → New repository secret** ein, mit dem Namen `HF_TOKEN`.
5. Unter **Actions → „Daten-Update & Veröffentlichung“ → Run workflow** startest du den ersten Lauf. Er dauert etwa 15–40 Minuten.
6. Danach ist die App erreichbar unter `https://DEIN-NAME.github.io/sportfinder/`.

Ab dann aktualisieren sich die Daten jeden Monat automatisch.

## Ordner

- `site/index.html`: die App
- `site/data/`: die erzeugten Kacheln (entstehen beim ersten Lauf)
- `pipeline/build_places.py`: der Datenimport
- `.github/workflows/daten-update.yml`: der monatliche Ablauf

## Lizenzhinweise

- Kartendaten: © OpenStreetMap-Mitwirkende (ODbL). Sie werden live abgefragt und nicht mit den Kacheln vermischt gespeichert.
- Orte: © Overture Maps Foundation (CDLA Permissive 2.0), Foursquare OS Places (Apache 2.0)
- Nominatim und die öffentlichen Overpass-Server sind für kleine Nutzerzahlen gedacht. Wenn die App wächst, sollten wir eigene Server oder Bezahldienste einplanen, ab etwa 20 €/Monat.
