using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using DiscDigitalisierung.App.Attrappen;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.ViewModels;

public sealed partial class MainViewModel : ObservableObject
{
    const int MaxLog = 5000;
    const int MaxFehler = 2000;
    const string Alle = "Alle";

    readonly Dienste _d;

    public MainViewModel(Dienste d)
    {
        _d = d;
        Einlesen = new SkriptSteuerung(SkriptArt.Einlesen, d.ProzessFabrik, () => d.Einstellungen.EinlesenArgumente);
        Umwandeln = new SkriptSteuerung(SkriptArt.Umwandeln, d.ProzessFabrik, () => d.Einstellungen.UmwandelnArgumente);
        Einlesen.EreignisEingetroffen += Verarbeiten;
        Umwandeln.EreignisEingetroffen += Verarbeiten;
        Umwandeln.PropertyChanged += (_, a) =>
        {
            if (a.PropertyName == nameof(SkriptSteuerung.Zustand)) AusserhalbPruefen();
        };

        Fragen.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HatFragen));
        DiscAnsicht = CollectionViewSource.GetDefaultView(Discs);
        DiscAnsicht.Filter = DiscFilter;
        ProtokollAnsicht = CollectionViewSource.GetDefaultView(Protokoll);
        ProtokollAnsicht.Filter = o => ProtokollFilter == Alle || ((LogEintrag)o).SkriptName == ProtokollFilter.ToLowerInvariant();
        StatusOptionen.Add(Alle);

        d.Bestand.Geaendert += (_, _) => Ui.Aufrufen(BestandAktualisieren);
        d.Netzwerk.Geaendert += (_, _) => Ui.Aufrufen(ZieleAktualisieren);
        Fenstertitel = "Disc-Digitalisierung" + (d.IstDemo ? " (Demo)" : "");
    }

    // ---- Skripte ----
    public SkriptSteuerung Einlesen { get; }
    public SkriptSteuerung Umwandeln { get; }
    public EinlesenBereich EinlesenAnsicht { get; } = new();
    public UmwandelnBereich UmwandelnAnsicht { get; } = new();
    public string Fenstertitel { get; }
    public bool IstDemo => _d.IstDemo;

    // ---- Nummern bei leeren Discs (einlesen.py --nummern, zur Laufzeit umschaltbar) ----
    public bool NummernImmer { get => _d.Einstellungen.Nummern == NummernModus.Immer; set { if (value) _ = NummernSetzenAsync(NummernModus.Immer); } }
    public bool NummernFragen { get => _d.Einstellungen.Nummern == NummernModus.Fragen; set { if (value) _ = NummernSetzenAsync(NummernModus.Fragen); } }
    public bool NummernNie { get => _d.Einstellungen.Nummern == NummernModus.Nie; set { if (value) _ = NummernSetzenAsync(NummernModus.Nie); } }
    [ObservableProperty] string _nummernHinweis = "";

    public static string NummernText(string modus) => modus switch
    {
        NummernModus.Immer => "Durchnummeriert: leere Discs verbrauchen ihre Nummer",
        NummernModus.Nie => "Erst testen: leere Discs bekommen keine Nummer",
        _ => "Nachfragen, ob die Nummer vergeben wird",
    };

    async Task NummernSetzenAsync(string wert)
    {
        if (_d.Einstellungen.Nummern == wert) return;
        _d.Einstellungen.Nummern = wert;
        NummernAnzeigen();
        try { if (!_d.IstDemo) _d.Einstellungen.Speichern(); }
        catch (Exception ex) { LogHinzufuegen(SkriptArt.Einlesen, "Einstellungen nicht gespeichert: " + ex.Message, 2, DateTimeOffset.Now); }
        if (Einlesen.Laeuft)
        {
            NummernHinweis = "wird übernommen …";
            await Einlesen.EinstellenAsync("nummern", wert);
        }
        else NummernHinweis = NummernText(wert) + " (gilt ab dem nächsten Start)";
    }

    void NummernAnzeigen()
    {
        OnPropertyChanged(nameof(NummernImmer));
        OnPropertyChanged(nameof(NummernFragen));
        OnPropertyChanged(nameof(NummernNie));
    }

    // ---- Fragen ----
    public ObservableCollection<FrageViewModel> Fragen { get; } = [];
    public bool HatFragen => Fragen.Count > 0;
    /// <summary>Wird ausgelöst, wenn eine neue Frage eintrifft (Ton und Taskleistenblinken macht das Fenster).</summary>
    public event Action? NeueFrage;

    // ---- Kopfleiste ----
    public ObservableCollection<ZielAnzeige> Ziele { get; } = [];
    [ObservableProperty] string _arbeitFreiText = "–";
    [ObservableProperty] string _standText = "–";

    // ---- Discs ----
    public ObservableCollection<DiscEintrag> Discs { get; } = [];
    public ICollectionView DiscAnsicht { get; }
    public ObservableCollection<string> StatusOptionen { get; } = [];
    public ObservableCollection<StatusZaehler> StatusZaehlerListe { get; } = [];
    [ObservableProperty] string _statusFilter = Alle;
    [ObservableProperty] string _suche = "";
    [ObservableProperty] string _discAnzahlText = "";

    partial void OnStatusFilterChanged(string value) => DiscAnsicht.Refresh();
    partial void OnSucheChanged(string value) => DiscAnsicht.Refresh();

    bool DiscFilter(object o)
    {
        var x = (DiscEintrag)o;
        if (StatusFilter != Alle && !string.Equals(x.Status, StatusFilter, StringComparison.OrdinalIgnoreCase)) return false;
        if (string.IsNullOrWhiteSpace(Suche)) return true;
        var s = Suche.Trim();
        return Enthaelt(x.Disc, s) || Enthaelt(x.Beschriftung, s) || Enthaelt(x.Hinweis, s) || Enthaelt(x.ProblemGrund, s);
    }

    static bool Enthaelt(string? text, string s) => text?.Contains(s, StringComparison.CurrentCultureIgnoreCase) == true;

    // ---- Fehler & Warnungen ----
    public ObservableCollection<FehlerEintrag> Fehler { get; } = [];
    public string FehlerTitel
    {
        get
        {
            var n = Fehler.Count(f => !f.Gelesen);
            return n > 0 ? $"Fehler & Warnungen ({n})" : "Fehler & Warnungen";
        }
    }
    public bool HatUngeleseneFehler => Fehler.Any(f => !f.Gelesen);

    void FehlerHinzufuegen(FehlerEintrag f)
    {
        f.PropertyChanged += (_, _) => FehlerZaehlerAktualisieren();
        Fehler.Insert(0, f);
        while (Fehler.Count > MaxFehler) Fehler.RemoveAt(Fehler.Count - 1);
        FehlerZaehlerAktualisieren();
    }

    void FehlerZaehlerAktualisieren()
    {
        OnPropertyChanged(nameof(FehlerTitel));
        OnPropertyChanged(nameof(HatUngeleseneFehler));
    }

    [RelayCommand]
    void AlleGelesen()
    {
        foreach (var f in Fehler) f.Gelesen = true;
    }

    // ---- Protokoll ----
    public ObservableCollection<LogEintrag> Protokoll { get; } = [];
    public ICollectionView ProtokollAnsicht { get; }
    public string[] ProtokollFilterOptionen { get; } = [Alle, "Einlesen", "Umwandeln"];
    [ObservableProperty] string _protokollFilter = Alle;
    [ObservableProperty] bool _mitscrollen = true;
    partial void OnProtokollFilterChanged(string value) => ProtokollAnsicht.Refresh();

    void LogHinzufuegen(SkriptArt art, string text, int schwere, DateTimeOffset zeit)
    {
        Protokoll.Add(new LogEintrag(zeit, art, text, schwere));
        if (Protokoll.Count > MaxLog + 250)
            for (var i = 0; i < 250; i++) Protokoll.RemoveAt(0);
    }

    // ---- Statusleiste ----
    public string SitzungsprotokollText => _d.SitzungsprotokollPfad is { } pfad
        ? "Sitzungsprotokoll: " + pfad
        : "Sitzungsprotokoll: keins (Demo-Modus)";
    [ObservableProperty] string _meldungText = "";

    // ---- Ablauf ----
    public async Task StartenAsync()
    {
        _d.Bestand.Beobachten();
        _d.Netzwerk.Starten(TimeSpan.FromSeconds(Math.Max(5, _d.Einstellungen.NetzwerkIntervallSekunden)));
        await _d.Bestand.AktualisierenAsync();
        await _d.Netzwerk.PruefenAsync();
        BestandAktualisieren();
        ZieleAktualisieren();

        await Einlesen.StartAsync();
        if (_d.Einstellungen.UmwandelnAutomatischStarten && !_d.Bestand.UmwandelnLaeuft)
            await Umwandeln.StartAsync();
    }

    public bool IrgendeinSkriptLaeuft => Einlesen.Laeuft || Umwandeln.Laeuft;

    public async Task AllesStoppenAsync()
    {
        await Task.WhenAll(Einlesen.StoppAsync(), Umwandeln.StoppAsync());
        _d.Bestand.Dispose();
        _d.Netzwerk.Dispose();
    }

    void AusserhalbPruefen() =>
        Umwandeln.LaeuftAusserhalb = _d.Bestand.UmwandelnLaeuft && !Umwandeln.Laeuft;

    void BestandAktualisieren()
    {
        var liste = _d.Bestand.Discs;
        Discs.Clear();
        foreach (var x in liste) Discs.Add(x);

        var gruppen = liste.GroupBy(x => x.Status.ToLowerInvariant()).OrderByDescending(g => g.Count()).ToList();
        StatusZaehlerListe.Clear();
        foreach (var g in gruppen) StatusZaehlerListe.Add(new StatusZaehler(g.Key, g.Count()));
        var optionen = new[] { Alle }.Concat(gruppen.Select(g => g.Key).OrderBy(s => s)).ToList();
        if (!optionen.SequenceEqual(StatusOptionen))
        {
            StatusOptionen.Clear();
            foreach (var o in optionen) StatusOptionen.Add(o);
            if (!optionen.Contains(StatusFilter)) StatusFilter = Alle;
        }
        DiscAnzahlText = $"{liste.Count} Discs";
        StandText = _d.Bestand.Stand is { } s ? $"Bestand: {s.LocalDateTime:HH:mm:ss}" : "Bestand: –";
        ArbeitFreiText = _d.Bestand.ArbeitFreiByte is { } f ? Ui.Groesse(f) + " frei" : "–";
        AusserhalbPruefen();
        DiscAnsicht.Refresh();
    }

    void ZieleAktualisieren()
    {
        Ziele.Clear();
        foreach (var z in _d.Netzwerk.Ziele)
        {
            var info = z.Erreichbar
                ? $"{Ui.Groesse(z.FreiByte)} frei" + (z.Antwortzeit is { } a ? $" · {a.TotalMilliseconds:0} ms" : "")
                : "nicht erreichbar";
            Ziele.Add(new ZielAnzeige(z.Name, z.Erreichbar, info, z.Pfad, z.Fehler ?? ""));
        }
        ZielTabelle.Clear();
        foreach (var z in _d.Netzwerk.Ziele) ZielTabelle.Add(z);
    }

    public ObservableCollection<ZielStatus> ZielTabelle { get; } = [];

    [RelayCommand]
    void StatusFilterSetzen(string? status) => StatusFilter = string.IsNullOrEmpty(status) ? Alle : status;

    [RelayCommand]
    void OrdnerOeffnen(DiscEintrag? disc)
    {
        if (disc == null) return;
        var ordner = disc.Ordner;
        if (string.IsNullOrEmpty(ordner) && disc.Ablage != null && _d.Einstellungen.ArbeitsOrdner != "")
            ordner = Path.Combine(_d.Einstellungen.ArbeitsOrdner, disc.Ablage, disc.Disc);
        if (string.IsNullOrEmpty(ordner) || !Directory.Exists(ordner))
        {
            MeldungText = $"Für {disc.Disc} gibt es keinen Ordner.";
            return;
        }
        MeldungText = "";
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{ordner}\"") { UseShellExecute = true });
    }

    [RelayCommand]
    void EinstellungenOeffnen()
    {
        if (Dialoge.EinstellungenZeigen(_d.Einstellungen))
            MeldungText = "Einstellungen gespeichert. Neue Argumente gelten ab dem nächsten Start der Skripte.";
    }

    [RelayCommand]
    void AbsturzSimulieren(string art)
    {
        var s = art == "einlesen" ? Einlesen : Umwandeln;
        (s.Prozess as DemoSkriptProzess)?.AbsturzSimulieren();
    }

    // ---- Klänge ----
    /// <summary>Anlass für den Klang am Ende einer Disc-Phase, null = kein Klang.</summary>
    static string? KlangAnlass(DiscEndeEreignis de) => (de.Phase, de.Status) switch
    {
        (_, "problem" or "unlesbar") => "problem",
        ("einlesen", "eingelesen" or "einsortiert") => "fertig",
        ("einlesen", "leer" or "uebersprungen") => "leer",
        ("einsortieren", _) => "umgewandelt",
        _ => null,
    };

    void KlangSpielen(string? anlass)
    {
        if (anlass == null) return;
        var fehler = Klaenge.Spielen(_d.Einstellungen.Klaenge, anlass, _d.Einstellungen.KlangLautstaerke);
        if (fehler != null) LogHinzufuegen(SkriptArt.Einlesen, fehler, 2, DateTimeOffset.Now);
    }

    // ---- Ereignisse ----
    void Verarbeiten(SkriptSteuerung skript, Ereignis e)
    {
        var art = skript.Art;
        var bereichE = art == SkriptArt.Einlesen;
        if (bereichE) EinlesenAnsicht.Verarbeiten(e); else UmwandelnAnsicht.Verarbeiten(e);

        switch (e)
        {
            case LogZeile l:
                LogHinzufuegen(art, l.Text, l.Fehlerkanal ? 3 : 0, l.Zeit);
                if (l.Fehlerkanal && !string.IsNullOrWhiteSpace(l.Text))
                    FehlerHinzufuegen(new FehlerEintrag(l.Zeit, skript.Name, null, "stderr", 3, l.Text));
                break;
            case MeldungEreignis m:
                var schwere = m.Stufe switch { MeldungsStufe.Fehler => 3, MeldungsStufe.Warnung => 2, MeldungsStufe.Hinweis => 1, _ => 0 };
                LogHinzufuegen(art, $"[{m.Stufe}] {m.Text}", schwere, m.Zeit);
                if (m.Stufe is MeldungsStufe.Warnung or MeldungsStufe.Fehler)
                    FehlerHinzufuegen(new FehlerEintrag(m.Zeit, skript.Name, m.Disc, m.Stufe == MeldungsStufe.Fehler ? "Fehler" : "Warnung", schwere, m.Text));
                break;
            case FrageEreignis f:
                KlangSpielen("frage");
                Fragen.Add(new FrageViewModel(skript, f));
                LogHinzufuegen(art, $"Frage {f.Id}: {f.Text}", 1, f.Zeit);
                NeueFrage?.Invoke();
                break;
            case FrageErledigtEreignis fe:
                var offen = Fragen.FirstOrDefault(x => x.Id == fe.Id && x.Skript == art);
                if (offen != null) Fragen.Remove(offen);
                LogHinzufuegen(art, $"Frage {fe.Id} beantwortet: {fe.Wert} ({fe.Durch})", 0, fe.Zeit);
                break;
            case DiscEreignis d:
                LogHinzufuegen(art, $"Disc {d.Disc} ({d.Phase}): {d.Beschriftung}", 0, d.Zeit);
                break;
            case DiscEndeEreignis de:
                KlangSpielen(KlangAnlass(de));
                LogHinzufuegen(art, $"Disc {de.Disc} fertig: {de.Status}{(string.IsNullOrEmpty(de.Grund) ? "" : " – " + de.Grund)}",
                    de.Status is "problem" or "unlesbar" ? 3 : 0, de.Zeit);
                if (de.Status is "problem" or "unlesbar")
                    FehlerHinzufuegen(new FehlerEintrag(de.Zeit, skript.Name, de.Disc, de.Status == "unlesbar" ? "Unlesbar" : "Problem", 3,
                        string.IsNullOrEmpty(de.Grund) ? $"Disc endete mit Status {de.Status}" : de.Grund));
                break;
            case EinstellungEreignis ei when ei.Name == "nummern" && NummernModus.Alle.Contains(ei.Wert):
                // Bestätigung des Skripts: das gilt jetzt (auch beim Start)
                if (_d.Einstellungen.Nummern != ei.Wert)
                {
                    _d.Einstellungen.Nummern = ei.Wert;
                    NummernAnzeigen();
                }
                NummernHinweis = "aktiv: " + NummernText(ei.Wert);
                LogHinzufuegen(art, "Leere Discs: " + NummernText(ei.Wert), 1, ei.Zeit);
                break;
            case LaufwerkEreignis:
            case FortschrittEreignis:
            case WarteschlangeEreignis:
                break;
            case StartEreignis s:
                LogHinzufuegen(art, $"Skript gestartet (PID {s.Pid}) {string.Join(' ', s.Argumente)}", 0, s.Zeit);
                break;
            case EndeEreignis en:
                LogHinzufuegen(art, $"Skript beendet (Code {en.Code})", 0, en.Zeit);
                break;
            case ProzessEreignis p:
                LogHinzufuegen(art, $"Prozess: {p.Zustand}{(p.ExitCode is { } c ? " (Code " + c + ")" : "")} {p.Text}", p.Zustand == ProzessZustand.Abgestuerzt ? 3 : 0, p.Zeit);
                if (p.Zustand == ProzessZustand.Abgestuerzt)
                    KlangSpielen("problem");
                if (p.Zustand == ProzessZustand.Abgestuerzt)
                    FehlerHinzufuegen(new FehlerEintrag(p.Zeit, skript.Name, null, "Absturz", 3,
                        $"Skript abgestürzt{(p.ExitCode is { } code ? " (Exit-Code " + code + ")" : "")}: {p.Text}"));
                if (p.Zustand is ProzessZustand.Abgestuerzt or ProzessZustand.Beendet or ProzessZustand.Gestoppt)
                    foreach (var q in Fragen.Where(x => x.Skript == art).ToList()) Fragen.Remove(q);
                break;
        }
    }
}
