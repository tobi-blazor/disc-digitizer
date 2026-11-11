using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.ViewModels;

public sealed class OptionViewModel(string wert, string text, string? hinweis, bool empfohlen, Action<string> antworten)
{
    public string Wert { get; } = wert;
    public string Text { get; } = text;
    /// <summary>Folge dieser Wahl, z.B. „Datenverlust ≈ 3 s Video“.</summary>
    public string Hinweis { get; } = hinweis ?? "";
    public bool HatHinweis => Hinweis != "";
    public bool Empfohlen { get; } = empfohlen;
    public IRelayCommand Befehl { get; } = new RelayCommand(() => antworten(wert));
}

/// <summary>Eine offene Frage eines Skripts.</summary>
public sealed partial class FrageViewModel : ObservableObject
{
    readonly SkriptSteuerung _skript;

    [ObservableProperty] string _eingabe;
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Offen))]
    bool _gesendet;

    public bool Offen => !Gesendet;

    public FrageViewModel(SkriptSteuerung skript, FrageEreignis f)
    {
        _skript = skript;
        Id = f.Id;
        Art = f.Art;
        Text = f.Text;
        Disc = f.Disc;
        Freitext = f.Freitext;
        Vorgabe = f.Vorgabe ?? "";
        _eingabe = Vorgabe;
        Details = f.Details ?? "";
        Optionen = f.Optionen.Select(o => new OptionViewModel(o.Wert, o.Text, o.Hinweis, o.Wert == f.Empfehlung, Senden)).ToList();
        MitHinweisen = Optionen.Any(o => o.HatHinweis);
        FreitextBefehl = new RelayCommand(() => Senden(string.IsNullOrWhiteSpace(Eingabe) ? Vorgabe : Eingabe.Trim()));
        UeberspringenBefehl = new RelayCommand(() => Senden("s"));
    }

    public SkriptArt Skript => _skript.Art;
    public string SkriptName => _skript.Name;
    public string Id { get; }
    public string Art { get; }
    public string Text { get; }
    public string? Disc { get; }
    public bool Freitext { get; }
    public string Vorgabe { get; }
    public IReadOnlyList<OptionViewModel> Optionen { get; }

    /// <summary>Erklärung und Begründung der Empfehlung (leer = keine).</summary>
    public string Details { get; }
    public bool HatDetails => Details != "";

    /// <summary>Optionen mit Folgen-Text: als große Knöpfe nebeneinander mit Erklärung.</summary>
    public bool MitHinweisen { get; }
    public bool OhneHinweise => !MitHinweisen;
    public IRelayCommand FreitextBefehl { get; }
    public IRelayCommand UeberspringenBefehl { get; }

    /// <summary>Für Freitextfragen, bei denen "s" überspringen bedeutet.</summary>
    public bool KannUeberspringen => Freitext && Art is "beschriftung" or "disc_nummer";

    public string Titel => $"{SkriptName}{(string.IsNullOrEmpty(Disc) ? "" : " · " + Disc)}";

    async void Senden(string wert)
    {
        if (Gesendet) return;
        Gesendet = true; // bleibt sichtbar bis frage_erledigt eintrifft
        await _skript.AntwortenAsync(Id, wert);
    }
}

/// <summary>Eintrag der Liste „Fehler &amp; Warnungen“.</summary>
public sealed partial class FehlerEintrag : ObservableObject
{
    [ObservableProperty] bool _gelesen;

    public FehlerEintrag(DateTimeOffset zeit, string skript, string? disc, string art, int schwere, string text)
    {
        Zeit = zeit; Skript = skript; Disc = disc ?? ""; Art = art; Schwere = schwere; Text = text;
    }

    public DateTimeOffset Zeit { get; }
    public string Skript { get; }
    public string Disc { get; }
    /// <summary>Warnung, Fehler, stderr, Absturz, Problem</summary>
    public string Art { get; }
    /// <summary>2 Warnung, 3 Fehler</summary>
    public int Schwere { get; }
    public string Text { get; }
}

public sealed record LogEintrag(DateTimeOffset Zeit, SkriptArt Skript, string Text, int Schwere)
{
    public string SkriptName => Skript == SkriptArt.Einlesen ? "einlesen" : "umwandeln";
}

public sealed record StatusZaehler(string Status, int Anzahl);

public sealed record ZielAnzeige(string Name, bool Erreichbar, string Info, string Pfad, string Fehler);
