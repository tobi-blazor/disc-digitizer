using System.Diagnostics;

namespace DiscDigitalisierung.App.ViewModels;

/// <summary>Lesegeschwindigkeit der aktuellen Disc: aktueller Wert (aus der Änderung der gelesenen Byte, geglättet)
/// und Verlauf über die Position (0..1 der Disc), wie die Transferraten-Kurve gängiger Laufwerkstools.
/// Nur auf dem UI-Thread benutzen.</summary>
public sealed class Geschwindigkeit
{
    const double Glaettung = 0.4;       // Anteil des neuen Messwerts (exponentiell geglättet)
    const double DvdEinfach = 1.385;    // MB/s bei 1x DVD
    const double CdEinfach = 0.1764;    // MB/s bei 1x CD (Audio)

    readonly Stopwatch _uhr = Stopwatch.StartNew();
    readonly List<(double Position, double MbS)> _punkte = [];
    long? _letzteByte;
    double _letzteZeit;

    public double MbS { get; private set; }
    public double Hoechstwert { get; private set; }
    public bool IstCd { get; private set; }

    /// <summary>Skala des Balkens in MB/s: DVD 16x ≈ 22 MB/s, CD 52x ≈ 9 MB/s, mindestens der Höchstwert.</summary>
    public double Skala => Math.Max(IstCd ? 52 * CdEinfach : 16 * DvdEinfach, Hoechstwert);

    /// <summary>Vielfaches der einfachen Geschwindigkeit (z.B. 6,1x).</summary>
    public double Faktor => MbS / (IstCd ? CdEinfach : DvdEinfach);

    public IReadOnlyList<(double Position, double MbS)> Punkte => _punkte;

    public event Action? Geaendert;

    public void Zuruecksetzen(bool istCd)
    {
        IstCd = istCd;
        _punkte.Clear();
        _letzteByte = null;
        MbS = 0;
        Hoechstwert = 0;
        Geaendert?.Invoke();
    }

    /// <summary>Neuer Fortschritt: gelesene Byte der Disc und Position (0..1).</summary>
    public void Messen(long? byteGelesen, double? position)
    {
        if (byteGelesen is not { } b) return;
        var t = _uhr.Elapsed.TotalSeconds;
        if (_letzteByte is not { } vorher || b < vorher)
        {   // erster Wert oder neuer Durchgang: nur merken
            if (_letzteByte != null) _punkte.Clear();
            _letzteByte = b;
            _letzteZeit = t;
            return;
        }
        var dt = t - _letzteZeit;
        if (dt < 0.3) return;
        var neu = (b - vorher) / 1e6 / dt;
        MbS = _punkte.Count == 0 ? neu : Glaettung * neu + (1 - Glaettung) * MbS;
        Hoechstwert = Math.Max(Hoechstwert, MbS);
        if (position is { } p && b > vorher)
            _punkte.Add((Math.Clamp(p, 0, 1), MbS));
        _letzteByte = b;
        _letzteZeit = t;
        Geaendert?.Invoke();
    }

    /// <summary>Lesen steht (z.B. Nachlesen einzelner Sektoren, Laufwerk hängt): Geschwindigkeit fällt auf 0.</summary>
    public void Steht()
    {
        if (MbS == 0) return;
        MbS = 0;
        Geaendert?.Invoke();
    }
}
