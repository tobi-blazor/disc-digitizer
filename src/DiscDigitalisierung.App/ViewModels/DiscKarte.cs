using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.ViewModels;

/// <summary>Lesestand einer Disc für die Disc-Grafik, in echten Sektorpositionen (2048 Byte, ab Anfang des
/// Dateisystems, innen nach außen). Bereiche werden überschrieben, wenn ein späterer Zustand kommt (z.B. erst
/// „ausstehend“, nach dem Nachlesen „ok“ oder „fehler“). Ohne Lesekarte (Musik-CD, MakeMKV, ältere Skripte)
/// zeigt sie nur den Fortschritt. Nur auf dem UI-Thread benutzen.</summary>
public sealed class DiscKarte
{
    public enum Feld { Unbeschrieben, NichtGelesen, Geplant, Teilweise, Ok, Nachlesen, Uebersprungen, Fehler }

    // nicht überlappende Intervalle, sortiert nach Von
    readonly List<Lesebereich> _bereiche = [];
    readonly List<Lesebereich> _geplant = [];

    public long SektorenMax { get; private set; }
    public long SektorenBelegt { get; private set; }
    public double Prozent { get; private set; }
    public bool HatLage => SektorenMax > 0;

    /// <summary>Zuletzt gelesener Sektor (Leseposition).</summary>
    public long Position { get; private set; } = -1;

    public long OkSektoren { get; private set; }
    public long FehlerSektoren { get; private set; }
    public long NachlesenSektoren { get; private set; }
    public long UebersprungenSektoren { get; private set; }
    public long GeplantSektoren { get; private set; }

    public event Action? Geaendert;

    public void Zuruecksetzen()
    {
        _bereiche.Clear(); _geplant.Clear();
        SektorenMax = SektorenBelegt = 0; Prozent = 0; Position = -1;
        Zaehlen();
        Geaendert?.Invoke();
    }

    public void Hinzufuegen(LesekarteEreignis e)
    {
        if (e.SektorenMax is > 0) SektorenMax = e.SektorenMax.Value;
        if (e.SektorenBelegt is > 0) SektorenBelegt = e.SektorenBelegt.Value;
        if (e.Geplant.Count > 0)
        {
            _geplant.Clear();
            _geplant.AddRange(e.Geplant.OrderBy(b => b.Von));
        }
        foreach (var b in e.Bereiche)
        {
            Setzen(b);
            Position = b.Bis - 1;
            if (b.Bis > SektorenMax) SektorenMax = b.Bis;
        }
        Zaehlen();
        Geaendert?.Invoke();
    }

    public void ProzentSetzen(double? prozent)
    {
        if (prozent is not { } p || Math.Abs(p - Prozent) < 0.1) return;
        Prozent = p;
        if (!HatLage) Geaendert?.Invoke();
    }

    /// <summary>Zustand des Anteils [von, bis) der Kapazität (0..1). Schlimmster Zustand gewinnt.</summary>
    public Feld Zustand(double von, double bis)
    {
        if (!HatLage)
        {   // nur Fortschritt: Anteil = Lesefortschritt, ohne echte Lage
            var p = Prozent / 100.0;
            return p >= bis ? Feld.Ok : p > von ? Feld.Teilweise : Prozent > 0 ? Feld.Geplant : Feld.Unbeschrieben;
        }
        long a = (long)(von * SektorenMax), b = Math.Max(a + 1, (long)(bis * SektorenMax));
        long ok = 0;
        bool fehler = false, gelassen = false, nachlesen = false;
        for (var i = Erster(_bereiche, a); i < _bereiche.Count && _bereiche[i].Von < b; i++)
        {
            var x = _bereiche[i];
            var anteil = Math.Min(b, x.Bis) - Math.Max(a, x.Von);
            switch (x.Zustand)
            {
                case "fehler": fehler = true; break;
                case "uebersprungen": gelassen = true; break;
                case "ausstehend": nachlesen = true; break;
                default: ok += anteil; break;
            }
        }
        if (fehler) return Feld.Fehler;
        if (gelassen) return Feld.Uebersprungen;
        if (nachlesen) return Feld.Nachlesen;
        long geplant = 0;
        for (var i = Erster(_geplant, a); i < _geplant.Count && _geplant[i].Von < b; i++)
            geplant += Math.Min(b, _geplant[i].Bis) - Math.Max(a, _geplant[i].Von);
        if (ok > 0) return ok >= Math.Max(1, geplant) * 0.98 ? Feld.Ok : Feld.Teilweise;
        if (geplant > 0) return Feld.Geplant;
        return a < SektorenBelegt ? Feld.NichtGelesen : Feld.Unbeschrieben;
    }

    static int Erster(List<Lesebereich> l, long a)
    {
        int lo = 0, hi = l.Count;
        while (lo < hi)
        {
            var m = (lo + hi) / 2;
            if (l[m].Bis <= a) lo = m + 1; else hi = m;
        }
        return lo;
    }

    /// <summary>Überschreibt [Von, Bis) mit dem neuen Zustand, gleiche Nachbarn werden zusammengefasst.</summary>
    void Setzen(Lesebereich n)
    {
        var i = Erster(_bereiche, n.Von);
        var neu = new List<Lesebereich>(3);
        var j = i;
        for (; j < _bereiche.Count && _bereiche[j].Von < n.Bis; j++)
        {
            var x = _bereiche[j];
            if (x.Von < n.Von) neu.Add(x with { Bis = n.Von });
            if (x.Bis > n.Bis) neu.Add(x with { Von = n.Bis });
        }
        _bereiche.RemoveRange(i, j - i);
        neu.Add(n);
        neu.Sort((p, q) => p.Von.CompareTo(q.Von));
        _bereiche.InsertRange(i, neu);
        // Nachbarn mit gleichem Zustand zusammenfassen (nur um die Einfügestelle)
        for (var k = Math.Max(0, i - 1); k < Math.Min(_bereiche.Count - 1, i + neu.Count); )
        {
            if (_bereiche[k].Bis == _bereiche[k + 1].Von && _bereiche[k].Zustand == _bereiche[k + 1].Zustand)
            {
                _bereiche[k] = _bereiche[k] with { Bis = _bereiche[k + 1].Bis };
                _bereiche.RemoveAt(k + 1);
            }
            else k++;
        }
    }

    void Zaehlen()
    {
        long ok = 0, f = 0, n = 0, u = 0;
        foreach (var x in _bereiche)
        {
            var l = x.Bis - x.Von;
            switch (x.Zustand)
            {
                case "fehler": f += l; break;
                case "ausstehend": n += l; break;
                case "uebersprungen": u += l; break;
                default: ok += l; break;
            }
        }
        OkSektoren = ok; FehlerSektoren = f; NachlesenSektoren = n; UebersprungenSektoren = u;
        GeplantSektoren = _geplant.Sum(x => x.Bis - x.Von);
    }
}
