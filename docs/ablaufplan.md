# Ablaufplan: Programm „Disc-Digitalisierung“

Stand 2026-11-09. Windows-Programm (C#, .NET 9, WPF) für die DVD- und CD-Digitalisierung.
Das Programm steuert die vorhandenen, erprobten Python-Skripte aus
`skripte/` und zeigt jederzeit, was gerade passiert und was schiefging.

## Ziel

- **Ein Fenster** statt zwei Konsolen: Einlesen, Umwandeln, Einsortieren/Export auf einen Blick.
- **Status jederzeit nachvollziehbar:** aktuelle Disc, Fortschritt, Warteschlange, Stand aller Discs, Export-Ziele.
- **Fehler erkennen:** Fehler und Warnungen hervorgehoben, gefiltert, mit Disc-Bezug. Ein Protokoll je Sitzung als Datei.
- **Fragen im Fenster beantworten** (Lesefehler, Beschriftung, leere Disc …) mit Ton und blinkender Taskleiste.

## Entscheidung: Programm steuert Python (statt Neubau)

Die Python-Logik (ca. 4600 Zeilen) enthält viele am echten Laufwerk gefundene Sonderfälle. Ein Neubau würde sie
alle neu entdecken müssen. Deshalb: C# übernimmt Oberfläche, Prozesssteuerung, Bestand und Netzwerk-Überwachung,
Python bleibt für Laufwerk, Kopieren, Umwandeln und Einsortieren. Die Verbindung ist ein kleines, versioniertes
Ereignis-Protokoll (JSON-Zeilen über stdout/stdin): [ereignis-protokoll.md](ereignis-protokoll.md).
Einzelne Teile lassen sich später nach C# portieren, ohne die Oberfläche zu ändern.

## Ablauf

```
                    ┌──────────────────────── Disc-Digitalisierung (WPF) ─────────────────────────┐
 Disc rein ──►      │  Laufwerk/Einlesen    Warteschlange/Umwandeln   Bestand        Export-Ziele │
                    │  Fortschritt, Fragen  Fortschritt, Zeitfenster  alle Discs     erreichbar,  │
                    │  Fehler               Fehler                    Filter         Platz        │
                    └──────▲──────│──────────────▲──────│──────────────▲─────────────────▲────────┘
                  Ereignisse│     │Antworten,    │      │stopp         │ liest           │ prüft
                  (stdout)  │     ▼stopp (stdin) │      ▼              │                 │
                    einlesen.py ──► 1-roh/ ──► umwandeln.py ──► 2-fertig/ ──► einsortieren auto ──► DVD_ZIEL / MUSIK_ZIEL
                         │                          │                                              (NAS)
                         └──► 3-problem/            └──► fortschritt.csv, disc.json  ◄── Bestand liest
```

1. **Start:** Programm lädt Einstellungen, startet `einlesen.py` und (falls eingestellt) `umwandeln.py` im Ereignis-Modus.
   Läuft `umwandeln.py` schon außerhalb (Sperre `umwandeln.lock`), wird das angezeigt und kein zweites gestartet.
2. **Disc rein:** `einlesen.py` meldet `laufwerk` → `disc` → laufend `fortschritt` → ggf. `frage` → `disc_ende`.
3. **Frage:** Programm zeigt sie oben im Fenster, Ton, Taskleiste blinkt. Antwort geht als `antwort` zurück.
4. **Umwandeln:** `umwandeln.py` meldet `warteschlange`, `disc`, `fortschritt` (HandBrake, Comskip, Prüfung), `disc_ende`.
5. **Einsortieren/Export:** Ergebnis über `disc_ende` (`einsortiert` oder `pruefen`). Der Netzwerk-Monitor prüft
   die Export-Ziele (erreichbar, freier Platz, Antwortzeit) und warnt, bevor das Einsortieren scheitert.
6. **Bestand:** liest `fortschritt.csv` und die Ordner `1-roh`, `2-fertig`, `3-problem` (FileSystemWatcher), zeigt alle
   Discs mit Status, Problemgrund, Lesefehlern.
7. **Protokoll:** Jedes Ereignis wird in `%LOCALAPPDATA%\DiscDigitalisierung\logs\JJJJ-MM-TT_HHMMSS.jsonl` geschrieben.

## Bausteine und Zuständigkeit

| Baustein | Ort | Inhalt | Wer |
|---|---|---|---|
| Kern | `src/DiscDigitalisierung.Kern` | Ereignis-Modelle, Schnittstellen, Einstellungen. **Vertrag, nur nach Absprache ändern** | Hauptsitzung |
| A Python-Ereignisse | `skripte/` | Modul `ereignisse.py`, Ereignis-Modus in `einlesen.py`, `kopieren.py`, `musik_cd.py`, `umwandeln.py` | Subagent A |
| B Prozesse | `src/DiscDigitalisierung.Prozesse`, `tests/…Prozesse.Tests` | Parser, `SkriptProzess`, Fabrik, Sitzungsprotokoll (JSONL) | Subagent B |
| C Bestand & Netzwerk | `src/DiscDigitalisierung.Bestand`, `tests/…Bestand.Tests` | `Bestand` (CSV, disc.json, Sperre, Platz), `NetzwerkMonitor` | Subagent C |
| D Oberfläche | `src/DiscDigitalisierung.App` | WPF, MVVM (CommunityToolkit.Mvvm), Fenster, Fragen, Fehlerliste, Einstellungen | Subagent D |
| Zusammenführen | alles | Bauen, Tests, echter Probelauf mit `--quelle`, Doku | Hauptsitzung |

Die Bausteine B, C, D hängen nur vom Kern ab und laufen parallel. D baut gegen die Schnittstellen und nutzt bis zur
Zusammenführung Attrappen (Fake-Implementierungen) im eigenen Projekt.

## Phasen

1. **Grundgerüst** (erledigt): Repo, Solution, Kern, Protokoll, dieser Plan.
2. **Parallel A–D** (Subagenten, erledigt 2026-11-09): je Baustein bauen und testen, nur in den eigenen Dateien.
3. **Zusammenführen** (erledigt 2026-11-09, 57 Tests grün, App mit `--einstellungen` und Laufwerk `Q:` gestartet): App an echte Implementierungen hängen, Gesamt-Build, Tests, Probelauf mit einer Testquelle
   (`einlesen.py --quelle <ordner>`, kein echtes Laufwerk), Doku in `skripte/README.md`.
4. **Echter Betrieb:** Nutzer startet das Programm statt `start.py` mit einem kleinen Stapel Discs. Rückmeldungen einarbeiten.
5. **Später (nicht in v1):** Jellyfin-Bibliothek nach dem Export aktualisieren (API-Schlüssel aus Passwortmanager/Umgebung),
   Prüfseite und Zuordnung (`einsortieren.py anwenden`) im Programm, Benachrichtigung an Home Assistant,
   Teile nach C# portieren.

## Fertig, wenn (v1)

- Programm startet beide Skripte, zeigt Laufwerk, aktuelle Disc mit Fortschritt, Warteschlange, Bestand, Export-Ziele.
- Alle Fragearten lassen sich im Fenster beantworten; Fragen sind nicht zu übersehen.
- Fehler/Warnungen sind gesammelt sichtbar, mit Disc und Zeit, und stehen im Sitzungsprotokoll.
- Absturz eines Skripts wird angezeigt (mit Exit-Code und letzter stderr-Ausgabe) und automatisch neu gestartet: nach 10 s, 30 s, 60 s; ab dem 4. Absturz in 15 min nicht mehr (`NeustartRegel`), dann Start per Knopf. Ein Stopp durch den Nutzer startet nicht neu.
- Ohne `DVD_EREIGNISSE` laufen die Python-Skripte unverändert wie bisher (`start.py` bleibt nutzbar).

## Regeln für alle

- Nur die eigenen Dateien ändern (siehe Tabelle). Braucht ein Baustein eine Änderung am Kern: im Bericht melden.
- Keine Secrets. Keine fremden Prozesse beenden. Das echte Laufwerk `E:` nicht benutzen (dort läuft der Betrieb).
- Kein `git commit`. Sprache in Code-Bezeichnern, Kommentaren und Oberfläche: Deutsch.
