using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Collections.ObjectModel;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.Kern;
using Microsoft.Win32;

namespace DiscDigitalisierung.App.ViewModels;

/// <summary>Bearbeitungskopie der <see cref="Einstellungen"/>; erst „Speichern“ schreibt zurück.</summary>
public sealed partial class EinstellungenViewModel : ObservableObject
{
    const string ZeitfensterArg = "--zeitfenster";
    readonly Einstellungen _e;

    [ObservableProperty] string _pythonPfad;
    [ObservableProperty] string _skriptOrdner;
    [ObservableProperty] string _arbeitsOrdner;
    [ObservableProperty] string _dvdZiel;
    [ObservableProperty] string _musikZiel;
    [ObservableProperty] string _laufwerk;
    [ObservableProperty] string _zeitfenster = "";
    [ObservableProperty] string _weitereEinlesenArgumente = "";
    [ObservableProperty] string _weitereUmwandelnArgumente = "";
    [ObservableProperty] bool _umwandelnAutomatischStarten;
    [ObservableProperty] int _netzwerkIntervallSekunden;
    [ObservableProperty] string _fehlerText = "";
    [ObservableProperty] int _klangLautstaerke;

    /// <summary>Je Anlass eine Zeile: Auswahl, ggf. eigene Datei, Probehören.</summary>
    public ObservableCollection<KlangZeile> KlangZeilen { get; } = [];
    public IReadOnlyList<KlangOption> KlangOptionen => Klaenge.Optionen;

    /// <summary>true = gespeichert, false = abgebrochen.</summary>
    public event Action<bool>? Schliessen;

    public EinstellungenViewModel(Einstellungen e)
    {
        _e = e;
        _pythonPfad = e.PythonPfad;
        _skriptOrdner = e.SkriptOrdner;
        _arbeitsOrdner = e.ArbeitsOrdner;
        _dvdZiel = e.DvdZiel;
        _musikZiel = e.MusikZiel;
        _laufwerk = e.Laufwerk;
        _umwandelnAutomatischStarten = e.UmwandelnAutomatischStarten;
        _netzwerkIntervallSekunden = e.NetzwerkIntervallSekunden;
        _klangLautstaerke = e.KlangLautstaerke;
        foreach (var a in Klaenge.Anlaesse)
            KlangZeilen.Add(new KlangZeile(a, Klaenge.Wahl(e.Klaenge, a.Id), () => KlangLautstaerke, t => FehlerText = t));

        _weitereEinlesenArgumente = string.Join(' ', e.EinlesenArgumente);

        var rest = new List<string>();
        var u = e.UmwandelnArgumente;
        for (var i = 0; i < u.Count; i++)
        {
            if (u[i] == ZeitfensterArg && i + 1 < u.Count) { _zeitfenster = u[++i]; continue; }
            rest.Add(u[i]);
        }
        _weitereUmwandelnArgumente = string.Join(' ', rest);
    }

    static List<string> Teilen(string s) => s.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();

    [RelayCommand]
    void Speichern()
    {
        _e.PythonPfad = PythonPfad.Trim();
        _e.SkriptOrdner = SkriptOrdner.Trim();
        _e.ArbeitsOrdner = ArbeitsOrdner.Trim();
        _e.DvdZiel = DvdZiel.Trim();
        _e.MusikZiel = MusikZiel.Trim();
        _e.Laufwerk = Laufwerk.Trim();
        _e.UmwandelnAutomatischStarten = UmwandelnAutomatischStarten;
        _e.NetzwerkIntervallSekunden = Math.Max(5, NetzwerkIntervallSekunden);
        _e.KlangLautstaerke = Math.Clamp(KlangLautstaerke, 0, 100);
        foreach (var z in KlangZeilen) _e.Klaenge[z.Anlass.Id] = z.Wert;

        _e.EinlesenArgumente = Teilen(WeitereEinlesenArgumente);

        var um = Teilen(WeitereUmwandelnArgumente);
        if (!string.IsNullOrWhiteSpace(Zeitfenster)) { um.Insert(0, Zeitfenster.Trim()); um.Insert(0, ZeitfensterArg); }
        _e.UmwandelnArgumente = um;

        try
        {
            _e.Speichern();
            Schliessen?.Invoke(true);
        }
        catch (Exception ex)
        {
            FehlerText = "Speichern fehlgeschlagen: " + ex.Message;
        }
    }

    [RelayCommand]
    void Abbrechen() => Schliessen?.Invoke(false);

    [RelayCommand]
    void Waehlen(string feld)
    {
        if (feld == "PythonPfad")
        {
            var d = new OpenFileDialog { Title = "Python auswählen", Filter = "Programme (*.exe)|*.exe|Alle Dateien|*.*" };
            if (d.ShowDialog() == true) PythonPfad = d.FileName;
            return;
        }
        var start = feld switch
        {
            "SkriptOrdner" => SkriptOrdner, "ArbeitsOrdner" => ArbeitsOrdner, "DvdZiel" => DvdZiel, _ => MusikZiel,
        };
        var ordner = new OpenFolderDialog { Title = "Ordner auswählen", InitialDirectory = Directory.Exists(start) ? start : null };
        if (ordner.ShowDialog() != true) return;
        switch (feld)
        {
            case "SkriptOrdner": SkriptOrdner = ordner.FolderName; break;
            case "ArbeitsOrdner": ArbeitsOrdner = ordner.FolderName; break;
            case "DvdZiel": DvdZiel = ordner.FolderName; break;
            default: MusikZiel = ordner.FolderName; break;
        }
    }
}

/// <summary>Klang für einen Anlass im Einstellungsdialog.</summary>
public sealed partial class KlangZeile : ObservableObject
{
    readonly Func<int> _lautstaerke;
    readonly Action<string> _fehler;

    public KlangAnlass Anlass { get; }
    [ObservableProperty] [NotifyPropertyChangedFor(nameof(IstDatei))] KlangOption _auswahl;
    [ObservableProperty] string _datei = "";

    public bool IstDatei => Auswahl.Id == Klaenge.DateiOption;

    /// <summary>Wert für <see cref="Einstellungen.Klaenge"/>.</summary>
    public string Wert => IstDatei ? Klaenge.DateiPraefix + Datei.Trim() : Auswahl.Id;

    public KlangZeile(KlangAnlass anlass, string wert, Func<int> lautstaerke, Action<string> fehler)
    {
        Anlass = anlass;
        _lautstaerke = lautstaerke;
        _fehler = fehler;
        if (wert.StartsWith(Klaenge.DateiPraefix, StringComparison.Ordinal))
        {
            _auswahl = Klaenge.Optionen.First(o => o.Id == Klaenge.DateiOption);
            _datei = wert[Klaenge.DateiPraefix.Length..];
        }
        else
            _auswahl = Klaenge.Optionen.FirstOrDefault(o => o.Id == wert) ?? Klaenge.Optionen[0];
    }

    partial void OnAuswahlChanged(KlangOption value)
    {
        if (IstDatei && string.IsNullOrWhiteSpace(Datei)) DateiWaehlen();
    }

    [RelayCommand]
    void DateiWaehlen()
    {
        var d = new OpenFileDialog
        {
            Title = $"Klang für „{Anlass.Name}“ auswählen",
            Filter = "Klänge (*.wav;*.mp3;*.wma;*.m4a;*.ogg)|*.wav;*.mp3;*.wma;*.m4a;*.ogg|Alle Dateien|*.*",
        };
        if (d.ShowDialog() == true) Datei = d.FileName;
    }

    [RelayCommand]
    void Probehoeren()
    {
        var f = Klaenge.SpielenWert(Wert, _lautstaerke());
        _fehler(f ?? "");
    }
}
