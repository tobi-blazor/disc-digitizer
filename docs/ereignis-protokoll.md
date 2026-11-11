# Ereignis-Protokoll Python ↔ C#

Vertrag zwischen den Python-Skripten (`skripte/`) und dem C#-Programm.
Version **1**. Änderungen nur gemeinsam auf beiden Seiten, Version hochzählen.

## Einschalten

Das Programm startet die Skripte mit:

| Umgebungsvariable | Wert | Wirkung |
|---|---|---|
| `DVD_EREIGNISSE` | `1` | Ereignis-Modus an |
| `PYTHONIOENCODING` | `utf-8` | stdin/stdout in UTF-8 |
| `PYTHONUNBUFFERED` | `1` | keine Pufferung |

Achtung: `python -I` ignoriert alle `PYTHON*`-Variablen. Deshalb stellt `ereignisse.py` im Ereignis-Modus stdin, stdout
und stderr selbst auf UTF-8 und Zeilenpufferung um. Die Variablen bleiben gesetzt, falls ohne `-I` gestartet wird.
Die Felder `v`, `typ` und `zeit` sind reserviert und dürfen nicht als Nutzdaten vorkommen.

Aufruf: `python -I <skriptordner>/einlesen.py [Argumente]` bzw. `umwandeln.py`. Ohne `DVD_EREIGNISSE` verhalten sich die Skripte **genau wie bisher** (Terminal, `input()`).

## Richtung Python → C# (stdout)

- Jede Zeile, die mit `@@ ` beginnt (zwei At-Zeichen, ein Leerzeichen), ist ein Ereignis: Rest der Zeile ist **ein** JSON-Objekt, einzeilig.
- Alle anderen Zeilen sind normale Ausgabe (die bisherigen `print`s) und werden im Programm als Log angezeigt. Fortschritts-Zeilen mit `\r` entfallen im Ereignis-Modus (stattdessen `fortschritt`).
- stderr ist Log (Fehlerkanal), z.B. Tracebacks.

Jedes Ereignis hat die Felder `v` (Protokollversion, `1`), `typ` und `zeit` (ISO 8601 mit Offset, z.B. `2026-11-09T14:03:12+01:00`). Unbekannte Felder ignoriert der Empfänger, unbekannte `typ` werden als Log angezeigt.

| `typ` | Felder | Wann |
|---|---|---|
| `start` | `skript` (`einlesen`/`umwandeln`), `pid`, `argumente` (Liste) | direkt nach dem Start |
| `laufwerk` | `zustand` (`leer`, `disc`, `haengt`, `ohne_inhalt`, `verarbeitet`), `label`, `medium`, `seit_s` | bei jeder Zustandsänderung des Laufwerks (nicht bei jeder Abfrage) |
| `disc` | `disc`, `beschriftung`, `label`, `medientyp` (`dvd-video`/`daten`/`audio`), `seriennummer`, `phase` (`einlesen`/`umwandeln`/`einsortieren`) | Beginn der Arbeit an einer Disc in einer Phase |
| `fortschritt` | `disc`, `phase`, `titel` (Name, z.B. `t02`), `titel_nr`, `titel_anzahl`, `byte`, `byte_gesamt`, `prozent` (0–100, Gesamtfortschritt der Disc in dieser Phase), `fehler_sektoren`, `rate_mb_s`, `rest_s`, `schritt` (z.B. `kopieren`, `handbrake`, `comskip`, `pruefen`) | höchstens ca. 1× pro Sekunde |
| `frage` | `id` (eindeutig, z.B. `q7`), `art` (siehe unten), `text`, `optionen` (Liste `{wert, text, hinweis}`, `hinweis` = Folge dieser Wahl, optional; leer bei Freitext), `freitext` (bool), `vorgabe` (Wert bei Enter/leer), `disc`, `details` (Erklärung und Begründung der Empfehlung, optional), `empfehlung` (empfohlener Wert, optional) | Skript braucht eine Entscheidung. Bei `lesefehler` arbeitet es dabei weiter (siehe unten) |
| `frage_erledigt` | `id`, `wert`, `durch` (`programm`/`terminal`/`zeit`/`einstellung`/`fertig`) | Antwort angenommen (auch wenn die Frage anders erledigt wurde) |
| `meldung` | `stufe` (`info`/`hinweis`/`warnung`/`fehler`), `text`, `disc` | wichtige Einzelmeldungen, z.B. „Auswerfen hat nicht geklappt“ |
| `disc_ende` | `disc`, `phase`, `status` (`eingelesen`, `problem`, `leer`, `unlesbar`, `uebersprungen`, `doppelt`, `umgewandelt`, `einsortiert`, `pruefen`), `grund`, `ziel` (Ordner/Pfad), `dauer_s` | Ende einer Disc in einer Phase |
| `warteschlange` | `wartend` (Liste Disc-Nummern), `aktiv` (Disc oder null), `pausiert_bis` (ISO oder null, Zeitfenster) | nur `umwandeln`: bei Änderung |
| `metadaten` | `disc`, `felder` (Liste `{gruppe, name, wert}`, Werte schon als Text, z.B. Dauer, GB) | vollständiger Stand aller bekannten Angaben zur Disc (Laufwerk, Disc, Rekorder, Titel n, Musik-CD, Aussortiert, Hinweise); ersetzt den vorigen. Beim Start, nach der Analyse, nach jedem Titel, am Ende |
| `lesekarte` | `disc`, `sektoren_belegt` (Länge des Dateisystems), `sektoren_max` (Kapazität des Rohlings), `bereiche` (neue `[von, bis, zustand]` in 2048-Byte-Sektoren ab Anfang des Dateisystems, `zustand`: `ok`, `ausstehend` = übersprungen, wird nachgelesen, `fehler` = nach dem Nachlesen unlesbar, `uebersprungen` = auf Wunsch gelassen), beim ersten Ereignis `geplant` (`[von, bis]`: wo die zu lesenden Dateien liegen) | echte Lage auf der Disc (FSCTL_GET_RETRIEVAL_POINTERS) für die Disc-Grafik; höchstens ca. 1× pro Sekunde; spätere Zustände überschreiben frühere |
| `empfehlung` | `disc`, `art` (`reinigen`), `stufe` (`keine`/`optional`/`empfohlen`/`anderes_laufwerk`), `text` | am Ende des Einlesens, wenn es Lesefehler gab (auch beim zweiten Versuch ohne Fehler) |
| `einstellung` | `name`, `wert` | aktueller Wert einer zur Laufzeit umschaltbaren Einstellung: beim Start und nach jeder angenommenen Änderung |
| `ende` | `code` | kurz vor dem Beenden |

### Umschaltbare Einstellungen

| `name` | Skript | Werte | Wirkung |
|---|---|---|---|
| `nummern` | `einlesen.py` (Startwert `--nummern`) | `fragen`, `immer`, `nie` | leere/unlesbare Disc: nachfragen, ob die Nummer vergeben wird / Nummer ohne Frage vergeben (Stapel durchnummeriert) / keine Nummer (erst testen, Nummer danach draufschreiben). Beim Umschalten auf `immer`/`nie` wird eine offene Frage `nummer_vergeben` mit `j`/`n` erledigt (`frage_erledigt` mit `durch` = `einstellung`). |

### Fragearten (`art`)

| `art` | Herkunft | Optionen / Freitext |
|---|---|---|
| `lesefehler` | `kopieren.py` (nur beim Nachlesen, nach 2 min; das Nachlesen läuft weiter) | `w` weiter nachlesen (Vorgabe), `ü` Lücke lassen, `i` alle Lücken lassen, `a` abbrechen und reinigen; je mit `hinweis`, dazu `details` und `empfehlung`. Ist das Nachlesen fertig, bevor geantwortet wurde: `frage_erledigt` mit `durch` = `fertig` |
| `beschriftung` | `einlesen.py` | Freitext, `vorgabe` = Disc-Label; Wert `s` = überspringen |
| `disc_nummer` | `einlesen.py --fragen` | Freitext, `vorgabe` = Vorschlag; `s` = überspringen |
| `ueberschreiben` | `einlesen.py --fragen` | `a` andere Nummer, `ü` überschreiben, `s` überspringen |
| `nummer_vergeben` | `einlesen.py` (leere/unlesbare Disc) | `j` ja, `n` nein |
| `duplikat` | `einlesen.py` (Seriennummer schon bekannt, z.B. zweite gebrannte Kopie) | `d` als Duplikat vermerken (nächste Nummer mit Status `doppelt`, nichts kopiert; im Modus `nummern` = `nie` ohne Nummer), `e` trotzdem einlesen (neue Nummer), `s` überspringen; mit `details`, `empfehlung` (`e`, wenn keine fehlerfreie Kopie existiert, sonst `d`) und `hinweis` je Option. Ersetzt `trotzdem_einlesen` |
| `musik_feld` | `musik_cd.py` (Benennung ohne MusicBrainz) | Freitext, `text` sagt welches Feld (Interpret, Album, Jahr, Titel NN) |
| `musik_uebernehmen` | `musik_cd.py` | `j` übernehmen, `n` neu eingeben, `s` später von Hand |

Die Werte entsprechen genau den bisherigen Terminal-Eingaben, damit die Skript-Logik unverändert bleibt.

## Richtung C# → Python (stdin)

Eine JSON-Zeile pro Nachricht, UTF-8, mit `\n` abgeschlossen:

| `typ` | Felder | Wirkung |
|---|---|---|
| `antwort` | `id`, `wert` | beantwortet die offene Frage `id`. Unbekannte/erledigte `id` werden ignoriert (mit `meldung` Stufe `warnung`). |
| `stopp` | – | sauber beenden wie Strg+C (`_thread.interrupt_main()`), danach `ende` |
| `einstellung` | `name`, `wert` | schaltet eine Einstellung um (siehe oben). Bestätigung mit Ereignis `einstellung`, ungültiger Wert mit `meldung` Stufe `warnung`. Kommt sie vor dem Anmelden im Skript an, wird sie vorgemerkt. |

Python liest stdin in einem eigenen Thread und verteilt Antworten an die wartende Frage. Es kann mehr als eine Frage gleichzeitig offen sein (z.B. Lesefehler im Wächter-Thread), daher immer über `id` zuordnen.

## Beispiel

```
@@ {"v":1,"typ":"start","zeit":"2026-11-09T14:00:00+01:00","skript":"einlesen","pid":4711,"argumente":[]}
Beobachte E: (alle 3 s), Beenden mit Strg+C
@@ {"v":1,"typ":"laufwerk","zeit":"…","zustand":"disc","label":"KINDERSERIE_FOLGE_6","medium":"DVD+R","seit_s":0}
@@ {"v":1,"typ":"disc","zeit":"…","disc":"D0072","beschriftung":"Kinderserie Folge 6","label":"KINDERSERIE_FOLGE_6","medientyp":"dvd-video","seriennummer":1234567890,"phase":"einlesen"}
@@ {"v":1,"typ":"fortschritt","zeit":"…","disc":"D0072","phase":"einlesen","titel":"t02","titel_nr":2,"titel_anzahl":4,"byte":1330000000,"byte_gesamt":1840000000,"prozent":72.3,"fehler_sektoren":46,"rate_mb_s":0.0,"rest_s":null,"schritt":"kopieren"}
@@ {"v":1,"typ":"frage","zeit":"…","id":"q1","art":"lesefehler","text":"Fehlerstelle bei 84 % (1,33 von 1,84 GB), seit 2:00 min","optionen":[{"wert":"w","text":"weiter versuchen"},{"wert":"ü","text":"überspringen"},{"wert":"i","text":"immer überspringen"},{"wert":"a","text":"abbrechen"}],"freitext":false,"vorgabe":"w","disc":"D0072"}
```
C# antwortet: `{"typ":"antwort","id":"q1","wert":"ü"}`
