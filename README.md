# Disc-Digitalisierung

Halbautomatisches Digitalisieren von DVDs und CDs für [Jellyfin](https://jellyfin.org) unter Windows:
Disc einlegen, Nummer auf die Disc schreiben, nächste Disc. Den Rest erledigen die Skripte im Hintergrund.

- **DVDs:** Fernsehaufnahmen von DVD-Rekordern (DVD-Video und DVD-VR, Rekorderdaten von Panasonic-Geräten),
  gekaufte DVD-Video und Daten-DVDs mit Videodateien.
- **Audio-CDs und Hörspiel-CDs:** bitgenau gelesen, mit AccurateRip geprüft, Titeldaten von MusicBrainz, FLAC mit Cover.

Das Projekt besteht aus zwei Teilen:

| Teil | Ordner | Aufgabe |
|---|---|---|
| Python-Skripte | [`skripte/`](skripte/README.md) | Einlesen (eigenes Kopieren über Lesefehler hinweg), Umwandeln mit HandBrake (x265), Werbung markieren mit Comskip, Einsortieren in Jellyfin-Ordner mit NFO-Dateien |
| Windows-Programm | `src/` | WPF-Oberfläche (.NET 9), steuert die Skripte und zeigt alles in einem Fenster |

Die Skripte laufen auch ohne das Programm im Terminal (`python -I skripte/start.py`).

> Screenshot folgt. Einen Eindruck ohne Laufwerk gibt der Demo-Modus: `DiscDigitalisierung.App.exe --demo`.

## Funktionen

- **Einlesen:** Laufwerk beobachten, Disc-Nummer automatisch vergeben (`D0001`, `D0002`, …), Beschriftung aus den
  Rekorderdaten bilden oder abfragen, Titel sektorgenau kopieren, auswerfen, Ton.
- **Lesefehler:** Kopieren nach dem Prinzip von ddrescue: erst alles Lesbare, dann die Fehlerstellen nachlesen, mit
  Rückfrage und Empfehlung (weiter lesen, Lücke lassen, reinigen).
- **Umwandeln:** Warteschlange mit niedriger Priorität, Interlace-Erkennung (25p/50p), HandBrakeCLI x265, Kapitel
  „Werbung“ über Comskip, Prüfung der Laufzeit, Vorschaubilder.
- **Einsortieren:** Folgen über Sender und Sendezeit auf fernsehserien.de finden, sichere Treffer automatisch nach
  `Serien/`, `Filme/` oder `Fernsehaufnahmen/` verschieben, unsichere zur Prüfung liegen lassen (`pruefen.html`).
- **Programm:** Fortschritt, Warteschlange, Bestand aller Discs mit Filter, Fragen im Fenster (mit Ton und
  blinkender Taskleiste), Fehlerliste, Speicherplatz und Erreichbarkeit der Export-Ziele, Sitzungsprotokoll,
  automatischer Neustart eines abgestürzten Skripts.

## Voraussetzungen

- Windows 10/11 (x64) mit einem DVD- oder Blu-ray-Laufwerk
- [Python](https://www.python.org/downloads/) 3.10 oder neuer im `PATH`, für Audio-CDs zusätzlich `numpy`
  (`python -m pip install numpy`)
- [HandBrakeCLI](https://handbrake.fr/downloads2.php), `ffmpeg` und `ffprobe` im `PATH`
- [MKVToolNix](https://mkvtoolnix.download/) (`mkvpropedit`, im `PATH` oder unter `C:\Program Files\MKVToolNix`)
- [Comskip](http://www.kaashoek.com/files/) (im `PATH` oder unter `%LOCALAPPDATA%\Programs\Comskip\comskip.exe`)
- optional [MakeMKV](https://www.makemkv.com/) als Ausweg für DVDs, die das eigene Kopieren nicht lesen kann
  (Standardpfad `C:\Program Files (x86)\MakeMKV\makemkvcon64.exe`, sonst Umgebungsvariable `MAKEMKVCON`)

Mit winget, zum Beispiel:

```bash
winget install Python.Python.3.12 HandBrake.HandBrake.CLI Gyan.FFmpeg MoritzBunkus.MKVToolNix GuinpinSoft.MakeMKV
```

Das Release ist self-contained, eine .NET-Installation ist dafür nicht nötig.

## Installation

1. Zip aus dem [neuesten Release](https://github.com/tobi-blazor/disc-digitizer/releases/latest) entpacken,
   z.B. nach `C:\Programme\DiscDigitalisierung`.
2. `DiscDigitalisierung.App.exe` starten. Die Skripte liegen im Unterordner `skripte\` und werden dort automatisch gefunden.
3. Im Einstellungsfenster Arbeitsordner, Ziele und Laufwerk eintragen.

## Konfiguration

Das Programm speichert seine Einstellungen in `%APPDATA%\DiscDigitalisierung\einstellungen.json`, Sitzungsprotokolle
in `%LOCALAPPDATA%\DiscDigitalisierung\logs\`. Leere Pfade übernimmt es aus den Benutzer-Umgebungsvariablen, die auch
die Skripte allein im Terminal lesen:

| Einstellung | Umgebungsvariable | Bedeutung | Standard |
|---|---|---|---|
| `SkriptOrdner` | – | Ordner mit `einlesen.py` usw. | `skripte\` neben dem Programm bzw. im Repo |
| `PythonPfad` | – | Python-Aufruf | `python` |
| `ArbeitsOrdner` | `DVD_ARBEIT` | Arbeitsordner mit `1-roh`, `2-fertig`, `3-problem`, `fortschritt.csv` (ca. 4 GB je Disc) | `ausgabe\dvd` neben `skripte\` |
| `DvdZiel` | `DVD_ZIEL` | Wurzel der Jellyfin-Bibliotheken für Videos, z.B. `\\nas\medien\dvd` | – (ohne: nur Einlesen und Umwandeln) |
| `MusikZiel` | `MUSIK_ZIEL` | Wurzel der Musikbibliothek | `<ArbeitsOrdner>\musik` |
| `Laufwerk` | `DVD_LAUFWERK` | Laufwerksbuchstabe | `E:` |
| `Nummern` | – | Nummer für leere Discs: `fragen`, `immer`, `nie` | `fragen` |
| `UmwandelnArgumente` | – | zusätzliche Argumente für `umwandeln.py`, z.B. `["--zeitfenster", "22-07"]` | – |

Weitere, nur für die Skripte: `TMDB_API_KEY` (optional, gleicht Folgennummern mit TMDB ab), `CD_OFFSET`
(Laufwerks-Offset für Audio-CDs, wird sonst einmalig über AccurateRip ermittelt), `MAKEMKVCON`.
Keine Schlüssel in `einstellungen.json` oder ins Repo schreiben.

Beispiel `einstellungen.json`:

```json
{
  "ArbeitsOrdner": "D:\\disc-arbeit",
  "DvdZiel": "\\\\nas\\medien\\dvd",
  "MusikZiel": "\\\\nas\\medien\\musik",
  "Laufwerk": "E:"
}
```

Ablauf, Ordnerstruktur, `disc.json` und alle Schalter der Skripte: [skripte/README.md](skripte/README.md).

## Aus dem Quellcode bauen

Benötigt das [.NET 9 SDK](https://dotnet.microsoft.com/download/dotnet/9.0).

```bash
dotnet test DiscDigitalisierung.sln
```

```bash
dotnet run --project src/DiscDigitalisierung.App -- --demo
```

```bash
pwsh ./release.ps1 -Version 0.1.0
```

`release.ps1` legt den self-contained Build samt `skripte\` und das Zip unter `artifacts\` ab.

| Projekt | Inhalt |
|---|---|
| `src/DiscDigitalisierung.Kern` | Ereignis-Modelle, Schnittstellen, Einstellungen |
| `src/DiscDigitalisierung.Prozesse` | Python-Skripte starten, Ereignisse lesen, Antworten senden, Sitzungsprotokoll |
| `src/DiscDigitalisierung.Bestand` | `fortschritt.csv` und Disc-Ordner lesen, Export-Ziele überwachen |
| `src/DiscDigitalisierung.App` | WPF-Oberfläche |
| `tests/…` | xUnit-Tests |

- Ablaufplan: [docs/ablaufplan.md](docs/ablaufplan.md)
- Ereignis-Protokoll Python ↔ C#: [docs/ereignis-protokoll.md](docs/ereignis-protokoll.md)

## Bekannte Grenzen

- Nur Windows; die Skripte lesen das Laufwerk über Windows-Schnittstellen.
- Oberfläche, Ausgaben und Code sind deutsch.
- Kopierschutz (CSS) wird nicht entfernt; geschützte Kauf-DVDs gehen nur über MakeMKV.
- Das Einsortieren ist auf das deutsche Fernsehen zugeschnitten (fernsehserien.de). fernsehserien.de erlaubt die
  Nutzung laut Impressum nur privat; die Skripte rufen langsam ab (mindestens 3 s Pause), cachen 30 Tage und
  übernehmen nur Folgennummern und Titel.
- TMDB-Abgleich ist ungetestet.
- Blu-ray-Discs sind nicht vorgesehen und ungetestet.

## Rechtliches

Nur für eigene Discs und den privaten Gebrauch. Ob du eine Disc kopieren darfst, hängt vom Recht deines Landes ab.

## Lizenz

[MIT](LICENSE)
