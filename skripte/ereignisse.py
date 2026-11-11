"""Ereignis-Modus: Skripte für das Programm "Disc-Digitalisierung" steuer- und überwachbar machen.

Zweck:
  Mit DVD_EREIGNISSE=1 schreiben einlesen.py, umwandeln.py, kopieren.py und musik_cd.py zusätzlich
  zur normalen Ausgabe Ereignis-Zeilen auf stdout ("@@ " + einzeiliges JSON) und stellen ihre
  Rückfragen nicht mehr im Terminal (input), sondern über das Protokoll. Antworten, "stopp" und
  "einstellung" (zur Laufzeit umschaltbare Werte, z.B. nummern in einlesen.py) kommen als JSON-Zeilen
  über stdin. Ohne DVD_EREIGNISSE=1 tut dieses Modul nichts: senden() ist leer und
  frage() ruft input() mit dem bisherigen Prompt auf, das Verhalten ist also unverändert.

  Das Protokoll (Ereignisse, Fragearten, stdin-Nachrichten) steht im Repo disc-digitalisierung:
  docs/ereignis-protokoll.md (Version 1). Änderungen nur gemeinsam mit dem C#-Programm.

  Das Ereignis "disc" führt die Medienart im Feld "medientyp" (dvd-video, daten, audio), weil "typ" der
  Ereignistyp ist. senden() verweigert die Felder "v", "typ" und "zeit".
  Mit python -I werden PYTHONIOENCODING/PYTHONUNBUFFERED ignoriert: Im Ereignis-Modus stellt dieses Modul
  beim Import stdin/stdout/stderr selbst auf UTF-8 und stdout/stderr auf zeilenweises Leeren um.

Aufruf: nur als Modul (import ereignisse). Beispiel:
  ereignisse.senden("meldung", stufe="hinweis", text="...")
  wert = ereignisse.frage("beschriftung", "Beschriftung?", freitext=True, vorgabe="X", prompt="Beschriftung: ")

Umgebungsvariablen:
  DVD_EREIGNISSE   "1" schaltet den Ereignis-Modus ein (setzt das Programm zusammen mit
                   PYTHONIOENCODING=utf-8 und PYTHONUNBUFFERED=1)

Nur Standardbibliothek.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sys
import threading
import time
import _thread

VERSION = 1
AKTIV = os.environ.get("DVD_EREIGNISSE") == "1"
FORTSCHRITT_ABSTAND_S = 1.0  # höchstens so oft je Phase

if AKTIV:
    for _strom, _opt in ((sys.stdout, {"line_buffering": True}), (sys.stderr, {"line_buffering": True}),
                         (sys.stdin, {})):
        try:
            _strom.reconfigure(encoding="utf-8", **_opt)
        except Exception:
            pass

_schreib_sperre = threading.Lock()
_sperre = threading.Lock()  # schützt _offen, _zaehler, _letzte, _ende, _leser
_offen: dict[str, "_Offen"] = {}
_zaehler = 0
_letzte: dict[str, float] = {}
_ende = False  # stdin geschlossen oder stopp: keine Antworten mehr
_leser: threading.Thread | None = None


class _Offen:
    def __init__(self) -> None:
        self.ereignis = threading.Event()
        self.wert: str = ""
        self.fehler = False  # EOF/stopp: EOFError werfen
        self.art = ""
        self.durch = "programm"  # wer geantwortet hat (frage_erledigt)


def _zeit() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def senden(typ: str, **felder) -> None:
    """Schreibt ein Ereignis ("@@ " + JSON in einer Zeile) auf stdout. Ohne Ereignis-Modus: nichts."""
    if not AKTIV:
        return
    if {"v", "typ", "zeit"} & felder.keys():
        raise ValueError("Felder v, typ und zeit sind für das Ereignis reserviert")
    _leser_starten()
    e = {"v": VERSION, "typ": typ, "zeit": _zeit()}
    e.update(felder)
    zeile = "@@ " + json.dumps(e, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    with _schreib_sperre:
        try:
            sys.stdout.flush()
            puffer = getattr(sys.stdout, "buffer", None)
            if puffer is not None:
                puffer.write(zeile.encode("utf-8"))
                puffer.flush()
            else:
                sys.stdout.write(zeile)
                sys.stdout.flush()
        except (OSError, ValueError):
            pass  # Empfänger weg: das Skript soll deshalb nicht abstürzen


def meldung(stufe: str, text: str, disc: str | None = None) -> None:
    """Wichtige Einzelmeldung. stufe: info, hinweis, warnung, fehler."""
    senden("meldung", stufe=stufe, text=text, disc=disc)


def fortschritt(phase: str, disc: str | None = None, sofort: bool = False, **felder) -> None:
    """Fortschrittsereignis, je Phase höchstens ca. 1x pro Sekunde. Bei prozent >= 100 oder
    sofort=True (z.B. Schrittwechsel) wird immer gesendet."""
    if not AKTIV:
        return
    jetzt = time.monotonic()
    with _sperre:
        if not (sofort or (felder.get("prozent") or 0) >= 100) \
                and jetzt - _letzte.get(phase, -1e9) < FORTSCHRITT_ABSTAND_S:
            return
        _letzte[phase] = jetzt
    senden("fortschritt", disc=disc, phase=phase, **felder)


# ---------------------------------------------------------------- stdin

def _leser_starten() -> None:
    global _leser
    with _sperre:
        if _leser is not None:
            return
        _leser = threading.Thread(target=_lesen, name="ereignisse-stdin", daemon=True)
    if threading.current_thread() is threading.main_thread():
        try:  # stopp wirkt über interrupt_main(), das nur mit einem Python-Handler für SIGINT etwas tut
            if signal.getsignal(signal.SIGINT) in (signal.SIG_IGN, signal.SIG_DFL, None):
                signal.signal(signal.SIGINT, signal.default_int_handler)
        except (ValueError, OSError):
            pass
    _leser.start()


def _lesen() -> None:
    try:
        while True:
            zeile = sys.stdin.readline()
            if not zeile:
                break
            zeile = zeile.strip()
            if zeile:
                _verteilen(zeile)
    except Exception:
        pass
    _alle_abbrechen()  # EOF: offene Fragen mit EOFError beenden (wie input() ohne stdin)


def _verteilen(zeile: str) -> None:
    try:
        n = json.loads(zeile)
        if not isinstance(n, dict):
            raise ValueError("kein Objekt")
    except ValueError:
        meldung("warnung", f"Ungültige Nachricht auf stdin ignoriert: {zeile[:100]}")
        return
    typ = n.get("typ")
    if typ == "antwort":
        fid = str(n.get("id"))
        with _sperre:
            p = _offen.pop(fid, None)
        if p is None:
            meldung("warnung", f"Antwort auf unbekannte oder erledigte Frage {fid} ignoriert")
            return
        w = n.get("wert")
        p.wert = "" if w is None else str(w)
        p.ereignis.set()
    elif typ == "stopp":
        _alle_abbrechen()
        _thread.interrupt_main()
    elif typ == "einstellung":
        name, wert = str(n.get("name")), str(n.get("wert"))
        with _sperre:
            setzen = _einstellungen.get(name)
            if setzen is None:  # noch nicht angemeldet (kommt evtl. direkt nach dem Start): vormerken
                _vorgemerkt[name] = wert
                return
        _einstellung_setzen(name, setzen, wert)
    else:
        meldung("warnung", f"Unbekannte Nachricht auf stdin ignoriert: typ={typ!r}")


_einstellungen: dict[str, "Callable[[str], str]"] = {}
_vorgemerkt: dict[str, str] = {}  # Werte, die vor dem Anmelden der Einstellung kamen


def _einstellung_setzen(name: str, setzen, wert: str) -> None:
    try:
        wert = setzen(wert)
    except ValueError as e:
        meldung("warnung", f"Einstellung {name}={wert!r} abgelehnt: {e}")
        return
    senden("einstellung", name=name, wert=wert)  # Bestätigung mit dem jetzt gültigen Wert


def einstellung_anmelden(name: str, setzen, aktuell: str) -> None:
    """Macht eine Einstellung zur Laufzeit umschaltbar (stdin {"typ":"einstellung","name":…,"wert":…}).

    setzen(wert) übernimmt den Wert und gibt den gültigen Wert zurück, ValueError lehnt ab. Läuft sonst
    im stdin-Thread. Meldet den aktuellen Wert sofort als Ereignis "einstellung". Kam für diesen Namen
    schon vorher ein Wert über stdin, wird er jetzt übernommen. Unbekannte Namen werden still vorgemerkt."""
    with _sperre:
        _einstellungen[name] = setzen
        vorher = _vorgemerkt.pop(name, None)
    senden("einstellung", name=name, wert=aktuell)
    if vorher is not None:
        _einstellung_setzen(name, setzen, vorher)


def _alle_abbrechen() -> None:
    """Weckt alle wartenden Fragen mit EOFError (Wächter-Threads sollen nicht hängen bleiben)."""
    global _ende
    with _sperre:
        _ende = True
        offen = list(_offen.values())
        _offen.clear()
    for p in offen:
        p.fehler = True
        p.ereignis.set()


# ---------------------------------------------------------------- Fragen

def _optionen(optionen) -> list[dict]:
    erg = []
    for o in optionen or []:
        if isinstance(o, dict):
            e = {"wert": o["wert"], "text": o.get("text", o["wert"])}
            if o.get("hinweis"):
                e["hinweis"] = o["hinweis"]
            erg.append(e)
        else:
            e = {"wert": o[0], "text": o[1] if len(o) > 1 else o[0]}
            if len(o) > 2 and o[2]:
                e["hinweis"] = o[2]  # Folge dieser Wahl
            erg.append(e)
    return erg


def frage(art: str, text: str, optionen=None, freitext: bool = False, vorgabe: str | None = None,
          disc: str | None = None, prompt: str | None = None, details: str | None = None,
          empfehlung: str | None = None) -> str:
    """Stellt eine Frage und liefert die Antwort als Text.

    Ohne Ereignis-Modus: input(prompt), exakt wie bisher (der Aufrufer übergibt den bisherigen
    Prompt-Text). Im Ereignis-Modus: Ereignis "frage" senden, auf die Antwort mit passender id warten,
    "frage_erledigt" senden. Leere Antwort = vorgabe (sonst ""). Wirft EOFError, wenn stdin
    geschlossen wird oder stopp kommt (wie input() ohne Eingabe).
    """
    global _zaehler
    if not AKTIV:
        return input(prompt if prompt is not None else text + " ")
    _leser_starten()
    p = _Offen()
    p.art = art
    with _sperre:
        if _ende:
            raise EOFError("stdin geschlossen")
        _zaehler += 1
        fid = f"q{_zaehler}"
        _offen[fid] = p
    senden("frage", id=fid, art=art, text=text, optionen=_optionen(optionen), freitext=bool(freitext),
           vorgabe=vorgabe, disc=disc, details=details, empfehlung=empfehlung)
    try:
        while not p.ereignis.wait(0.25):  # mit Zeitgrenze, damit KeyboardInterrupt (stopp) ankommt
            pass
    except BaseException:
        with _sperre:
            _offen.pop(fid, None)
        senden("frage_erledigt", id=fid, wert=None, durch="programm")
        raise
    if p.fehler:
        senden("frage_erledigt", id=fid, wert=None, durch="programm")
        raise EOFError("stdin geschlossen oder gestoppt")
    wert = p.wert if p.wert.strip() else (vorgabe if vorgabe is not None else "")
    senden("frage_erledigt", id=fid, wert=wert, durch=p.durch)
    return wert


def offene_beantworten(art: str, wert: str, durch: str = "einstellung") -> int:
    """Beantwortet alle offenen Fragen dieser Art selbst (z.B. nach dem Umschalten einer Einstellung,
    die die Frage überflüssig macht). frage_erledigt meldet durch="einstellung". Rückgabe: Anzahl."""
    with _sperre:
        treffer = [(fid, p) for fid, p in _offen.items() if p.art == art]
        for fid, _ in treffer:
            del _offen[fid]
    for _, p in treffer:
        p.wert, p.durch = wert, durch
        p.ereignis.set()
    return len(treffer)


# ---------------------------------------------------------------- Metadaten und Lesekarte

_NAMEN = {"cd": "Musik-CD", "rekorder": "Rekorder", "sonstige_dateien": "Nicht kopierte Dateien"}
_zusatz: dict = {}  # zuletzt übergebene Zusatzgruppen (z.B. Laufwerk), gelten für weitere Aufrufe


def _wert(name: str, v) -> str:
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, int) and (name == "byte" or name.endswith("_byte")):
        return f"{v / 1e9:.2f} GB ({v:,} Byte)".replace(",", ".").replace(".", ",", 1) if v >= 1e6 else f"{v} Byte"
    if isinstance(v, (int, float)) and name.endswith("_s"):
        s = int(round(v))
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d} min"
    if isinstance(v, float):
        return f"{v:g}".replace(".", ",")
    return str(v)


def metadaten_felder(daten: dict, zusatz: dict | None = None, titel: list | None = None) -> list[dict]:
    """Flacht alles, was über eine Disc bekannt ist, zu Feldern {gruppe, name, wert} ab.

    zusatz: weitere Gruppen {"Laufwerk": {...}}. titel: Titelliste statt daten["titel"] (z.B. geplante
    Titel samt schon kopierter Angaben). Leere Werte entfallen, verschachtelte Werte bekommen
    Punkt-Namen (aufnahme.sender), Listen aus Listen nur ihre Anzahl."""
    from dataclasses import asdict, is_dataclass
    felder: list[dict] = []

    def add(gruppe: str, name: str, v) -> None:
        if v is None or v == "" or v == [] or v == {}:
            return
        felder.append({"gruppe": gruppe, "name": name, "wert": _wert(name, v)})

    def flach(gruppe: str, praefix: str, obj) -> None:
        if is_dataclass(obj):
            obj = asdict(obj)
        if not isinstance(obj, dict):
            add(gruppe, praefix.rstrip(".") or "wert", obj)
            return
        for k, v in obj.items():
            if is_dataclass(v):
                v = asdict(v)
            if isinstance(v, dict):
                flach(gruppe, f"{praefix}{k}.", v)
            elif isinstance(v, (list, tuple)):
                if v and all(isinstance(x, (str, int, float)) for x in v):
                    add(gruppe, praefix + k, ", ".join(map(str, v)))
                elif v:
                    add(gruppe, praefix + k, f"{len(v)} Einträge")
            else:
                add(gruppe, praefix + k, v)

    for g, inhalt in (zusatz or {}).items():
        flach(g, "", inhalt)
    oben = {k: v for k, v in daten.items() if k not in ("titel", "aussortiert", "hinweise")
            and not isinstance(v, dict)}
    flach("Disc", "", oben)
    for k, v in daten.items():
        if isinstance(v, dict) and k not in ("titel",):
            flach(_NAMEN.get(k, k), "", v)
    for i, t in enumerate(titel if titel is not None else daten.get("titel") or [], 1):
        d = asdict(t) if is_dataclass(t) else t
        flach(f"Titel {d.get('nr', i) if isinstance(d, dict) else i}", "", d)
    for a in daten.get("aussortiert") or []:
        if isinstance(a, dict):
            name = f"DVD-Titel {a.get('dvd_titel', '?')}"
            add("Aussortiert", name, ", ".join(f"{k}: {_wert(k, v)}" for k, v in a.items() if k != "dvd_titel"))
    for i, h in enumerate(daten.get("hinweise") or [], 1):
        add("Hinweise", str(i), h)
    return felder


def metadaten(disc: str | None, daten: dict, zusatz: dict | None = None, titel: list | None = None) -> None:
    """Ereignis "metadaten": vollständiger Stand (ersetzt den vorigen). Ohne Ereignis-Modus: nichts."""
    if not AKTIV:
        return
    if zusatz is not None:
        _zusatz.clear()
        _zusatz.update(zusatz)
    try:
        felder = metadaten_felder(daten, _zusatz, titel)
    except Exception as e:  # Anzeige darf das Einlesen nie stören
        felder = [{"gruppe": "Fehler", "name": "metadaten", "wert": f"{type(e).__name__}: {e}"}]
    senden("metadaten", disc=disc, felder=felder)


class Lesekarte:
    """Echte Positionen auf der Disc für die Disc-Grafik: welche Sektoren gelesen, fehlerhaft oder
    übersprungen sind, in 2048-Byte-Sektoren ab Anfang des Dateisystems (innen nach außen).

    lage(pfad) liefert [(byte_von, byte_bis, sektor_start)] einer Datei (einlesen.datei_lage) oder None.
    Gesendet wird höchstens ca. 1x pro Sekunde (Ereignis "lesekarte", nur neue Bereiche); das erste
    Ereignis enthält zusätzlich "geplant": wo die zu lesenden Dateien liegen."""

    def __init__(self, disc, sektoren_belegt, sektoren_max, lage) -> None:
        self.disc, self.belegt, self.max = disc, sektoren_belegt, sektoren_max
        self._lage_fn = lage
        self._lagen: dict = {}
        self._neu: list[list] = []
        self._geplant: list[list[int]] | None = None
        self._letzt = 0.0

    def _sektoren(self, pfad: str, von: int, bis: int) -> list[list[int]]:
        if pfad not in self._lagen:
            try:
                self._lagen[pfad] = self._lage_fn(pfad)
            except Exception:
                self._lagen[pfad] = None
        aus = []
        for dv, db, s0 in self._lagen[pfad] or []:
            a, b = max(von, dv), min(bis, db)
            if a < b:
                aus.append([s0 + (a - dv) // 2048, s0 + (b - dv + 2047) // 2048])
        return aus

    def geplant(self, dateien) -> None:
        """dateien: [(pfad, byte_von, byte_bis)], die gelesen werden sollen."""
        if not AKTIV:
            return
        bereiche = sorted(s for p, a, b in dateien for s in self._sektoren(p, a, b))
        zusammen: list[list[int]] = []
        for a, b in bereiche:
            if zusammen and a <= zusammen[-1][1]:
                zusammen[-1][1] = max(zusammen[-1][1], b)
            else:
                zusammen.append([a, b])
        self._geplant = zusammen
        self.senden(immer=True)

    def bereich(self, pfad: str, von: int, bis: int, zustand: str) -> None:
        """zustand: ok, fehler, uebersprungen. von/bis: Byte in der Datei pfad."""
        if not AKTIV or bis <= von:
            return
        for a, b in self._sektoren(pfad, von, bis):
            if self._neu and self._neu[-1][2] == zustand and self._neu[-1][1] == a:
                self._neu[-1][1] = b
            else:
                self._neu.append([a, b, zustand])
        if time.monotonic() - self._letzt >= FORTSCHRITT_ABSTAND_S:
            self.senden()

    def senden(self, immer: bool = False) -> None:
        if not AKTIV or not (self._neu or immer):
            return
        felder = {"disc": self.disc, "sektoren_belegt": self.belegt, "sektoren_max": self.max, "bereiche": self._neu}
        if self._geplant is not None:
            felder["geplant"] = self._geplant
            self._geplant = None  # nur einmal
        senden("lesekarte", **felder)
        self._neu = []
        self._letzt = time.monotonic()
