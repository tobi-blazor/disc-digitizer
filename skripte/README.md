# Python-Skripte

Die Skripte lesen DVDs (Fernsehaufnahmen, DVD-Video, DVD-VR) und Audio-CDs ein, wandeln sie um und sortieren sie in Jellyfin-Bibliotheken ein. Sie laufen allein im Terminal (`start.py`) oder gesteuert vom Programm Disc-Digitalisierung (siehe [README im Hauptordner](../README.md)). Entwickelt und getestet mit einem Blu-ray-Laufwerk LG BH16NS55.

## Ablauf

```
start.py
 ├─ Disc rein ─► einlesen.py ─► 1-roh/<disc>/              (Hauptfenster, Ton + Auswerfen)
 └─ umwandeln.py ─► 2-fertig/<disc>/ ─► einsortieren.py auto ─┬─► DVD_ZIEL         (alle Titel sicher)
    (eigenes Fenster, Warteschlange)                          └─► bleibt zum Prüfen (pruefen.html, zuordnung.csv)
```

| Teil | Aufgabe |
|---|---|
| [start.py](start.py) | Startet alles: Übersicht, `umwandeln.py` im eigenen Fenster, `einlesen.py` im Vordergrund |
| [dvd_vr.py](dvd_vr.py) | DVD-Video-Struktur lesen (Titel, Zellen, Dauer), doppelte/leere Titel aussortieren, Rekorderdaten aus `VIDEO_RM.IFO` (Panasonic), Titel sektorgenau kopieren |
| [kopieren.py](kopieren.py) | Kopieren über Lesefehler hinweg (für `dvd_vr.py` und Daten-DVDs): Fehlerstellen sektorweise retten, nach 2 min im Terminal fragen (weiter versuchen, überspringen, immer überspringen, abbrechen), Disc herausgenommen = Abbruch |
| [gemeinsam.py](gemeinsam.py) | Ordner, `disc.json` lesen/schreiben, `fortschritt.csv` mit Dateisperre |
| [einlesen.py](einlesen.py) | Laufwerk beobachten (alle 3 s), Disc-Nummer automatisch, Beschriftung bei Rekorderdaten automatisch, sonst abfragen, DVD-Video mit `dvd_vr.py` kopieren (MakeMKV nur als Ausweg), Daten-DVDs samt Begleitdateien kopieren, Menübild, auswerfen, Ton |
| [umwandeln.py](umwandeln.py) | Warteschlange mit niedriger Priorität: Interlace-Messung (25p/50p), HandBrakeCLI x265, Comskip, Kapitel „Werbung“, Prüfung, Vorschaubilder, Rohdaten löschen, danach `einsortieren.py auto`. Daten-Discs mit progressivem H.264/HEVC/AV1/VP9 (z.B. MP4 aus dem Netz) werden nur nach MKV umgepackt, ohne Neukodieren und ohne Comskip |
| [musik_cd.py](musik_cd.py) | Musik-CDs (von `einlesen.py` erkannt, nur `TrackNN.cda`): liest mit `cd_leser.py`, Offset einmalig über AccurateRip, Album direkt nach `MUSIK_ZIEL`. Ohne MusicBrainz-Treffer fragt es im Terminal nach der Benennung, bevor die CD ausgeworfen wird. Mit Lesefehlern oder übersprungener Abfrage nach `3-problem/`, nachträglich benennen mit `--benennen <disc>` |
| [cd_leser.py](cd_leser.py) | Audio-CD direkt über Windows lesen (`IOCTL_CDROM_RAW_READ`, ohne Adminrechte), AccurateRip CRC v1/v2, Offset-Suche über Frame 450, MusicBrainz-Disc-ID, Cover Art Archive, FLAC mit Tags über ffmpeg. Ohne AccurateRip-Treffer wird jeder Titel gelesen, bis zwei Lesungen gleich sind (höchstens 5). Braucht numpy. Test: `python -I skripte/cd_leser.py E: --offset` |
| [ereignisse.py](ereignisse.py) | Ereignis-Modus für das Programm Disc-Digitalisierung (nur mit `DVD_EREIGNISSE=1`): Ereignisse als `@@ `-JSON-Zeilen auf stdout, Fragen und `stopp` über stdin. Ohne die Variable ohne Wirkung (`frage()` ruft `input()` auf) |
| [comskip.ini](comskip.ini) | Comskip-Einstellungen für `umwandeln.py` (nur EDL-Ausgabe) |
| [einsortieren.py](einsortieren.py) | Folge über Sender und Sendezeit auf fernsehserien.de finden, Prüfseite und `zuordnung.csv` je Disc. Sichere Discs automatisch (`auto`), sonst nach Bestätigung umbenennen, NFO schreiben, nach `DVD_ZIEL` verschieben |

## Bedienung

Ein Befehl startet alles:

```bash
python -I skripte/start.py
```

1. Das Skript zeigt den Stand und welche Discs auf eine Prüfung warten. Danach öffnet es ein zweites Fenster „DVD umwandeln“.
2. Disc einlegen. Panasonic-Discs laufen ohne Eingabe durch:
   - Das Skript vergibt die nächste Nummer und zeigt sie groß an (`>>> D0003 <<<`). Die Nummer mit Stift auf die Disc schreiben.
   - Die Beschriftung bildet es aus Sendung und Datum, z.B. „Wissen macht Ah! 2011-09-26 bis 2011-10-03“.
   - Bei anderen Discs fragt es nur nach der Beschriftung (Enter übernimmt das Disc-Label, `s` überspringt). Die Nummer vergibt es auch hier selbst (seit 2026-11-05).
3. Nach ca. 5 min wirft das Skript die Disc aus und spielt einen Ton: drei steigende Töne heißen ok, drei tiefe heißen Problem. Dann die nächste Disc einlegen.
4. Im zweiten Fenster wandelt das Skript Disc für Disc um, ca. 3× schneller als die Laufzeit. Danach ruft es `einsortieren.py auto` auf:
   - **Alle Titel sicher zugeordnet:** Die Disc wandert ohne Rückfrage nach `DVD_ZIEL`. Bei doppelten Folgen bleibt die längere Aufnahme.
   - **Sonst:** Die Disc bleibt in `2-fertig/<disc>/` zum Prüfen liegen, mit dem Hinweis „prüfen: …“ in `fortschritt.csv`.
5. Unsichere Discs gelegentlich prüfen: `2-fertig/<disc>/pruefen.html` ansehen, `zuordnung.csv` korrigieren und in der Spalte `ok` ein `x` setzen. Danach:

```bash
python -I skripte/einsortieren.py anwenden D0001 --ausfuehren
```

Ohne `--ausfuehren` zeigt `anwenden` nur, was passieren würde. Den Stand aller Discs zeigen `einsortieren.py liste --alle` und `gemeinsam.py`.

Beenden:
- Strg+C im Hauptfenster beendet nur das Einlesen. Das Umwandeln-Fenster arbeitet die Warteschlange weiter ab.
- Strg+C im Umwandeln-Fenster unterbricht das Umwandeln. Beim nächsten Start geht es mit dem unterbrochenen Titel weiter.

Weitere Schalter:
- `start.py`: `--zeitfenster 22-07` (Umwandeln nur nachts), `--fragen` (immer nach Nummer und Beschriftung fragen), `--ohne-umwandeln`, `--nummern fragen|immer|nie` (leere Discs, siehe unten)
- `einlesen.py`: `--einmal`, `--ohne-auswerfen`, `--fragen`, `--disc D0001 --beschriftung "…"`, `--quelle <ordner>`
- `umwandeln.py`: `--einmal`, `--disc`, `--encoder x265|vce_h265`, `--rf`, `--preset`, `--schwelle 0.30`, `--prioritaet unter-normal|niedrig`, `--werbung-immer`, `--ohne-einsortieren`
- `einsortieren.py`: `vorschlagen [disc] [--neu]`, `anwenden <disc> [--ausfuehren]`, `auto <disc>`, `liste [--alle]`
- Eine Disc mit bekannter Seriennummer (z.B. eine zweite gebrannte Kopie derselben Aufnahmen) wird nicht still übersprungen. Seit 2026-11-09 kommt eine Frage mit beiden Discs (Beschriftung, Status, Lesefehler), Empfehlung und Folgen: `d` als Duplikat vermerken (die nächste Nummer bekommt in `fortschritt.csv` den Status `doppelt` mit „gleiche Disc wie D0104“, nichts wird kopiert), `e` trotzdem einlesen (empfohlen, wenn keine Kopie fehlerfrei gelesen ist), `s` überspringen. Ohne Terminal und ohne Programm wird wie früher übersprungen, neu einlesen geht dann mit `--fragen`. Ausnahme: Liegt die Disc in `3-problem/`, wird sie beim erneuten Einlegen unter derselben Nummer noch einmal eingelesen. „Erneut eingelegt“ erkennt das Skript am Medienwechsel-Zähler des Laufwerks, also auch, wenn die Disc schneller wieder drin ist als die Abfrage alle 3 s.
- Ist im Arbeitsordner zu wenig Platz (Disc plus 5 GB Reserve), wartet das Einlesen mit der Disc im Laufwerk, bis `umwandeln.py` Rohdaten abgearbeitet hat. Das Einlesen ist viel schneller als das Umwandeln (ca. 6 min gegenüber ca. 1–1,5 h je Disc), es staut sich also bei vielen Discs am Stück. Je Disc liegen ca. 4 GB in `1-roh/`.
- Disc-Nummern ab `D9000` sind für Tests reserviert und zählen nicht beim Nummernvorschlag.
- Leere oder unlesbare Disc: Erkennt das Laufwerk ein Medium, aber 30 s lang kein Dateisystem (z.B. ein leerer Rohling), meldet `einlesen.py` das mit der Art des Mediums (`DVD-R`, `CD-R` …). Dann kommt ein Doppelton und die Frage, ob die nächste Nummer trotzdem vergeben wird, weil sie schon auf der Disc steht. Bei `j` bekommt die Nummer in `fortschritt.csv` den Status `leer` (ohne Ordner), sonst geht sie an die nächste Disc. Danach wird ausgeworfen. Dasselbe gilt, sofort und noch vor der Beschriftungsfrage, für eine formatierte Disc ohne Dateien (z.B. DVD-RW mit leerem UDF-Dateisystem). Deren Wurzel meldet beim Auflisten „Datei nicht gefunden“ (WinError 2). Was mit der Nummer passiert, legt `--nummern` fest (bei `start.py` und `einlesen.py`): `fragen` (Standard), `immer` (Stapel ist durchnummeriert: die leere Disc verbraucht ihre Nummer ohne Frage, Status `leer`) oder `nie` (erst testen und die Nummer danach draufschreiben: keine Nummer, nur auswerfen; `--unnummeriert` ist dasselbe). Im Programm Disc-Digitalisierung schalten drei Knöpfe im Einlesen-Bereich das sofort um, ohne Neustart (Ereignis-Modus, stdin `einstellung` `nummern`). Eine gerade offene Frage erledigt sich dabei.
- Schwer lesbare Disc: Antwortet das Laufwerk 60 s lang nicht (Anlaufen dauert bis ca. 30 s), weil es sich an der Disc festbeißt, zeigt `einlesen.py` das an („Laufwerk E: antwortet seit 60 s nicht …“, dann die laufende Zeit). Man kann abwarten oder die Disc mit der Taste am Laufwerk auswerfen. Wird sie herausgenommen, kommt dieselbe Frage nach der Nummer, dann mit dem Status `unlesbar`.
- Lesefehler beim Kopieren (seit 2026-11-09 nach dem Prinzip von ddrescue, ohne auf den Nutzer zu warten):
  1. **Schneller Durchgang:** Ist ein 1-MB-Block nicht lesbar, wird die Stelle sofort übersprungen (Nullen) und gemerkt, die Sprungweite wächst bis 32 MB. So kommt alles Lesbare zuerst herunter.
  2. **Nachlesen:** Am Ende des Titels bzw. der Datei werden die gemerkten Stellen erst von beiden Rändern nach innen, dann Sektor für Sektor nachgelesen, in mindestens zwei Durchgängen und weiteren, solange einer noch etwas rettet.
  3. **Frage ohne Warten:** Dauert das Nachlesen über 2 min, kommt eine Frage mit Lage, Umfang (MB und ungefähr Sekunden Video), Restzeit, Empfehlung und der Folge jeder Wahl: `w` weiter nachlesen (Standard, auch ohne Antwort), `ü` Lücke lassen, `i` alle Lücken lassen, `a` abbrechen und reinigen. Das Nachlesen läuft dabei weiter.
  4. **Empfehlung Reinigen:** Am Ende bewertet `kopieren.reinigen_empfehlung` die verbliebenen Lücken: keine, optional (bis 1 MB), empfohlen, oder anderes Laufwerk (zweiter Versuch, Lücken an denselben Stellen wie vorher). Bei „empfohlen“ kommt die Disc nach `3-problem/` (reinigen, neu einlegen, gleiche Nummer), sonst wird sie mit Hinweis übernommen. Hat der Nutzer Lücken gelassen, wird sie immer übernommen.
  Lesen schadet der Disc nicht, es belastet nur das Laufwerk (gesprungene Discs sind bei hoher Drehzahl ein Risiko). Musik-CDs und MakeMKV sind ausgenommen.
- Neuer Versuch nach `problem`: Ordner von Hand aus `3-problem` nach `1-roh` zurück, `status` in `disc.json` auf `eingelesen`. Fertige Titel werden übersprungen.

Warum eigenes Kopieren statt MakeMKV: MakeMKV 2.0 hält die IFO-Dateien des Panasonic-Rekorders für beschädigt und scannt die ganze Disc. Für 5 von 9 Titeln brauchte es über 7 min. `dvd_vr.py` hat Titel 3 (0,3 GB) in 33 s kopiert, ohne Lesefehler (Test 2026-11-04). MakeMKV bleibt als Ausweg für Discs, deren IFO-Dateien `dvd_vr.py` nicht lesen kann.

## Ereignis-Modus für das Programm Disc-Digitalisierung

Das Windows-Programm „Disc-Digitalisierung“ (dieses Repo, `src/`) startet `einlesen.py` und `umwandeln.py` mit `DVD_EREIGNISSE=1` und steuert sie über ein kleines JSON-Protokoll. Das Protokoll (Ereignisse, Fragearten, Antworten) steht in [docs/ereignis-protokoll.md](../docs/ereignis-protokoll.md) (Version 1).

- Jede Zeile `@@ {…}` auf stdout ist ein Ereignis: `start`, `laufwerk`, `disc`, `fortschritt`, `frage`, `frage_erledigt`, `meldung`, `disc_ende`, `warteschlange`, `ende`. Alle anderen Zeilen sind die gewohnte Ausgabe, die `\r`-Fortschrittszeilen entfallen.
- Fragen (Beschriftung, Lesefehler, leere Disc, Benennung von Musik-CDs …) gehen als `frage` hinaus. Die Antwort kommt als `{"typ":"antwort","id":…,"wert":…}` über stdin, die Werte sind die bisherigen Tastatureingaben (`w`/`ü`/`i`/`a`, `j`/`n`, `s` …). `{"typ":"stopp"}` wirkt wie Strg+C.
- Die Disc-Medienart steht im Feld `medientyp`, weil `typ` der Ereignistyp ist.
- Im Ereignis-Modus entfallen die Hinweistöne bei Fragen (das Programm macht sie), der Ton nach jeder Disc bleibt.
- `einlesen.py` im Laufwerksmodus hält die Sperre `einlesen.lock` in `DVD_ARBEIT` (wie `umwandeln.lock`). Läuft schon eines (z.B. `start.py`), endet das zweite mit Exit-Code 3.
- Ohne `DVD_EREIGNISSE=1` verhalten sich die Skripte wie bisher.

## Ordner

Unter `DVD_ARBEIT` (Standard `ausgabe/dvd/` im Repo- bzw. Programmordner, nicht im Repo):

| Ordner | Inhalt |
|---|---|
| `1-roh/<disc>/` | `disc.json`, `tNN.vob` (DVD-Video) oder die kopierten Dateien (Daten-DVD), `menue.png` |
| `2-fertig/<disc>/` | `disc.json`, `tNN.mkv`, `vorschau/tNN-*.jpg`. Rohdaten sind nach bestandener Prüfung gelöscht |
| `3-problem/<disc>/` | Discs mit Lesefehlern oder fehlgeschlagener Prüfung |
| `fortschritt.csv` | je Disc: `disc;beschriftung;typ;titel;stunden;status;geaendert;hinweis` |

Disc-Namen: `D0001`, `D0002`, …, die Nummer steht mit Stift auch auf der Disc.

## disc.json

```json
{
 "disc": "D0001",
 "beschriftung": "Maus Jan-März 2012",
 "label": "DVD_VIDEO_RECORDER",
 "typ": "dvd-video",
 "rekorder": "Panasonic DMR-EHA10",
 "status": "eingelesen",
 "eingelesen": "2026-11-04T19:00:00",
 "lesefehler_sektoren": 0,
 "aussortiert": [{"dvd_titel": 10, "grund": "leer"}],
 "titel": [
  {
   "nr": 1,
   "datei": "t01.vob",
   "dauer_s": 1801,
   "dvd_titel": 1,
   "aufnahme": {"name": "Die Sendung mit der Maus", "sender": "ARD", "sendezeit": "2012-01-15T11:30", "dauer_s": 1801},
   "werbung": [[312.4, 498.0]],
   "zuordnung": {"art": "aufnahme", "sendung": "Die Sendung mit der Maus", "staffel": null, "folge": null, "titel": null, "jahr": null, "tmdb_id": null, "quelle": "fernsehserien.de", "bestaetigt": false}
  }
 ],
 "geaendert": "2026-11-04T19:00:00"
}
```

- Für Daten-DVDs ist `typ` = `daten`, `datei` zeigt auf die kopierte Videodatei, dazu `begleitdateien` (`.eit`, `.meta`, `.txt`, `.xml`, …) und auf Disc-Ebene `sonstige_dateien` (Endung → Anzahl).
- Jeder Teil ändert nur seine eigenen Felder und setzt `status` weiter:

| Teil | Felder auf Disc-Ebene | Felder je Titel |
|---|---|---|
| `einlesen.py` | `seriennummer`, `quelle` (`dvd_vr`/`makemkv`/`kopie`), `einlesen_s`, `hinweise`, `problem_grund` | `byte`, `lesefehler_sektoren`, `luecken_byte` (nur bei Lesefehlern) |
| `umwandeln.py` | `umgewandelt`, `umwandeln_fehler` | `datei` → `tNN.mkv`, `roh_datei`, `werbung` (Liste `[start_s, ende_s]`), `werbung_erkennung`, `video` (`encoder` (beim Umpacken `kopie (<codec>)`, dann `rf`, `preset`, `interlaced_anteil` = `null`), `rf`, `preset`, `bildrate`, `interlaced_anteil`, `groesse_byte`, `dauer_s`, `sekunden_rechenzeit`, `tonspuren`, `untertitel`) |
| `einsortieren.py` | – | `zuordnung` (dazu `sendedatum`, `konfidenz`, `kandidaten`, `hinweise`, `url`, `quelle` = `nutzer` bei Korrektur), nach dem Anwenden `einsortiert` (`pfad`, `zeit` bzw. `verworfen`) |

- `zuordnung.art`: `folge`, `film`, `aufnahme` oder `weg` (nicht einsortieren, z.B. doppelte Aufnahme derselben Folge).
- Weitere Dateien im Disc-Ordner: `menue.png`, IFO-Kopien, `vorschau/tNN-1…6.jpg`, `umwandeln.log`, `pruefen.html`, `zuordnung.csv` (Semikolon, UTF-8 mit BOM für Excel).

## Ziel in Jellyfin

Unter `DVD_ZIEL`, getrennt von anderen Bibliotheken (z.B. Sonarr/Radarr):

| Art | Pfad | Metadaten |
|---|---|---|
| Folge | `Serien/<Serie>/Season NN/<Serie> SNNENN.mkv` | holt Jellyfin selbst (TMDB). Nach der Sonntagsregel zusätzlich eigene NFO (siehe unten) |
| Film | `Filme/<Titel> (<Jahr>)/<Titel> (<Jahr>).mkv` | holt Jellyfin selbst |
| Aufnahme | `Fernsehaufnahmen/<Sendung>/Season <Jahr>/<Sendung> - <JJJJ-MM-TT>.mkv` | eigene `tvshow.nfo` und Episoden-NFO mit `<lockdata>true</lockdata>`, damit Jellyfin keine falschen Folgen aus dem Netz holt |

Musik-CDs gehen nicht über `umwandeln.py` und `einsortieren.py`. `musik_cd.py` legt sie gleich nach dem Einlesen unter `MUSIK_ZIEL/<Albuminterpret>/<Album> (<Jahr>)/NN - <Titel>.flac` ab (bei mehreren CDs eines Albums `D.NN - …`), mit eingebettetem Cover, `cover.jpg` und `cd-leser.json` (Disc-IDs, Offset, AccurateRip-Ergebnis und SHA-1 je Titel). `2-fertig/<disc>/` enthält dann nur `disc.json` (Status `einsortiert`, Feld `ziel`). Gebrannte CDs kennt MusicBrainz meist nicht. Dann ertönt nach dem Lesen ein Doppelton, und das Terminal fragt nach Albuminterpret, Album, Jahr und den Titeln (bei Samplern `Interpret / Titel`, leer = `Titel NN`). Die Dateien bekommen Tags und Namen ohne Neukodieren, danach wird abgelegt und erst dann ausgeworfen. Wer den Interpreten leer lässt, findet das Album ohne Titel in `3-problem/<disc>/album/` (`NN - Titel NN.flac`) und benennt es später mit `python -I skripte/musik_cd.py --benennen <disc>`, ohne die CD neu einzulesen.

`lockdata` sperrt laut Jellyfin-Quellcode den Abruf aus dem Netz je Eintrag (`BaseNfoParser.cs` setzt `IsLocked`, `MetadataService.cs` überspringt dann die Online-Anbieter) ([NFO-Doku](https://jellyfin.org/docs/general/server/metadata/nfo)).

## Quellen für das Benennen

- **fernsehserien.de:** Suche, Episodenführer und Folgenseiten mit Sendeterminen (schema.org `startDate`, Sender). `robots.txt` sperrt nur GPTBot (geprüft 2026-11-04). Laut [Impressum](https://www.fernsehserien.de/impressum) nur für den privaten Gebrauch: Das Skript übernimmt nur Nummern und Titel, keine Beschreibungstexte. Abruf mit generischem User-Agent, mindestens 3 s Pause, 30 Tage Cache unter `<DVD_ARBEIT>/cache/`.
- **TMDB:** nur mit `TMDB_API_KEY`, gleicht dann die Nummerierung ab (ungetestet, kein Schlüssel gesetzt).
- Treffer gelten als sicher bei gleichem Datum, Startzeit ±20 min und passendem Sender.
- Suche in drei Stufen:
  1. Folgen mit Erstausstrahlung bis 14 Tage vor der Aufnahme, deren Sendetermine geprüft werden.
  2. Findet das nichts (Wiederholung), liest es alle Sendetermine der Serie am Aufnahmetag von `/<serie>/sendetermine/datum-JJJJMMTT` (alle Sender, mit Staffel.Folge). Hinweis in der Prüfseite: „gefunden über die Sendetermine am Aufnahmetag“. Seit 2026-11-05, getestet mit D0002: 4 Wiederholungen aus den Staffeln 5–7 gefunden.
  3. **Sonntagsregel** (Liste `SONNTAGSSERIEN`, bisher nur „Die Sendung mit der Maus“, Startjahr 1971): Gilt, wenn fernsehserien.de am Aufnahmetag nur Sendetermine ohne Folge nennt (Maus vor 2012: „Lach- und Sachgeschichten“).
     - Staffel = Jahr − 1970, Folge = n-ter Sonntag im Jahr.
     - Eine Aufnahme vom Montag (KiKA) oder Samstag (NDR/WDR/BR-alpha) zählt zum letzten Sonntag.
     - Sicher ist der Treffer nur, wenn ein Sendetermin mit passendem Sender ±20 min existiert. Dienstag bis Freitag gilt die Regel nicht.
     - `anwenden` schreibt dazu eine Episoden-NFO mit „Sendung vom TT.MM.JJJJ“ und dem Sonntag als Sendedatum, ohne `lockdata`.
     - **Belege:**
       - TMDB zählt 2011 E43 = 23.10., E44 = 30.10., E45 = 06.11., E47 = 20.11., E48 = 27.11. und 2012 E03 = 15.01., E04 = 22.01. Für Januar bis September 2011 kennt TMDB keine Folgen.
       - fernsehserien.de zeigt 2012/2013, dass Montag und Samstag die Folge vom Sonntag davor wiederholen, z.B. Folge 1833 am 15., 16. und 21.01.2012. Für 2011 ist das übertragen (unbestätigt).
       - Abgerufen 2026-11-05.

## Umgebungsvariablen

| Variable | Zweck |
|---|---|
| `DVD_ARBEIT` | Arbeitsordner, Standard `ausgabe/dvd` im Repo- bzw. Programmordner |
| `DVD_ZIEL` | Wurzel der getrennten Jellyfin-Bibliotheken, z.B. eine Netzwerkfreigabe (nur `einsortieren.py`) |
| `DVD_LAUFWERK` | Laufwerk, Standard `E:` |
| `DVD_EREIGNISSE` | `1` = Ereignis-Modus für das Programm Disc-Digitalisierung (siehe oben, nur vom Programm gesetzt) |
| `MUSIK_ZIEL` | Musikbibliothek, Standard `<DVD_ARBEIT>/musik` (nur `musik_cd.py`) |
| `CD_OFFSET` | Laufwerks-Offset für Musik-CDs in Samples. Fehlt er, sucht `musik_cd.py` ihn an der ersten CD, die AccurateRip kennt, und speichert ihn in `<DVD_ARBEIT>/cd_offset.txt` |
| `TMDB_API_KEY` | optional, nur `einsortieren.py`. Nie ins Repo schreiben |

## Programme

Getestet mit MakeMKV 2.0.0, HandBrakeCLI 1.11.2, MKVToolNix 103.0.0, MediaInfo-CLI 26.10, Comskip 0.82.012, ffmpeg/ffprobe 9.0.2 und numpy 2.0.2 (für `cd_leser.py`). Installation und Suchpfade: [README im Hauptordner](../README.md#voraussetzungen).
