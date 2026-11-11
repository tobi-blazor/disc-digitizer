namespace DiscDigitalisierung.Kern;

// Schnittstellen zwischen den Projekten. Die App kennt nur diese, die Umsetzungen liegen in
// DiscDigitalisierung.Prozesse und DiscDigitalisierung.Bestand. Änderungen nur nach Absprache.
// Ereignisse (event) können aus Hintergrund-Threads kommen: die App marshalt selbst auf den UI-Thread.

/// <summary>Ein laufendes Python-Skript (einlesen.py oder umwandeln.py) im Ereignis-Modus.</summary>
public interface ISkriptProzess : IAsyncDisposable
{
    SkriptArt Art { get; }
    ProzessZustand Zustand { get; }
    int? Pid { get; }

    /// <summary>Alle Ereignisse inkl. <see cref="LogZeile"/> und <see cref="ProzessEreignis"/>.</summary>
    event EventHandler<Ereignis>? EreignisEmpfangen;

    /// <summary>Startet das Skript. Wirft, wenn es schon läuft.</summary>
    Task StartenAsync(IReadOnlyList<string> argumente, CancellationToken ct = default);

    /// <summary>Sauber beenden: schickt "stopp", wartet bis <paramref name="warten"/>, beendet danach den
    /// eigenen Prozessbaum hart. Andere Prozesse werden nie angefasst.</summary>
    Task StoppenAsync(TimeSpan warten, CancellationToken ct = default);

    Task AntwortenAsync(string frageId, string wert, CancellationToken ct = default);

    /// <summary>Schaltet eine Einstellung zur Laufzeit um (stdin "einstellung"). Das Skript bestätigt mit
    /// <see cref="EinstellungEreignis"/> oder lehnt mit einer Warnung ab.</summary>
    Task EinstellenAsync(string name, string wert, CancellationToken ct = default);
}

public interface ISkriptProzessFabrik
{
    ISkriptProzess Erzeugen(SkriptArt art);
}

/// <summary>Eine Zeile aus fortschritt.csv, ergänzt um Angaben aus dem Ordner der Disc.</summary>
/// <param name="Ablage">1-roh, 2-fertig, 3-problem oder null (kein Ordner, z.B. einsortiert/leer)</param>
public sealed record DiscEintrag(
    string Disc,
    string Beschriftung,
    string Typ,
    int Titel,
    double Stunden,
    string Status,
    DateTimeOffset? Geaendert,
    string Hinweis,
    string? Ablage,
    string? Ordner,
    string? ProblemGrund,
    long? LesefehlerSektoren,
    long? GroesseByte);

/// <summary>Stand aller Discs aus DVD_ARBEIT (fortschritt.csv, 1-roh, 2-fertig, 3-problem). Nur lesend.</summary>
public interface IBestand : IDisposable
{
    IReadOnlyList<DiscEintrag> Discs { get; }
    DateTimeOffset? Stand { get; }

    /// <summary>Wahr, wenn die Sperre umwandeln.lock von einem Prozess gehalten wird.</summary>
    bool UmwandelnLaeuft { get; }

    /// <summary>Freier Platz im Arbeitsordner in Byte.</summary>
    long? ArbeitFreiByte { get; }

    event EventHandler? Geaendert;

    Task AktualisierenAsync(CancellationToken ct = default);

    /// <summary>Beobachtet den Arbeitsordner (FileSystemWatcher + Abfrage als Rückfall) und meldet Änderungen.</summary>
    void Beobachten();
}

/// <param name="Name">z.B. "Videos (DVD_ZIEL)", "Musik (MUSIK_ZIEL)"</param>
public sealed record ZielStatus(
    string Name,
    string Pfad,
    bool Erreichbar,
    long? FreiByte,
    long? GesamtByte,
    TimeSpan? Antwortzeit,
    DateTimeOffset GeprueftUm,
    string? Fehler);

/// <summary>Überwacht die Export-Ziele (Netzwerkfreigaben): erreichbar, Platz, Antwortzeit. Nur lesend.</summary>
public interface INetzwerkMonitor : IDisposable
{
    IReadOnlyList<ZielStatus> Ziele { get; }
    event EventHandler? Geaendert;
    Task PruefenAsync(CancellationToken ct = default);
    void Starten(TimeSpan intervall);
}
