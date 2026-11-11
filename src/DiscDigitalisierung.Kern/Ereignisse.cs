namespace DiscDigitalisierung.Kern;

// Modelle zum Ereignis-Protokoll (docs/ereignis-protokoll.md, Version 1).
// Gemeinsamer Vertrag aller Projekte: Änderungen nur nach Absprache (siehe docs/ablaufplan.md).

/// <summary>Welches Python-Skript ein Ereignis geschickt hat.</summary>
public enum SkriptArt { Einlesen, Umwandeln }

public enum ProzessZustand { Gestoppt, Startet, Laeuft, Beendet, Abgestuerzt }

public enum MeldungsStufe { Info, Hinweis, Warnung, Fehler }

/// <summary>Basis aller Ereignisse. <see cref="Zeit"/> kommt aus dem JSON (Feld "zeit"), sonst Empfangszeit.</summary>
public abstract record Ereignis
{
    public DateTimeOffset Zeit { get; init; } = DateTimeOffset.Now;
    public SkriptArt Skript { get; init; }
}

public sealed record StartEreignis(int Pid, IReadOnlyList<string> Argumente) : Ereignis;

/// <param name="Zustand">leer, disc, haengt, ohne_inhalt, verarbeitet</param>
public sealed record LaufwerkEreignis(string Zustand, string? Label, string? Medium, double? SeitSekunden) : Ereignis;

/// <param name="Typ">dvd-video, daten, audio</param>
/// <param name="Phase">einlesen, umwandeln, einsortieren</param>
public sealed record DiscEreignis(string Disc, string? Beschriftung, string? Label, string? Typ, long? Seriennummer,
    string Phase) : Ereignis;

public sealed record FortschrittEreignis(string? Disc, string Phase, string? Titel, int? TitelNr, int? TitelAnzahl,
    long? Byte, long? ByteGesamt, double? Prozent, long? FehlerSektoren, double? RateMbS, double? RestSekunden,
    string? Schritt) : Ereignis;

/// <param name="Hinweis">Folge dieser Wahl (z.B. „Datenverlust ≈ 3 s Video“), optional</param>
public sealed record Antwortoption(string Wert, string Text, string? Hinweis = null);

/// <param name="Art">lesefehler, beschriftung, disc_nummer, ueberschreiben, nummer_vergeben, trotzdem_einlesen,
/// musik_feld, musik_uebernehmen</param>
public sealed record FrageEreignis(string Id, string Art, string Text, IReadOnlyList<Antwortoption> Optionen,
    bool Freitext, string? Vorgabe, string? Disc) : Ereignis
{
    /// <summary>Erklärung: Lage, Umfang, Restzeit, Begründung der Empfehlung (mehrzeilig möglich).</summary>
    public string? Details { get; init; }

    /// <summary>Empfohlener Wert (eine der Optionen).</summary>
    public string? Empfehlung { get; init; }
}

/// <param name="Durch">programm, terminal, zeit</param>
public sealed record FrageErledigtEreignis(string Id, string Wert, string? Durch) : Ereignis;

public sealed record MeldungEreignis(MeldungsStufe Stufe, string Text, string? Disc) : Ereignis;

/// <param name="Status">eingelesen, problem, leer, unlesbar, uebersprungen, umgewandelt, einsortiert, pruefen</param>
public sealed record DiscEndeEreignis(string Disc, string Phase, string Status, string? Grund, string? Ziel,
    double? DauerSekunden) : Ereignis;

public sealed record WarteschlangeEreignis(IReadOnlyList<string> Wartend, string? Aktiv, DateTimeOffset? PausiertBis)
    : Ereignis;

public sealed record EndeEreignis(int Code) : Ereignis;

public sealed record MetadatenFeld(string Gruppe, string Name, string Wert);

/// <summary>Alles, was über die Disc bekannt ist (Laufwerk, Disc, Rekorder, Titel …). Ersetzt den vorigen Stand.</summary>
public sealed record MetadatenEreignis(string? Disc, IReadOnlyList<MetadatenFeld> Felder) : Ereignis;

/// <param name="Zustand">ok, ausstehend (übersprungen, wird nachgelesen), fehler (unlesbar), uebersprungen
/// (auf Wunsch gelassen), geplant (in <see cref="LesekarteEreignis.Geplant"/>)</param>
public sealed record Lesebereich(long Von, long Bis, string Zustand);

/// <summary>Echte Lage auf der Disc in 2048-Byte-Sektoren ab Anfang des Dateisystems (innen nach außen):
/// neue Bereiche seit dem letzten Ereignis, beim ersten Ereignis einer Disc dazu die geplanten Bereiche
/// (wo die zu lesenden Dateien liegen). SektorenBelegt = Länge des Dateisystems, SektorenMax = Kapazität.</summary>
public sealed record LesekarteEreignis(string? Disc, long? SektorenBelegt, long? SektorenMax,
    IReadOnlyList<Lesebereich> Geplant, IReadOnlyList<Lesebereich> Bereiche) : Ereignis;

/// <summary>Empfehlung zur Disc, z.B. Reinigen. Stufe: keine, optional, empfohlen, anderes_laufwerk.</summary>
public sealed record EmpfehlungEreignis(string? Disc, string Art, string Stufe, string Text) : Ereignis;

/// <summary>Aktueller Wert einer zur Laufzeit umschaltbaren Einstellung (beim Start und nach jeder Änderung).</summary>
/// <param name="Name">z.B. "nummern" (einlesen.py)</param>
public sealed record EinstellungEreignis(string Name, string Wert) : Ereignis;

/// <summary>Normale Textzeile (stdout) oder stderr (<see cref="Fehlerkanal"/>), auch unbekannte Ereignistypen.</summary>
public sealed record LogZeile(string Text, bool Fehlerkanal) : Ereignis;

/// <summary>Der Prozess selbst hat sich geändert (vom Host erzeugt, nicht von Python).</summary>
public sealed record ProzessEreignis(ProzessZustand Zustand, int? ExitCode, string? Text) : Ereignis;
