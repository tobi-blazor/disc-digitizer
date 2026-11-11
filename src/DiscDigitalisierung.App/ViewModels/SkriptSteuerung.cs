using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using DiscDigitalisierung.App.Helfer;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.ViewModels;

/// <summary>Steuert ein Skript (Einlesen oder Umwandeln): Zustand, Start/Stopp/Neustart.
/// Ereignisse werden hier auf den UI-Thread gebracht und von dort weitergereicht.</summary>
public sealed partial class SkriptSteuerung : ObservableObject
{
    readonly ISkriptProzessFabrik _fabrik;
    readonly Func<IReadOnlyList<string>> _argumente;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Laeuft), nameof(ZustandText), nameof(KannStarten))]
    [NotifyCanExecuteChangedFor(nameof(StartCommand), nameof(StoppCommand), nameof(NeustartCommand))]
    ProzessZustand _zustand = ProzessZustand.Gestoppt;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(KannStarten))]
    [NotifyCanExecuteChangedFor(nameof(StartCommand), nameof(NeustartCommand))]
    bool _laeuftAusserhalb;

    string? _zusatz;

    // ---- automatischer Neustart nach Absturz ----
    readonly NeustartRegel _neustart = new();
    CancellationTokenSource? _neustartAbbruch;
    string? _neustartText;

    public SkriptSteuerung(SkriptArt art, ISkriptProzessFabrik fabrik, Func<IReadOnlyList<string>> argumente)
    {
        Art = art;
        _fabrik = fabrik;
        _argumente = argumente;
    }

    public SkriptArt Art { get; }
    public string Name => Art == SkriptArt.Einlesen ? "Einlesen" : "Umwandeln";
    public ISkriptProzess? Prozess { get; private set; }
    public bool Laeuft => Zustand is ProzessZustand.Laeuft or ProzessZustand.Startet;
    public bool KannStarten => !Laeuft && !LaeuftAusserhalb;

    public string ZustandText => Zustand switch
    {
        ProzessZustand.Startet => "startet …",
        ProzessZustand.Laeuft => Prozess?.Pid is { } pid ? $"läuft (PID {pid})" : "läuft",
        ProzessZustand.Beendet => "beendet" + _zusatz,
        ProzessZustand.Abgestuerzt => "abgestürzt" + _zusatz + _neustartText,
        _ => "gestoppt",
    };

    /// <summary>Alle Ereignisse dieses Skripts, auf dem UI-Thread.</summary>
    public event Action<SkriptSteuerung, Ereignis>? EreignisEingetroffen;

    /// <summary>Start durch den Nutzer: zählt die Abstürze neu.</summary>
    [RelayCommand(CanExecute = nameof(KannStarten))]
    public Task StartAsync()
    {
        _neustart.Zuruecksetzen();
        return Starten();
    }

    async Task Starten()
    {
        NeustartAbbrechen();
        if (Laeuft) return;
        if (Prozess != null) await Prozess.DisposeAsync();
        var prozess = _fabrik.Erzeugen(Art);
        Prozess = prozess;
        prozess.EreignisEmpfangen += (_, e) => Ui.Aufrufen(() => { if (ReferenceEquals(prozess, Prozess)) Verarbeiten(e); });
        _zusatz = null;
        Zustand = ProzessZustand.Startet;
        try
        {
            await prozess.StartenAsync(_argumente());
        }
        catch (Exception ex)
        {
            Verarbeiten(new ProzessEreignis(ProzessZustand.Abgestuerzt, null, "Start fehlgeschlagen: " + ex.Message) { Skript = Art });
        }
    }

    [RelayCommand(CanExecute = nameof(Laeuft))]
    public async Task StoppAsync()
    {
        NeustartAbbrechen();
        if (Prozess == null || !Laeuft) return;
        try { await Prozess.StoppenAsync(TimeSpan.FromSeconds(10)); }
        catch (Exception ex)
        {
            Verarbeiten(new ProzessEreignis(ProzessZustand.Abgestuerzt, null, "Stoppen fehlgeschlagen: " + ex.Message) { Skript = Art });
            NeustartAbbrechen(); // Nutzer wollte stoppen: nicht von selbst wieder starten
            OnPropertyChanged(nameof(ZustandText));
            return;
        }
        if (Laeuft) Zustand = ProzessZustand.Gestoppt;
    }

    [RelayCommand(CanExecute = nameof(KannStarten))]
    public async Task NeustartAsync()
    {
        await StoppAsync();
        await StartAsync();
    }

    /// <summary>Wird von Start/Stopp/Neustart gesperrt, solange es läuft; Neustart ist auch im Lauf erlaubt.</summary>
    public async Task AntwortenAsync(string frageId, string wert)
    {
        if (Prozess == null) return;
        try { await Prozess.AntwortenAsync(frageId, wert); }
        catch (Exception ex)
        {
            Verarbeiten(new MeldungEreignis(MeldungsStufe.Fehler, "Antwort konnte nicht gesendet werden: " + ex.Message, null) { Skript = Art });
        }
    }

    /// <summary>Schaltet eine Einstellung des laufenden Skripts um. Fehler landen als Meldung im Protokoll.</summary>
    public async Task EinstellenAsync(string name, string wert)
    {
        if (Prozess == null || !Laeuft) return;
        try { await Prozess.EinstellenAsync(name, wert); }
        catch (Exception ex)
        {
            Verarbeiten(new MeldungEreignis(MeldungsStufe.Fehler, $"Einstellung {name} konnte nicht gesendet werden: " + ex.Message, null) { Skript = Art });
        }
    }

    void Verarbeiten(Ereignis e)
    {
        if (e is ProzessEreignis p)
        {
            _zusatz = p.ExitCode is { } c ? $" (Code {c})" : null;
            Zustand = p.Zustand;
            OnPropertyChanged(nameof(ZustandText));
        }
        else if (e is StartEreignis) OnPropertyChanged(nameof(ZustandText));
        EreignisEingetroffen?.Invoke(this, e);
        if (e is ProzessEreignis { Zustand: ProzessZustand.Abgestuerzt } a) NeustartPlanen(a.Zeit);
    }

    /// <summary>Nach einem Absturz: nach kurzer Wartezeit selbst neu starten (Skripte setzen dort fort,
    /// wo sie waren). Nach zu vielen Abstürzen in Folge nicht mehr, dann muss der Nutzer nachsehen.</summary>
    void NeustartPlanen(DateTimeOffset zeit)
    {
        NeustartAbbrechen();
        if (LaeuftAusserhalb) return;
        var warten = _neustart.Absturz(zeit);
        if (warten is not { } w)
        {
            _neustartText = $" – kein automatischer Neustart ({_neustart.Anzahl}× in {NeustartRegel.Fenster.TotalMinutes:0} min)";
            OnPropertyChanged(nameof(ZustandText));
            EreignisEingetroffen?.Invoke(this, new MeldungEreignis(MeldungsStufe.Fehler,
                $"{Name} ist {_neustart.Anzahl}× in {NeustartRegel.Fenster.TotalMinutes:0} min abgestürzt, kein automatischer Neustart mehr. "
                + "Fehler ansehen und von Hand starten.", null) { Skript = Art });
            return;
        }
        var um = DateTimeOffset.Now + w;
        _neustartText = $" – Neustart um {um:HH:mm:ss}";
        OnPropertyChanged(nameof(ZustandText));
        EreignisEingetroffen?.Invoke(this, new MeldungEreignis(MeldungsStufe.Hinweis,
            $"Automatischer Neustart in {w.TotalSeconds:0} s ({_neustart.Anzahl}. Absturz in {NeustartRegel.Fenster.TotalMinutes:0} min)", null) { Skript = Art });
        var abbruch = _neustartAbbruch = new CancellationTokenSource();
        _ = NeustartNach(w, abbruch.Token);
    }

    async Task NeustartNach(TimeSpan warten, CancellationToken abbruch)
    {
        try { await Task.Delay(warten, abbruch); }
        catch (OperationCanceledException) { return; }
        // Task.Delay kehrt ohne ConfigureAwait auf den UI-Thread zurück
        if (abbruch.IsCancellationRequested || Laeuft || LaeuftAusserhalb) return;
        await Starten();
    }

    void NeustartAbbrechen()
    {
        _neustartAbbruch?.Cancel();
        _neustartAbbruch = null;
        _neustartText = null;
    }
}
