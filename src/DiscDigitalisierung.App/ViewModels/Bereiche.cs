using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.ViewModels;

/// <summary>Anzeige-Zustand des Einlesen-Bereichs.</summary>
public sealed partial class EinlesenBereich : ObservableObject
{
    [ObservableProperty] string _laufwerk = "unbekannt";
    [ObservableProperty] string _discNummer = "–";
    [ObservableProperty] string _beschriftung = "";
    [ObservableProperty] string _typLabel = "";
    [ObservableProperty] double _prozent;
    [ObservableProperty] string _titelText = "";
    [ObservableProperty] string _groesseText = "";
    [ObservableProperty] string _rateText = "";
    [ObservableProperty] string _restText = "";
    [ObservableProperty] string _schritt = "";
    [ObservableProperty] long _fehlerSektoren;
    [ObservableProperty] string _letztesErgebnis = "noch keines";
    [ObservableProperty] string _letzterStatus = "";

    public bool HatFehler => FehlerSektoren > 0;

    // ---- Disc-Grafik und Metadaten der Disc, die gerade eingelesen wird ----
    public DiscKarte Karte { get; } = new();
    public ObservableCollection<MetaZeile> Metadaten { get; } = [];
    public ICollectionView MetadatenAnsicht { get; }
    /// <summary>Lesefehler kurz: „keine“ oder z.B. „1600 werden nachgelesen · 160 unlesbar Sektoren“.</summary>
    [ObservableProperty] string _fehlerText = "keine";

    /// <summary>Was gerade passiert: Lesen, Nachlesen der Fehlerstellen, fertig.</summary>
    [ObservableProperty] string _phaseText = "";

    // ---- Lesegeschwindigkeit ----
    public Geschwindigkeit Tempo { get; } = new();
    [ObservableProperty] double _tempoMbS;
    [ObservableProperty] double _tempoSkala = 22;
    [ObservableProperty] string _tempoText = "–";

    /// <summary>Empfehlung nach dem Einlesen (Reinigen?) und ihre Stufe: keine, optional, empfohlen, anderes_laufwerk.</summary>
    [ObservableProperty] [NotifyPropertyChangedFor(nameof(HatEmpfehlung))] string _empfehlung = "";
    [ObservableProperty] string _empfehlungStufe = "";
    public bool HatEmpfehlung => Empfehlung != "";

    public EinlesenBereich()
    {
        MetadatenAnsicht = CollectionViewSource.GetDefaultView(Metadaten);
        MetadatenAnsicht.GroupDescriptions.Add(new PropertyGroupDescription(nameof(MetaZeile.Gruppe)));
        Karte.Geaendert += FehlerAktualisieren;
        Tempo.Geaendert += () =>
        {
            TempoMbS = Tempo.MbS;
            TempoSkala = Tempo.Skala;
            TempoText = Tempo.MbS > 0.05
                ? $"{Tempo.MbS.ToString("0.0", Ui.Kultur)} MB/s · {Tempo.Faktor.ToString("0.0", Ui.Kultur)}x"
                : "steht";
        };
    }

    void FehlerAktualisieren()
    {
        if (!Karte.HatLage)
        {
            FehlerText = FehlerSektoren > 0 ? $"{FehlerSektoren} Sektoren" : "keine";
            return;
        }
        var teile = new List<string>();
        if (Karte.NachlesenSektoren > 0) teile.Add($"{Karte.NachlesenSektoren} nachzulesen");
        if (Karte.FehlerSektoren > 0) teile.Add($"{Karte.FehlerSektoren} unlesbar");
        if (Karte.UebersprungenSektoren > 0) teile.Add($"{Karte.UebersprungenSektoren} gelassen");
        FehlerText = teile.Count > 0 ? string.Join(" · ", teile) + " (Sektoren)" : "keine";
        if (Karte.NachlesenSektoren > 0 && Prozent >= 99.9) PhaseText = "Nachlesen der Fehlerstellen";
    }

    partial void OnFehlerSektorenChanged(long value)
    {
        OnPropertyChanged(nameof(HatFehler));
        FehlerAktualisieren();
    }

    public void Verarbeiten(Ereignis e)
    {
        switch (e)
        {
            case MetadatenEreignis me:
                Metadaten.Clear();
                foreach (var f in me.Felder) Metadaten.Add(new MetaZeile(f.Gruppe, MetaNamen.Bezeichnung(f.Name), f.Wert));
                break;
            case LesekarteEreignis lk:
                Karte.Hinzufuegen(lk);
                break;
            case EmpfehlungEreignis em when em.Art == "reinigen":
                Empfehlung = em.Text;
                EmpfehlungStufe = em.Stufe;
                break;
            case LaufwerkEreignis l:
                Laufwerk = l.Zustand switch
                {
                    "leer" => "Laufwerk leer – bitte Disc einlegen",
                    "disc" => $"Disc eingelegt{(string.IsNullOrEmpty(l.Medium) ? "" : " (" + l.Medium + ")")}",
                    "haengt" => "Laufwerk hängt",
                    "ohne_inhalt" => "Disc ohne Inhalt",
                    "verarbeitet" => "Disc verarbeitet – bitte entnehmen",
                    _ => l.Zustand,
                };
                break;
            case DiscEreignis d:
                DiscNummer = d.Disc;
                Beschriftung = d.Beschriftung ?? "";
                TypLabel = string.Join(" · ", new[] { d.Typ, d.Label }.Where(s => !string.IsNullOrEmpty(s)));
                Prozent = 0; TitelText = ""; GroesseText = ""; RateText = ""; RestText = ""; Schritt = ""; FehlerSektoren = 0;
                Karte.Zuruecksetzen();
                Tempo.Zuruecksetzen(d.Typ == "audio");
                Metadaten.Clear();
                Empfehlung = ""; EmpfehlungStufe = "";
                PhaseText = "Lesen";
                break;
            case FortschrittEreignis f:
                if (f.Disc != null) DiscNummer = f.Disc;
                Karte.ProzentSetzen(f.Prozent);
                if (f.Prozent is { } p) Prozent = p;
                TitelText = f.TitelNr is { } n && f.TitelAnzahl is { } a ? $"{n} von {a}" + (f.Titel is { } tn ? $" ({tn})" : "") : f.Titel ?? "";
                GroesseText = f.Byte != null ? $"{Ui.Groesse(f.Byte)} von {Ui.Groesse(f.ByteGesamt)}" : "";
                RateText = f.RateMbS is { } r ? r.ToString("0.0", Ui.Kultur) + " MB/s" : "";
                RestText = Ui.Dauer(f.RestSekunden);
                Schritt = f.Schritt ?? "";
                if (f.FehlerSektoren is { } fs) FehlerSektoren = fs;
                Tempo.Messen(f.Byte, f.Prozent / 100.0);
                if (f.Schritt == "fertig") PhaseText = "fertig";
                break;
            case DiscEndeEreignis de:
                PhaseText = "fertig";
                Tempo.Steht();
                LetzterStatus = de.Status;
                LetztesErgebnis = $"{de.Disc}: {de.Status}{(string.IsNullOrEmpty(de.Grund) ? "" : " – " + de.Grund)}"
                    + (de.DauerSekunden is { } s ? $" (Dauer {Ui.Dauer(s)})" : "");
                break;
            case ProzessEreignis { Zustand: ProzessZustand.Gestoppt or ProzessZustand.Beendet or ProzessZustand.Abgestuerzt }:
                Laufwerk = "Skript nicht aktiv";
                break;
        }
    }
}

/// <summary>Anzeige-Zustand des Umwandeln-Bereichs.</summary>
public sealed partial class UmwandelnBereich : ObservableObject
{
    [ObservableProperty] string _aktiveDisc = "–";
    [ObservableProperty] string _beschriftung = "";
    [ObservableProperty] string _schritt = "";
    [ObservableProperty] double _prozent;
    [ObservableProperty] string _restText = "";
    [ObservableProperty] string _pauseText = "";
    [ObservableProperty] string _letztesErgebnis = "noch keines";
    [ObservableProperty] string _letzterStatus = "";

    public ObservableCollection<string> Warteschlange { get; } = [];
    public string WarteschlangeTitel => $"Warteschlange ({Warteschlange.Count})";

    public void Verarbeiten(Ereignis e)
    {
        switch (e)
        {
            case DiscEreignis d:
                AktiveDisc = d.Disc;
                Beschriftung = d.Beschriftung ?? "";
                Prozent = 0; Schritt = ""; RestText = "";
                break;
            case FortschrittEreignis f:
                if (f.Disc != null) AktiveDisc = f.Disc;
                if (f.Prozent is { } p) Prozent = p;
                Schritt = f.Schritt switch
                {
                    "handbrake" => "HandBrake" + (f.TitelNr is { } n && f.TitelAnzahl is { } a ? $" (Titel {n}/{a})" : ""),
                    "comskip" => "Comskip (Werbung markieren)",
                    "pruefen" => "Prüfung",
                    null => "",
                    var sch => sch,
                };
                RestText = "Rest " + Ui.Dauer(f.RestSekunden);
                break;
            case WarteschlangeEreignis w:
                Warteschlange.Clear();
                foreach (var x in w.Wartend) Warteschlange.Add(x);
                OnPropertyChanged(nameof(WarteschlangeTitel));
                if (w.Aktiv == null) { AktiveDisc = "–"; Beschriftung = ""; Schritt = ""; Prozent = 0; RestText = ""; }
                PauseText = w.PausiertBis is { } bis ? $"Pausiert bis {bis.LocalDateTime:HH:mm} (Zeitfenster)" : "";
                break;
            case DiscEndeEreignis de:
                LetzterStatus = de.Status;
                LetztesErgebnis = $"{de.Disc}: {de.Status}{(string.IsNullOrEmpty(de.Grund) ? "" : " – " + de.Grund)}"
                    + (de.DauerSekunden is { } s ? $" (Dauer {Ui.Dauer(s)})" : "");
                break;
            case ProzessEreignis { Zustand: ProzessZustand.Gestoppt or ProzessZustand.Beendet or ProzessZustand.Abgestuerzt }:
                Schritt = "Skript nicht aktiv";
                break;
        }
    }
}

/// <summary>Eine Zeile der Metadaten-Anzeige.</summary>
public sealed record MetaZeile(string Gruppe, string Name, string Wert);

/// <summary>Lesbare Namen für die Feldnamen aus Python (sonst: Unterstriche zu Leerzeichen, erster Buchstabe groß).</summary>
public static class MetaNamen
{
    static readonly Dictionary<string, string> Namen = new()
    {
        ["disc"] = "Disc-Nummer", ["beschriftung"] = "Beschriftung", ["label"] = "Volume-Label", ["typ"] = "Typ",
        ["status"] = "Status", ["eingelesen"] = "Eingelesen um", ["lesefehler_sektoren"] = "Lesefehler (Sektoren)",
        ["quelle"] = "Gelesen mit", ["einlesen_s"] = "Einlesedauer", ["seriennummer"] = "Seriennummer (dezimal)",
        ["rekorder"] = "Rekorder", ["problem_grund"] = "Problemgrund", ["geaendert"] = "Geändert",
        ["laufwerk"] = "Laufwerk", ["medium"] = "Medium", ["dateisystem"] = "Dateisystem", ["belegt_byte"] = "Belegt",
        ["kapazitaet_byte"] = "Kapazität", ["volume_seriennummer"] = "Volume-Seriennummer",
        ["medienwechsel_zaehler"] = "Medienwechsel-Zähler",
        ["nr"] = "Nr.", ["dvd_titel"] = "DVD-Titel", ["vts"] = "Titelsatz (VTS)", ["pgc"] = "Programmkette (PGC)",
        ["dauer_s"] = "Dauer", ["zellen"] = "Zellen", ["groesse_byte"] = "Größe (geplant)", ["datei"] = "Datei",
        ["byte"] = "Kopiert", ["luecken_byte"] = "Lücken", ["begleitdateien"] = "Begleitdateien",
        ["aufnahme.name"] = "Sendung", ["aufnahme.sender"] = "Sender", ["aufnahme.sendezeit"] = "Sendezeit",
        ["aufnahme.dauer_s"] = "Dauer laut Rekorder",
        ["musicbrainz_discid"] = "MusicBrainz-Disc-ID", ["musicbrainz_release"] = "MusicBrainz-Release",
        ["accuraterip_id"] = "AccurateRip-ID", ["accuraterip_datensaetze"] = "AccurateRip-Datensätze",
        ["spuren"] = "Spuren", ["offset"] = "Laufwerks-Offset", ["interpret"] = "Interpret", ["album"] = "Album",
        ["jahr"] = "Jahr", ["titel"] = "Titel", ["disc_von"] = "Disc", ["cover"] = "Cover", ["benennung"] = "Benennung",
        ["recording_id"] = "MusicBrainz-Aufnahme", ["track_id"] = "MusicBrainz-Track",
    };

    public static string Bezeichnung(string name)
    {
        if (Namen.TryGetValue(name, out var n)) return n;
        var t = name.Replace('_', ' ').Replace('.', ' ');
        return t.Length > 0 ? char.ToUpper(t[0], Ui.Kultur) + t[1..] : t;
    }
}
