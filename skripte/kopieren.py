"""Von einer Disc kopieren, auch über Lesefehler hinweg, ohne auf den Nutzer zu warten (Prinzip ddrescue).

Zweck:
  Ein Kratzer kostet das Laufwerk pro Sektor Sekunden bis Minuten (es versucht selbst mehrfach).
  Früher blieb das Einlesen an so einer Stelle stehen. Jetzt geht es in zwei Schritten:
  1. Schneller Durchgang (1-MB-Blöcke): Ist ein Block nicht lesbar, wird die Stelle sofort
     übersprungen (mit Nullen gefüllt) und gemerkt. Die Sprungweite wächst (1, 2, 4 ... 32 MB), bis wieder
     ein Block lesbar ist. So kommt alles Lesbare zuerst sicher herunter.
  2. Nachlesen der gemerkten Stellen: zuerst von beiden Rändern nach innen (dort ist meist noch viel
     lesbar), dann Sektor für Sektor. Weitere Durchgänge, solange einer noch etwas rettet (höchstens
     `versuche`). Gerettete Sektoren ersetzen die Nullen in der Zieldatei.
  Gefragt wird nur, wenn das Nachlesen länger als NACHFRAGE_S dauert, und die Frage hält nichts an:
  Das Nachlesen läuft weiter, bis eine Antwort kommt oder nichts mehr zu holen ist.
      [w]eiter nachlesen    Standard, auch ohne Antwort
      [ü] Lücke lassen      den Rest dieser Stelle nicht mehr versuchen (Nullen, Datenverlust)
      [i] alle Lücken lassen auf dieser Disc nicht mehr nachlesen
      [a]bbrechen           Disc als Problem ablegen, reinigen, neu einlegen (gleiche Nummer)
  Die Frage nennt Lage, Umfang, geschätzten Videoverlust, Restzeit und eine Empfehlung.
  Reine Lesezugriffe schaden der Disc nicht. Sie belasten nur das Laufwerk, und gesprungene Discs
  sind bei hoher Drehzahl ein Risiko.
  Ein einzelner Lesevorgang über HAENGT_S wird gemeldet (Windows kann ihn nicht abbrechen; Auswerfen
  mit der Taste beendet ihn). Disc herausgenommen = Abbruch.
  Im Ereignis-Modus (DVD_EREIGNISSE=1, siehe ereignisse.py) geht die Frage als Ereignis "frage"
  (art "lesefehler", mit details, empfehlung und hinweis je Option) an das Programm.

Benutzt von dvd_vr.py (DVD-Video-Titel) und einlesen.py (Dateien von Daten-DVDs).
Aufruf: nur als Modul. Umgebungsvariablen: DVD_EREIGNISSE (optional, siehe ereignisse.py).
Nur Standardbibliothek.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # auch mit python -I
import ereignisse  # noqa: E402

SEKTOR = 2048
BLOCK = 512 * SEKTOR  # 1 MB
NACHFRAGE_S = 120  # so lange läuft das Nachlesen, bevor (ohne anzuhalten) gefragt wird
HAENGT_S = 20  # ein einzelner Lesevorgang, der länger dauert, wird gemeldet
MAX_SPRUNG = 32  # größte Sprungweite im schnellen Durchgang, in Blöcken
DISC_FEHLT = {21, 1006, 1110, 1112}  # Windows: nicht bereit, Datenträger gewechselt/entfernt, kein Medium
KLEIN_SEKTOREN = 512  # bis 1 MB Verlust gilt als klein (wenige Sekunden Video)

_eingabe = input  # in Tests ersetzbar


class Abgebrochen(Exception):
    """Kopieren abgebrochen: Nutzerwahl oder Disc herausgenommen."""


class _Disc:
    """Wahl und Zählung für die ganze Disc (über mehrere Titel/Dateien hinweg)."""

    def __init__(self) -> None:
        self.neu()

    def neu(self, disc: str | None = None) -> None:
        self.disc = disc  # nur für Ereignisse (Zuordnung der Frage)
        self.immer = False  # alle Lücken dieser Disc lassen (nicht nachlesen)
        self.uebersprungen = False  # Nutzer hat Datenverlust ausdrücklich in Kauf genommen
        self.byte_pro_sekunde: float | None = None  # Videodatenrate des laufenden Titels (Schätzung Verlust)
        self.stellen = 0  # Fehlerstellen auf der Disc bisher
        self.verloren_sektoren = 0  # endgültig unlesbare oder gelassene Sektoren bisher


DISC = _Disc()


def neue_disc(disc: str | None = None) -> None:
    """Vor jeder Disc aufrufen: setzt Wahl und Zählung zurück. `disc` dient der Zuordnung der Frage."""
    DISC.neu(disc)


def _hinweiston() -> None:
    if ereignisse.AKTIV:
        return  # im Ereignis-Modus macht das Programm den Ton
    try:
        import winsound
        for f in (1046, 784, 1046, 784):
            winsound.Beep(f, 120)
    except Exception:
        pass


def _gb(byte: int) -> str:
    return f"{byte / 1e9:.2f}".replace(".", ",")


def _mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def video_text(sektoren: int) -> str:
    """Umfang in MB und, wenn die Datenrate bekannt ist, ungefähr in Sekunden Video."""
    byte = sektoren * SEKTOR
    t = f"{byte / 1e6:.1f} MB".replace(".", ",")
    if DISC.byte_pro_sekunde:
        s = byte / DISC.byte_pro_sekunde
        t += f" ≈ {s:.0f} s Video" if s >= 1 else " ≈ unter 1 s Video"
    return t


class _Zustand:
    def __init__(self, laenge: int) -> None:
        self.laenge = laenge
        self.modus = "lassen" if DISC.immer else "nachlesen"  # nachlesen | lassen | alle_lassen | abbrechen
        self.phase = "schnell"  # schnell | nachlesen | fertig
        self.lesen_seit: float | None = None  # Beginn des laufenden Lesevorgangs
        self.gemeldet_haengt = False
        self.nachlesen_seit: float | None = None
        self.gefragt = False
        self.frage_offen = False
        self.fertig = False
        self.stelle = 0  # zuletzt angefragte Leseposition
        self.offen_sektoren = 0  # noch nicht (erfolgreich) gelesene Sektoren in Lücken
        self.gerettet = 0  # beim Nachlesen gerettete Sektoren
        self.versucht_fehl = 0  # beim Nachlesen gescheiterte Leseversuche
        self.fehl_zeit = 0.0  # Zeit dafür (Schätzung Restzeit)
        self.stellen = 0


LESEFEHLER_OPTIONEN = [("w", "weiter nachlesen"), ("ü", "Lücke lassen"), ("i", "alle Lücken lassen"),
                       ("a", "abbrechen und reinigen")]


def _frage_inhalt(z: _Zustand) -> tuple[str, str, str, list[tuple[str, str, str]]]:
    """(text, details, empfehlung, optionen mit hinweis) für die Lesefehler-Frage."""
    pro = z.fehl_zeit / z.versucht_fehl if z.versucht_fehl else 5.0
    rest_s = z.offen_sektoren * pro
    text = (f"Lesefehler: {z.stellen} Stelle(n), {z.offen_sektoren} Sektoren noch offen ({video_text(z.offen_sektoren)}). "
            f"Nachlesen läuft seit {_mmss(time.monotonic() - (z.nachlesen_seit or time.monotonic()))} min, "
            f"bisher {z.gerettet} Sektoren gerettet.")
    details = (f"Alles andere ist schon gelesen. Restzeit fürs Nachlesen bis zu ca. {_mmss(rest_s)} min "
               f"(ca. {pro:.0f} s je unlesbarem Sektor). Ohne Antwort liest das Programm weiter, solange es "
               "noch etwas rettet. Der Disc schadet das nicht.")
    if z.offen_sektoren <= KLEIN_SEKTOREN:
        empf, grund = "ü", "Die Lücke ist klein: Es fehlen nur wenige Sekunden Bild, das spart Zeit."
    elif z.gerettet == 0 and z.versucht_fehl >= 20:
        empf, grund = "a", ("Beim Nachlesen kam noch nichts zurück. Oft sind Schmutz oder Fingerabdrücke schuld: "
                            "Reinigen und neu einlegen holt wahrscheinlich mehr heraus.")
    else:
        empf, grund = "w", "Das Nachlesen rettet noch Sektoren. Weiterlaufen lassen lohnt sich."
    details += "\nEmpfehlung: " + grund
    optionen = [
        ("w", "weiter nachlesen", "Liest die Stelle weiter Sektor für Sektor. Kein Datenverlust, kostet nur Zeit."),
        ("ü", "Lücke lassen", f"Der Rest dieser Stelle bleibt leer ({video_text(z.offen_sektoren)}): kurzer Bildfehler "
                              "oder Aussetzer. Bei der nächsten Fehlerstelle wird wieder nachgelesen."),
        ("i", "alle Lücken lassen", "Wie „Lücke lassen“, aber für alle weiteren Stellen dieser Disc. Am schnellsten, "
                                    "es können mehrere Aussetzer entstehen."),
        ("a", "abbrechen und reinigen", "Einlesen endet, die Disc kommt nach 3-problem. Reinigen, trocknen lassen und "
                                         "neu einlegen: Der neue Versuch läuft unter derselben Nummer."),
    ]
    return text, details, empf, optionen


def _wahl(z: _Zustand) -> str:
    text, details, empf, optionen = _frage_inhalt(z)
    if ereignisse.AKTIV:
        return ereignisse.frage("lesefehler", text, optionen=optionen, vorgabe="w", disc=DISC.disc,
                                details=details, empfehlung=empf, prompt="  Wahl [w/ü/i/a]: ")
    print(f"\n\n  {text}\n  {details}")
    for w, t, h in optionen:
        print(f"  [{w}] {t}{' (empfohlen)' if w == empf else ''}: {h}")
    return _eingabe("  Wahl [w/ü/i/a, Enter = weiter]: ")


def _fragen(z: _Zustand) -> None:
    """Läuft im Wächter-Thread; der Hauptthread liest dabei weiter."""
    _hinweiston()
    try:
        a = _wahl(z).strip().lower()[:1]
    except EOFError:
        a = "w"
    if z.phase != "nachlesen":
        return  # inzwischen fertig: Antwort hat sich erledigt
    if a in ("ü", "u"):
        z.modus = "lassen"
        DISC.uebersprungen = True
        print("  Lasse den Rest dieser Stelle, sobald der laufende Leseversuch endet.")
    elif a == "i":
        z.modus = "lassen"
        DISC.immer = DISC.uebersprungen = True
        print("  Lasse ab jetzt alle Lücken dieser Disc.")
    elif a == "a":
        z.modus = "abbrechen"
        print("  Breche ab, sobald der laufende Leseversuch endet. Sofort: Disc mit der Taste am Laufwerk "
              "auswerfen.\n  Dann reinigen und wieder einlegen, der neue Versuch läuft unter derselben Nummer.")
    else:
        print("  Lese weiter nach.")
    print()


def _waechter(z: _Zustand, fragen: bool) -> None:
    while not z.fertig:
        time.sleep(1)
        jetzt = time.monotonic()
        if (not z.gemeldet_haengt and z.lesen_seit is not None and jetzt - z.lesen_seit > HAENGT_S):
            z.gemeldet_haengt = True
            t = (f"Laufwerk liest seit {HAENGT_S} s an einer Fehlerstelle bei {z.stelle * 100 // max(z.laenge, 1)} %. "
                 "Windows kann das nicht abbrechen; danach wird die Stelle übersprungen und später nachgelesen.")
            ereignisse.meldung("hinweis", t, DISC.disc)
            if not ereignisse.AKTIV and fragen:
                print(f"\n  {t}")
        if (fragen and not z.gefragt and z.phase == "nachlesen" and z.modus == "nachlesen"
                and z.nachlesen_seit is not None and jetzt - z.nachlesen_seit > NACHFRAGE_S):
            z.gefragt = z.frage_offen = True
            try:
                _fragen(z)
            finally:
                z.frage_offen = False


def kopieren(lesen, laenge: int, out, fortschritt=None, versuche: int = 2, fragen: bool | None = None,
             karte=None) -> dict:
    """Kopiert `laenge` Byte aus `lesen(pos, n) -> bytes` nach `out` (binär, mit seek).

    `lesen` wirft OSError bei Lesefehlern. `fortschritt(pos, laenge, fehler_sektoren)` wird laufend
    aufgerufen (fehler_sektoren = noch offene bzw. unlesbare Sektoren). `karte(von, bis, zustand)`
    (optional) erfährt jeden Bereich: ok, ausstehend (übersprungen, wird nachgelesen), fehler (unlesbar
    nach dem Nachlesen), uebersprungen (auf Wunsch gelassen). Rückgabe:
    {"byte", "fehler_sektoren", "luecken": [[von, bis], ...] in Byte, "uebersprungen": bool,
     "stellen", "gerettet_sektoren"}.
    Wirft Abgebrochen bei Nutzerwahl "abbrechen" oder wenn die Disc herausgenommen wurde.
    """
    karte = karte or (lambda von, bis, zustand: None)
    if fragen is None:
        fragen = (sys.stdin.isatty() and sys.stdout.isatty()) or ereignisse.AKTIV
    z = _Zustand(laenge)

    def melden(pos: int) -> None:
        if fortschritt:
            fortschritt(pos, laenge, z.offen_sektoren)

    def einmal(pos: int, n: int) -> bytes:
        z.lesen_seit, z.stelle, z.gemeldet_haengt = time.monotonic(), pos, False
        try:
            return lesen(pos, n)
        except OSError as e:
            if getattr(e, "winerror", None) in DISC_FEHLT:
                raise Abgebrochen(f"Disc herausgenommen bei {pos * 100 // max(laenge, 1)} %") from e
            raise
        finally:
            z.lesen_seit = None

    def pruefen_abbruch() -> None:
        if z.modus == "abbrechen":
            raise Abgebrochen(f"vom Nutzer abgebrochen bei {z.stelle * 100 // max(laenge, 1)} %")

    w = threading.Thread(target=_waechter, args=(z, fragen), daemon=True)
    w.start()
    offen: list[list[int]] = []  # Lücken aus dem schnellen Durchgang [von, bis) in Byte
    try:
        # ---- 1. schneller Durchgang
        pos, sprung = 0, 1
        while pos < laenge:
            pruefen_abbruch()
            n = min(BLOCK, laenge - pos)
            try:
                daten = einmal(pos, n)
            except OSError:
                daten = None
            if daten is not None:
                if not daten:  # Quelle kürzer als gemeldet
                    break
                karte(pos, pos + len(daten), "ok")
                out.seek(pos)
                out.write(daten)
                pos += len(daten)
                sprung = 1
            else:
                weite = min(sprung * BLOCK, laenge - pos)
                out.seek(pos)
                for k in range(0, weite, BLOCK):
                    out.write(bytes(min(BLOCK, weite - k)))
                if offen and offen[-1][1] == pos:
                    offen[-1][1] = pos + weite
                else:
                    offen.append([pos, pos + weite])
                    z.stellen += 1
                    ereignisse.meldung("hinweis", f"Fehlerstelle bei {pos * 100 // max(laenge, 1)} % übersprungen, "
                                       "wird am Ende nachgelesen.", DISC.disc)
                karte(pos, pos + weite, "ausstehend")
                z.offen_sektoren += (weite + SEKTOR - 1) // SEKTOR
                pos += weite
                sprung = min(sprung * 2, MAX_SPRUNG)
            melden(pos)
        ende = pos

        # ---- 2. Nachlesen
        def sektor(s: int) -> bool:
            """Liest einen Sektor nach; True = gerettet."""
            m = min(SEKTOR, ende - s)
            t0 = time.monotonic()
            try:
                teil = einmal(s, m)
            except OSError:
                z.versucht_fehl += 1
                z.fehl_zeit += time.monotonic() - t0
                return False
            if len(teil) < m:
                teil += bytes(m - len(teil))
            out.seek(s)
            out.write(teil)
            karte(s, s + m, "ok")
            z.gerettet += 1
            z.offen_sektoren -= 1
            melden(ende)
            return True

        rest: list[tuple[int, str]] = []  # nicht gelesene Sektoren: (Anfang in Byte, fehler|uebersprungen)
        if offen and z.modus == "nachlesen":
            z.phase, z.nachlesen_seit = "nachlesen", time.monotonic()
            ereignisse.meldung("hinweis", f"Lese {z.offen_sektoren} Sektoren an {len(offen)} Stelle(n) nach.", DISC.disc)
        for von, bis in offen:
            if z.modus == "lassen" and not DISC.immer:  # "Lücke lassen" galt nur für die vorige Stelle
                z.modus, z.gefragt, z.nachlesen_seit = "nachlesen", False, time.monotonic()
            sektoren = list(range(von, bis, SEKTOR))
            if z.modus != "nachlesen":
                rest += [(s, "uebersprungen") for s in sektoren]
                continue
            # von den Rändern nach innen, bis je ein Sektor scheitert
            a, schon = 0, set()
            while a < len(sektoren) and z.modus == "nachlesen":
                if not sektor(sektoren[a]):
                    schon.add(sektoren[a])
                    break
                a += 1
            pruefen_abbruch()
            b = len(sektoren)
            while b - 1 > a and z.modus == "nachlesen":
                if not sektor(sektoren[b - 1]):
                    schon.add(sektoren[b - 1])
                    break
                b -= 1
            pruefen_abbruch()
            mitte = sektoren[a:b]
            # Mitte Sektor für Sektor; zwei Durchgänge (manche Sektoren gehen erst beim zweiten Mal),
            # weitere nur, solange der vorige etwas gerettet hat
            for d in range(max(1, versuche)):
                vorher, naechste = len(mitte), []
                for i, s in enumerate(mitte):
                    if z.modus != "nachlesen":
                        naechste += mitte[i:]
                        break
                    if (d == 0 and s in schon) or not sektor(s):
                        naechste.append(s)
                pruefen_abbruch()
                mitte = naechste
                if not mitte or (d >= 1 and len(mitte) == vorher) or z.modus != "nachlesen":
                    break
            zustand = "uebersprungen" if z.modus == "lassen" else "fehler"
            rest += [(s, zustand) for s in mitte]
        z.phase = "fertig"
        if z.frage_offen and ereignisse.AKTIV:
            ereignisse.offene_beantworten("lesefehler", "w", durch="fertig")

        # Ergebnis: Lücken aus den übrig gebliebenen Sektoren
        luecken: list[list[int]] = []
        for s, zustand in sorted(rest):
            e = min(s + SEKTOR, ende)
            karte(s, e, zustand)
            if luecken and luecken[-1][1] == s:
                luecken[-1][1] = e
            else:
                luecken.append([s, e])
        z.offen_sektoren = len(rest)
        melden(ende)
        DISC.stellen += z.stellen
        DISC.verloren_sektoren += len(rest)
        return {"byte": ende, "fehler_sektoren": len(rest), "luecken": luecken,
                "uebersprungen": any(zu == "uebersprungen" for _, zu in rest), "stellen": z.stellen,
                "gerettet_sektoren": z.gerettet}
    finally:
        z.fertig = True
        if z.frage_offen and not ereignisse.AKTIV:
            print("\n  (Die Frage hat sich erledigt. Bitte Enter drücken.)")
            w.join()  # Terminal: sonst schluckt die offene Frage die nächste Eingabe


def luecken_text(luecken: list[list[int]], laenge: int) -> str:
    """z.B. "62–64 %, 80 %" für die Meldung am Ende."""
    teile = []
    for von, bis in luecken:
        a, b = von * 100 // max(laenge, 1), (bis * 100 + laenge - 1) // max(laenge, 1)
        teile.append(f"{a} %" if b - a <= 1 else f"{a}–{b} %")
    return ", ".join(dict.fromkeys(teile))


def reinigen_empfehlung(verloren_sektoren: int, stellen: int, gleiche_stellen_wie_vorher: bool | None) -> tuple[str, str]:
    """Empfehlung nach dem Einlesen: (stufe, text). stufe: keine, optional, empfohlen, anderes_laufwerk.

    gleiche_stellen_wie_vorher: True/False beim zweiten Versuch (Lücken an denselben Stellen wie beim
    vorigen Versuch), None beim ersten."""
    if verloren_sektoren == 0:
        return "keine", "Alles gelesen, Reinigen nicht nötig."
    umfang = f"{verloren_sektoren} Sektoren an {stellen} Stelle(n) unlesbar ({video_text(verloren_sektoren)})"
    if gleiche_stellen_wie_vorher:
        return "anderes_laufwerk", (f"{umfang}, an denselben Stellen wie beim vorigen Versuch. Reinigen hat nicht "
                                    "geholfen, vermutlich ein Kratzer oder Materialschaden. Lücken übernehmen oder ein "
                                    "anderes Laufwerk versuchen.")
    if verloren_sektoren <= KLEIN_SEKTOREN:
        return "optional", (f"{umfang}. Reinigen könnte die Lücke schließen, lohnt sich bei so wenig Verlust "
                            "aber kaum.")
    return "empfohlen", (f"{umfang}. Reinigen empfohlen (weiches, fusselfreies Tuch, von der Mitte gerade nach "
                         "außen wischen, nicht im Kreis), trocknen lassen und neu einlegen. Schmutz und "
                         "Fingerabdrücke verursachen oft genau solche Fehler.")
