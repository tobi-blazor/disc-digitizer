using DiscDigitalisierung.App.Attrappen;
using DiscDigitalisierung.Bestand;
using DiscDigitalisierung.Kern;
using DiscDigitalisierung.Prozesse;

namespace DiscDigitalisierung.App;

/// <summary>Kompositions-Punkt: stellt alle Dienste bereit, gegen die die Oberfläche gebaut ist.
/// Ohne Argument die echten Umsetzungen, mit <c>--demo</c> die Attrappen.</summary>
public sealed class Dienste
{
    public Einstellungen Einstellungen { get; }
    public ISkriptProzessFabrik ProzessFabrik { get; }
    public IBestand Bestand { get; }
    public INetzwerkMonitor Netzwerk { get; }
    public bool IstDemo { get; }

    /// <summary>Pfad des Sitzungsprotokolls (JSONL) oder null im Demo-Modus.</summary>
    public string? SitzungsprotokollPfad { get; }

    Dienste(Einstellungen einstellungen, ISkriptProzessFabrik fabrik, IBestand bestand, INetzwerkMonitor netzwerk,
        bool demo, string? protokollPfad = null)
    {
        Einstellungen = einstellungen;
        ProzessFabrik = fabrik;
        Bestand = bestand;
        Netzwerk = netzwerk;
        IstDemo = demo;
        SitzungsprotokollPfad = protokollPfad;
    }

    public static Dienste Erzeugen(string[] args)
    {
        // --einstellungen <datei>: andere Einstellungsdatei (Tests, zweites Laufwerk)
        var i = Array.FindIndex(args, a => a.Equals("--einstellungen", StringComparison.OrdinalIgnoreCase));
        var einstellungen = Einstellungen.Laden(i >= 0 && i + 1 < args.Length ? args[i + 1] : null);
        if (args.Contains("--demo", StringComparer.OrdinalIgnoreCase))
            return new Dienste(einstellungen, new DemoSkriptProzessFabrik(), new DemoBestand(), new DemoNetzwerk(), true);
        return Echt(einstellungen);
    }

    static Dienste Echt(Einstellungen e)
    {
        var protokoll = new SitzungsProtokoll();
        var fabrik = new ProtokollierendeFabrik(new SkriptProzessFabrik(e), protokoll);
        return new Dienste(e, fabrik, new Bestand.Bestand(e), new NetzwerkMonitor(e), false, protokoll.Pfad);
    }

    /// <summary>Hängt jeden erzeugten Prozess an das Sitzungsprotokoll. Das Protokoll lebt so lange wie das Programm.</summary>
    sealed class ProtokollierendeFabrik(ISkriptProzessFabrik innen, SitzungsProtokoll protokoll) : ISkriptProzessFabrik
    {
        public ISkriptProzess Erzeugen(SkriptArt art)
        {
            var p = innen.Erzeugen(art);
            protokoll.Anhaengen(p);
            return p;
        }
    }
}
