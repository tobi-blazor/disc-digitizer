"""Musik-CDs (Audio-CD) als FLAC einlesen und direkt in die Musikbibliothek legen.

Zweck:
  Wird von einlesen.py aufgerufen, wenn eine Audio-CD im Laufwerk liegt (nur TrackNN.cda).
  Gelesen wird mit cd_leser.py (direkt über Windows, ohne Adminrechte). cyanrip wird nicht mehr
  benutzt, es konnte das Laufwerk nicht öffnen (D0063, 2026-11-07).
  1. Inhaltsverzeichnis, AccurateRip-Datensätze und MusicBrainz-Daten holen.
  2. Laufwerks-Offset: CD_OFFSET, sonst <DVD_ARBEIT>/cd_offset.txt, sonst einmalig über AccurateRip
     suchen (geht nur mit einer CD, die AccurateRip kennt) und dort speichern. Ohne Offset wird mit 0
     gelesen (für gebrannte CDs egal, AccurateRip passt dann aber nicht).
  3. Alle Titel lesen, mit AccurateRip prüfen, sonst so oft lesen, bis zwei Lesungen gleich sind.
  4. Ohne Titeldaten (z.B. gebrannte CD): Hinweiston, dann fragt das Terminal nach Albuminterpret,
     Album, Jahr und den Titeln ("Interpret / Titel" bei Samplern). Die FLAC-Dateien bekommen die
     Tags ohne Neukodieren und neue Namen. Erst danach wirft einlesen.py die CD aus.
  5. Album nach MUSIK_ZIEL/<Albuminterpret>/<Album> (<Jahr>)/, Status "einsortiert". Mit Lesefehlern
     oder wenn die Abfrage übersprungen wird (leerer Interpret): Ordner bleibt in
     3-problem/<disc>/album/. Nachträglich benennen ohne neues Einlesen: --benennen <disc>.

Aufruf:
  python -I skripte/musik_cd.py E:                 Infos der eingelegten CD, liest nichts
  python -I skripte/musik_cd.py --benennen D0063   gelesene CD aus 3-problem benennen

Umgebungsvariablen:
  MUSIK_ZIEL   Wurzel der Musikbibliothek, Standard <DVD_ARBEIT>/musik
  CD_OFFSET    Laufwerks-Offset in Samples (optional, sonst siehe oben)
  DVD_EREIGNISSE  1 = Ereignis-Modus: Benennung wird über das Protokoll abgefragt, Fortschritt gemeldet
               (siehe ereignisse.py)

Abhängigkeiten: siehe cd_leser.py (numpy, ffmpeg, Netzzugriff).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # auch mit python -I
import cd_leser as cd  # noqa: E402
import ereignisse  # noqa: E402
import gemeinsam as g  # noqa: E402

MUSIK_ZIEL = Path(os.environ.get("MUSIK_ZIEL") or g.ARBEIT / "musik")
OFFSET_DATEI = g.ARBEIT / "cd_offset.txt"


def ist_audio_cd(wurzel: str) -> bool:
    """True, wenn auf der Disc nur Audiospuren liegen (Windows zeigt sie als TrackNN.cda)."""
    try:
        namen = os.listdir(wurzel)
    except OSError:
        return False
    return bool(namen) and all(n.lower().endswith(".cda") for n in namen)


def _laufwerk(wurzel: str) -> str:
    return wurzel.strip().rstrip("\\/")[:2].upper()  # "E:\\" -> "E:"


def offset(lw: str, toc: cd.Toc, saetze: list, daten: dict) -> int:
    wert = os.environ.get("CD_OFFSET")
    if wert:
        return int(wert)
    if OFFSET_DATEI.exists():
        return int(OFFSET_DATEI.read_text(encoding="utf-8").split()[0])
    if not saetze:
        daten["hinweise"].append("Laufwerks-Offset unbekannt, CD nicht in AccurateRip: mit Offset 0 gelesen")
        return 0
    print("  Laufwerks-Offset unbekannt, suche ihn einmalig über AccurateRip ...")
    with cd.Laufwerk(lw) as l:
        o = cd.offset_suchen(l, toc, saetze)
    if o is None:
        daten["hinweise"].append("Laufwerks-Offset über AccurateRip nicht gefunden: mit Offset 0 gelesen")
        return 0
    OFFSET_DATEI.write_text(f"{o}\nermittelt über AccurateRip an {daten['disc']}, {g.jetzt()}\n", encoding="utf-8")
    print(f"  Offset {o:+d} gespeichert in {OFFSET_DATEI}")
    return o


def _hinweiston() -> None:
    """Zwei kurze Töne: Eingabe nötig (anders als Fertig- und Problem-Ton in einlesen.py)."""
    if ereignisse.AKTIV:
        return  # im Ereignis-Modus macht das Programm den Ton
    try:
        import winsound
        for _ in range(2):
            winsound.Beep(1046, 120)
            winsound.Beep(784, 120)
    except Exception:
        pass


def benennung_abfragen(titel: list[dict], im_laufwerk: bool = True, disc: str | None = None) -> cd.Album | None:
    """Fragt im Terminal (im Ereignis-Modus über das Protokoll) nach Interpret, Album, Jahr und Titelnamen.
    None = später von Hand."""
    if not (sys.stdin.isatty() or ereignisse.AKTIV):
        return None
    frage = ereignisse.frage
    _hinweiston()
    print("  Keine Titeldaten gefunden. Bitte Benennung eingeben"
          + (", die CD bleibt so lange im Laufwerk." if im_laufwerk else "."))
    print("  Leer beim Interpreten = später von Hand (Album bleibt in 3-problem).")
    while True:
        interpret = frage("musik_feld", "Albuminterpret (Sampler: Various Artists). Leer = später von Hand",
                          freitext=True, disc=disc,
                          prompt="  Albuminterpret (Sampler: Various Artists): ").strip()
        if not interpret:
            return None
        album = frage("musik_feld", "Album", freitext=True, disc=disc, prompt="  Album: ").strip()
        while not album:
            album = frage("musik_feld", "Album (Pflicht)", freitext=True, disc=disc,
                          prompt="  Album (Pflicht): ").strip()
        jahr = frage("musik_feld", "Jahr (leer = unbekannt)", freitext=True, disc=disc,
                     prompt="  Jahr (leer = unbekannt): ").strip()
        while jahr and not (jahr.isdigit() and len(jahr) == 4):
            jahr = frage("musik_feld", "Jahr vierstellig oder leer", freitext=True, disc=disc,
                         prompt="  Jahr vierstellig oder leer: ").strip()
        print("  Titel je Spur. Anderer Interpret als das Album: \"Interpret / Titel\". Leer = \"Titel NN\".")
        spuren = []
        for t in titel:
            dauer = f"{t['dauer_s'] // 60}:{t['dauer_s'] % 60:02d}"
            e = frage("musik_feld", f"Titel {t['nr']:02d} ({dauer}). Anderer Interpret als das Album: "
                      "\"Interpret / Titel\". Leer = \"Titel NN\"", freitext=True, disc=disc,
                      prompt=f"  {t['nr']:02d}  {dauer}  ").strip()
            ti, _, tt = e.partition(" / ")
            spuren.append({"titel": tt.strip(), "interpret": ti.strip()} if tt else {"titel": e, "interpret": ""})
        zusammen = [f"{interpret} – {album}" + (f" ({jahr})" if jahr else "")]
        for t, s in zip(titel, spuren):
            zusammen.append(f"{t['nr']:02d}  " + (f"{s['interpret']} – " if s["interpret"] else "")
                            + (s["titel"] or f"Titel {t['nr']:02d}"))
        print("\n  " + "\n  ".join(zusammen))
        a = frage("musik_uebernehmen", "Übernehmen?\n" + "\n".join(zusammen),
                  optionen=[("j", "übernehmen"), ("n", "neu eingeben"), ("s", "später von Hand")], vorgabe="j",
                  disc=disc, prompt="  Übernehmen? [J]a, [n]eu eingeben, [s]päter von Hand: ").strip().lower()
        if a in ("s", "später"):
            return None
        if a in ("", "j", "ja"):
            return cd.Album(interpret=interpret, titel=album, jahr=jahr, release_id="", releasegroup_id="",
                            interpret_ids=[], disc=1, discs=1, spuren=spuren, cover=False)


def _fortschritt_ereignisse(disc: str, anzahl: int):
    """Ereignis-Modus: Fortschritt aus cd_leser.py. Dessen Konsolenanzeige (_anzeige, nur im Terminal sichtbar)
    wird durch eine ersetzt, die "NN  mm:ss  Lesung V  PP%" auswertet. Rückgabe: log-Funktion für
    disc_lesen(), die je fertigem Titel aufgerufen wird."""
    fertig = [0]
    muster = re.compile(r"\s*(\d+)\s+\d+:\d{2}\s+Lesung (\d+)\s+(\d+)%")

    def anzeige(text: str) -> None:
        m = muster.match(text)
        if not m:
            return
        nr, lesung, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ereignisse.fortschritt("einlesen", disc, titel=f"t{nr:02d}", titel_nr=nr, titel_anzahl=anzahl,
                               prozent=round((fertig[0] + p / 100) * 100 / max(anzahl, 1), 1),
                               schritt="kopieren" if lesung == 1 else f"lesung {lesung}")

    def log(zeile: str) -> None:
        print(zeile)
        fertig[0] += 1
        ereignisse.fortschritt("einlesen", disc, sofort=True, titel_anzahl=anzahl,
                               prozent=round(fertig[0] * 100 / max(anzahl, 1), 1), schritt="kopieren")

    cd._anzeige = anzeige
    return log


def einlesen(wurzel: str, arbeit: Path, daten: dict) -> str:
    """Liest die Audio-CD nach arbeit/album/ und legt sie in MUSIK_ZIEL ab.

    Rückgabe: "" = einsortiert (daten["status"] = "einsortiert"), sonst Grund für 3-problem.
    """
    lw = _laufwerk(wurzel)
    daten["typ"] = "audio-cd"
    daten["quelle"] = "cd_leser"
    with cd.Laufwerk(lw) as l:
        toc = l.toc()
    try:
        saetze = cd.ar_abfragen(toc)
    except cd.CdFehler as e:
        saetze = []
        daten["hinweise"].append(f"AccurateRip nicht erreichbar: {e}")
    try:
        album = cd.musicbrainz(toc)
    except cd.CdFehler as e:
        album = None
        daten["hinweise"].append(f"MusicBrainz nicht erreichbar: {e}")
    id1, id2, cddb = cd.ar_ids(toc)
    daten["cd"] = {"musicbrainz_discid": cd.mb_discid(toc), "accuraterip_id": f"{id1:08x}-{id2:08x}-{cddb:08x}",
                   "accuraterip_datensaetze": len(saetze), "spuren": len(toc.spuren),
                   "musicbrainz_release": album.release_id if album else None}
    if album:
        daten["hinweise"] += album.hinweise
        daten["cd"].update({"interpret": album.interpret, "album": album.titel, "jahr": album.jahr or None,
                            "disc_von": f"{album.disc}/{album.discs}" if album.discs > 1 else None,
                            "cover": album.cover})
        print(f"  MusicBrainz: {album.interpret} – {album.titel} ({album.jahr or '?'})")
    else:
        print("  MusicBrainz kennt die CD nicht, lese ohne Titeldaten")
    print(f"  AccurateRip: {'%d Datensätze' % len(saetze) if saetze else 'CD unbekannt'}")
    daten["cd"]["offset"] = off = offset(lw, toc, saetze, daten)
    ereignisse.metadaten(daten["disc"], daten, titel=_album_titel(album, toc))

    album_ordner = arbeit / "album"
    if ereignisse.AKTIV:
        e = cd.disc_lesen(lw, album_ordner, off, saetze, album, log=_fortschritt_ereignisse(daten["disc"], len(toc.audio)))
    else:
        e = cd.disc_lesen(lw, album_ordner, off, saetze, album)
    daten["titel"] = e["titel"]
    ereignisse.metadaten(daten["disc"], daten)

    def json_schreiben() -> None:
        (album_ordner / "cd-leser.json").write_text(
            json.dumps({"cd": daten["cd"], "titel": e["titel"], "hinweise": daten["hinweise"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")

    json_schreiben()
    if e["fehler"]:
        return "Lesefehler: " + "; ".join(e["fehler"])[:300]
    if not album or not (album.interpret and album.titel):
        album = benennung_abfragen(e["titel"], disc=daten["disc"])
        if not album:
            return ("keine MusicBrainz-Daten: benennen mit python -I skripte/musik_cd.py "
                    f"--benennen {daten['disc']}")
        cd.neu_taggen(album_ordner, e["titel"], album, len(e["titel"]))
        daten["cd"]["benennung"] = "von Hand im Terminal"
        daten["hinweise"].append("keine MusicBrainz-Daten, Benennung von Hand eingegeben")
        json_schreiben()
    ablegen(album_ordner, album, daten)
    return ""


def _album_titel(album: "cd.Album | None", toc) -> list[dict]:
    """Geplante Spuren mit MusicBrainz-Angaben für die Metadaten-Anzeige (vor dem Lesen)."""
    aus = []
    for i in range(len(toc.audio)):
        d: dict = {"nr": i + 1}
        if album and i < len(album.spuren):
            d.update({k: v for k, v in album.spuren[i].items() if not k.endswith("_ids")})
        aus.append(d)
    return aus


def ablegen(album_ordner: Path, album: cd.Album, daten: dict) -> None:
    """Verschiebt den Albumordner nach MUSIK_ZIEL/<Interpret>/<Album> (<Jahr>), Status einsortiert."""
    daten["beschriftung"] = f"{album.interpret} – {album.titel}" + (f" ({album.jahr})" if album.jahr else "")
    ziel = MUSIK_ZIEL / cd.sauber(album.interpret) / cd.sauber(
        f"{album.titel} ({album.jahr})" if album.jahr else album.titel)
    if ziel.exists():
        ziel = ziel.with_name(f"{ziel.name} [{daten['disc']}]")
        daten["hinweise"].append("Album gab es schon im Ziel, Ordner hat die Disc-Nummer als Zusatz")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(album_ordner), str(ziel))
    daten["ziel"] = str(ziel)
    daten["status"] = "einsortiert"
    print(f"  -> {ziel}")


def benennen(disc: str) -> int:
    """Benennt eine schon gelesene Musik-CD aus 3-problem/<disc>/album/ nachträglich und legt sie ab."""
    ordner = g.PROBLEM / disc
    album_ordner = ordner / "album"
    if not (album_ordner / "cd-leser.json").exists():
        print(f"{album_ordner} hat keine cd-leser.json, ist das eine Musik-CD?")
        return 1
    daten = g.disc_json_lesen(ordner)
    leser = json.loads((album_ordner / "cd-leser.json").read_text(encoding="utf-8"))
    if any("FEHLER" in t.get("accuraterip", "") for t in leser["titel"]):
        print(f"{disc} hat Lesefehler, bitte neu einlesen (CD einlegen).")
        return 1
    print(f"{disc}: \"{daten.get('beschriftung', '')}\", {len(leser['titel'])} Titel")
    album = benennung_abfragen(leser["titel"], im_laufwerk=False, disc=disc)
    if not album:
        print("Nichts geändert.")
        return 0
    cd.neu_taggen(album_ordner, leser["titel"], album, len(leser["titel"]))
    leser["cd"]["benennung"] = "von Hand im Terminal"
    leser.setdefault("hinweise", []).append("keine MusicBrainz-Daten, Benennung von Hand eingegeben")
    (album_ordner / "cd-leser.json").write_text(json.dumps(leser, ensure_ascii=False, indent=1), encoding="utf-8")
    daten["titel"] = leser["titel"]
    daten.setdefault("hinweise", []).append("keine MusicBrainz-Daten, Benennung von Hand eingegeben")
    ablegen(album_ordner, album, daten)
    daten.pop("problem_grund", None)
    g.disc_json_schreiben(ordner, daten)
    g.FERTIG.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ordner), str(g.FERTIG / disc))
    g.fortschritt_setzen(daten)
    print(f"{disc}: einsortiert, Disc-Ordner jetzt in {g.FERTIG.name}/")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--benennen":
        sys.exit(benennen(sys.argv[2].upper()))
    lw = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("DVD_LAUFWERK") or "E:")
    print(f"Audio-CD: {ist_audio_cd(lw + chr(92))}")
    sys.argv = [sys.argv[0], lw]
    sys.exit(cd.main())
