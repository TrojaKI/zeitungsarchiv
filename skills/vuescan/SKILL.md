---
name: vuescan-config
description: >
  Generiere und optimiere VueScan INI-Konfigurationsdateien für den
  CanoScan 8800F, speziell für Zeitungsartikel-Scans. Verwende diesen
  Skill immer wenn VueScan, vuescan.ini, Scanner-Konfiguration oder
  Scan-Qualität für das Zeitungsarchiv erwähnt wird.
---

# Skill: VueScan INI Konfiguration

## Ziel

Erstelle eine optimale `vuescan.ini` für Zeitungsartikel-Scans mit dem
CanoScan 8800F unter Linux und macOS.

## Optimale Parameter für Zeitungsartikel

```
Auflösung:   400 DPI   — OCR-optimal; 300 möglich, 600 unnötig groß
Farbe:       Graustufen — Zeitungstext braucht kein RGB
Bit-Tiefe:   8-bit     — ausreichend, 16-bit verdoppelt Dateigröße
Format:      TIFF      — verlustfreier Master für beste OCR-Qualität
Descreen:    ein       — entfernt Druckraster (Moiré-Muster)
```

## INI-Template generieren

```ini
; vuescan.ini — CanoScan 8800F, Zeitungsartikel
; Generiert für Projekt: zeitungsarchiv
; Pfad Linux:  ~/.vuescan/vuescan.ini
; Pfad macOS:  ~/Library/VueScan/vuescan.ini

[Scanner]
scanner-vendor=Canon
scanner-model=CanoScan 8800F
scanner-type=flatbed

[Input]
input-resolution=400
input-color=gray
input-bit-depth=8
input-size=a4
input-multi-scan=no

[Output]
output-tiff=yes
output-tiff-compression=none
output-tiff-filename={INBOX_DIR}/{date}-{time}.tif
output-jpeg=no
output-pdf=no
output-auto-rotate=yes
output-overwrite=no

[Filter]
filter-descreen=yes
filter-descreen-lines=133
filter-sharpen=medium
filter-grain-reduction=light
filter-auto-level=no
```

## Installationspfade

| System | Pfad |
|--------|------|
| Linux  | `~/.vuescan/vuescan.ini` |
| macOS  | `~/Library/VueScan/vuescan.ini` |

## Schritt-für-Schritt

1. INI generieren mit korrektem `output-tiff-filename`-Pfad
2. Pfad an Betriebssystem anpassen (Linux vs. macOS)
3. `{INBOX_DIR}` durch tatsächlichen Inbox-Pfad ersetzen
4. VueScan-Platzhalter `{date}` und `{time}` beibehalten
5. Nutzer anweisen: VueScan beenden → INI kopieren → VueScan starten
6. Ersten Test-Scan durchführen, Qualität prüfen

## Qualitätsprüfung

Nach dem ersten Scan prüfen:
- Dateigröße: A4 Graustufen 400 DPI TIFF ≈ 15–25 MB (plausibel?)
- Dateiname: enthält Datum+Uhrzeit?
- Moiré-Muster sichtbar? → filter-descreen-lines erhöhen (150, 175)
- Zu dunkel/hell? → filter-auto-level=yes testen

## Typische Fehler

| Problem | Lösung |
|---------|--------|
| VueScan ignoriert INI | Pfad prüfen; VueScan muss nach INI-Änderung neu starten |
| Dateiname ohne Datum | VueScan-Platzhalter `{date}` korrekt? |
| Moiré-Raster im Bild | filter-descreen-lines auf 150 erhöhen |
| Datei zu groß (>40MB) | input-resolution auf 300 reduzieren |
