namespace DiscDigitalisierung.Kern;

/// <summary>Entscheidet, ob und wann ein abgestürztes Skript automatisch neu gestartet wird.
/// Die Wartezeit steigt mit jedem Absturz im Zeitfenster. Stürzt es zu oft hintereinander ab,
/// liegt der Fehler vermutlich nicht an einem kurzen Störfall: dann kein Neustart mehr, bis der
/// Nutzer selbst startet (<see cref="Zuruecksetzen"/>).</summary>
public sealed class NeustartRegel
{
    public static readonly TimeSpan[] Wartezeiten = [TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(60)];
    public static readonly TimeSpan Fenster = TimeSpan.FromMinutes(15);

    readonly List<DateTimeOffset> _abstuerze = [];

    /// <summary>Abstürze im aktuellen Zeitfenster.</summary>
    public int Anzahl => _abstuerze.Count;

    /// <summary>Meldet einen Absturz. Rückgabe: Wartezeit bis zum Neustart, oder null = nicht mehr neu starten.</summary>
    public TimeSpan? Absturz(DateTimeOffset jetzt)
    {
        _abstuerze.RemoveAll(t => jetzt - t > Fenster);
        _abstuerze.Add(jetzt);
        return _abstuerze.Count <= Wartezeiten.Length ? Wartezeiten[_abstuerze.Count - 1] : null;
    }

    public void Zuruecksetzen() => _abstuerze.Clear();
}
