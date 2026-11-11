using System.Media;
using System.Windows.Media;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Ein Anlass, zu dem ein Klang spielt (Frage, Disc fertig …), mit Standard-Klang.</summary>
public sealed record KlangAnlass(string Id, string Name, string Standard);

/// <summary>Eine Auswahl in der Klangliste: eingebaut, Windows-Klang, aus oder eigene Datei.</summary>
public sealed record KlangOption(string Id, string Name)
{
    public override string ToString() => Name;
}

/// <summary>Klänge der App: sanfte, eingebaute Töne (beim ersten Gebrauch als WAV erzeugt), Windows-Klänge
/// oder eine eigene Datei (wav, mp3, wma …). Einstellung je Anlass in <see cref="Kern.Einstellungen.Klaenge"/>:
/// "aus", eingebauter Name, "windows:Asterisk" oder "datei:&lt;Pfad&gt;". Abspielen nur auf dem UI-Thread.</summary>
public static class Klaenge
{
    public const string Aus = "aus";
    public const string DateiPraefix = "datei:";
    public const string DateiOption = "datei";

    public static readonly KlangAnlass[] Anlaesse =
    [
        new("frage", "Frage wartet auf Antwort", "glocke"),
        new("fertig", "Disc eingelesen", "dreiklang"),
        new("problem", "Problem (Lesefehler, Absturz)", "zweiklang-ab"),
        new("leer", "Leere Disc / übersprungen", "tropfen"),
        new("umgewandelt", "Disc umgewandelt und einsortiert", Aus),
    ];

    public static readonly KlangOption[] Optionen =
    [
        new(Aus, "Aus"),
        new("glocke", "Glocke (sanft)"),
        new("dreiklang", "Dreiklang (sanft)"),
        new("harfe", "Harfe"),
        new("zweiklang-auf", "Zweiklang aufwärts"),
        new("zweiklang-ab", "Zweiklang abwärts"),
        new("tropfen", "Tropfen"),
        new("marimba", "Marimba"),
        new("piepen", "Piepen (wie früher)"),
        new("windows:Asterisk", "Windows: Information"),
        new("windows:Exclamation", "Windows: Hinweis"),
        new("windows:Hand", "Windows: Fehler"),
        new("windows:Question", "Windows: Frage"),
        new(DateiOption, "Eigene Datei …"),
    ];

    static readonly List<MediaPlayer> Laufend = [];

    /// <summary>Wert der Einstellung für einen Anlass (mit Standard).</summary>
    public static string Wahl(IReadOnlyDictionary<string, string> klaenge, string anlass) =>
        klaenge.TryGetValue(anlass, out var w) && !string.IsNullOrWhiteSpace(w)
            ? w : Anlaesse.FirstOrDefault(a => a.Id == anlass)?.Standard ?? Aus;

    /// <summary>Spielt den Klang zum Anlass. Fehler (Datei fehlt …) werden zurückgegeben, nie geworfen.</summary>
    public static string? Spielen(IReadOnlyDictionary<string, string> klaenge, string anlass, int lautstaerke) =>
        SpielenWert(Wahl(klaenge, anlass), lautstaerke);

    /// <summary>Spielt einen Einstellungswert direkt (auch zum Probehören im Einstellungsdialog).</summary>
    public static string? SpielenWert(string wert, int lautstaerke)
    {
        try
        {
            if (wert == Aus || string.IsNullOrWhiteSpace(wert)) return null;
            if (wert.StartsWith("windows:", StringComparison.Ordinal))
            {
                SystemKlang(wert["windows:".Length..]).Play(); // Lautstärke regelt Windows
                return null;
            }
            string datei;
            if (wert.StartsWith(DateiPraefix, StringComparison.Ordinal))
            {
                datei = wert[DateiPraefix.Length..];
                if (!File.Exists(datei)) return $"Klangdatei nicht gefunden: {datei}";
            }
            else
            {
                datei = Erzeugen(wert) ?? throw new ArgumentException($"unbekannter Klang „{wert}“");
            }
            var p = new MediaPlayer { Volume = Math.Clamp(lautstaerke, 0, 100) / 100.0 };
            p.MediaEnded += (_, _) => Fertig(p);
            p.MediaFailed += (_, _) => Fertig(p);
            Laufend.Add(p); // Referenz halten, sonst bricht der Klang ab
            p.Open(new Uri(datei));
            p.Play();
            return null;
        }
        catch (Exception ex)
        {
            return "Klang nicht abspielbar: " + ex.Message;
        }
    }

    static void Fertig(MediaPlayer p)
    {
        p.Close();
        Laufend.Remove(p);
    }

    static SystemSound SystemKlang(string name) => name switch
    {
        "Asterisk" => SystemSounds.Asterisk,
        "Exclamation" => SystemSounds.Exclamation,
        "Hand" => SystemSounds.Hand,
        "Question" => SystemSounds.Question,
        _ => SystemSounds.Beep,
    };

    // ---------------------------------------------------------------- eingebaute Klänge

    const int Rate = 44100;
    const string Version = "1"; // hochzählen, wenn sich die Klänge ändern

    /// <summary>Note: Beginn (s), Frequenz (Hz), Dauer (s), Abklingzeit (s), Teiltöne (Faktor, Stärke).</summary>
    sealed record Note(double Start, double Hz, double Dauer, double Abklingen, (double Faktor, double Staerke)[] Teiltoene,
        double Gleiten = 1.0);

    static readonly (double, double)[] Weich = [(1, 1.0), (2, 0.18), (3, 0.05)];
    static readonly (double, double)[] Glockig = [(1, 1.0), (2.76, 0.32), (5.4, 0.12), (8.93, 0.04)];
    static readonly (double, double)[] Holz = [(1, 1.0), (3.9, 0.35), (9.2, 0.08)];

    static Note[]? Noten(string name) => name switch
    {
        "glocke" => [new(0, 880, 1.6, 0.55, Glockig)],
        "dreiklang" => [new(0, 523.25, 1.1, 0.45, Weich), new(0.14, 659.25, 1.0, 0.45, Weich), new(0.28, 783.99, 1.2, 0.55, Weich)],
        "harfe" => [new(0, 523.25, 1.0, 0.4, Weich), new(0.08, 659.25, 1.0, 0.4, Weich), new(0.16, 783.99, 1.0, 0.4, Weich),
            new(0.24, 1046.5, 1.3, 0.5, Weich)],
        "zweiklang-auf" => [new(0, 587.33, 0.9, 0.35, Weich), new(0.2, 880, 1.2, 0.45, Weich)],
        "zweiklang-ab" => [new(0, 659.25, 0.9, 0.35, Weich), new(0.24, 440, 1.3, 0.5, Weich)],
        "tropfen" => [new(0, 1500, 0.35, 0.09, [(1, 1.0)], Gleiten: 0.45)],
        "marimba" => [new(0, 523.25, 0.7, 0.22, Holz), new(0.16, 783.99, 0.8, 0.25, Holz)],
        "piepen" => [new(0, 660, 0.14, 99, [(1, 1.0)]), new(0.14, 880, 0.14, 99, [(1, 1.0)]), new(0.28, 1175, 0.14, 99, [(1, 1.0)])],
        _ => null,
    };

    /// <summary>Erzeugt den eingebauten Klang als WAV unter %LOCALAPPDATA%\DiscDigitalisierung\klaenge (einmalig).</summary>
    static string? Erzeugen(string name)
    {
        var noten = Noten(name);
        if (noten == null) return null;
        var ordner = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DiscDigitalisierung", "klaenge");
        var datei = Path.Combine(ordner, $"{name}-v{Version}.wav");
        if (File.Exists(datei)) return datei;
        Directory.CreateDirectory(ordner);
        var ende = noten.Max(n => n.Start + n.Dauer) + 0.05;
        var puffer = new double[(int)(ende * Rate)];
        foreach (var n in noten)
        {
            var start = (int)(n.Start * Rate);
            var laenge = (int)(n.Dauer * Rate);
            double phase = 0;
            for (var i = 0; i < laenge && start + i < puffer.Length; i++)
            {
                var t = i / (double)Rate;
                var hz = n.Gleiten == 1.0 ? n.Hz : n.Hz * Math.Pow(n.Gleiten, Math.Min(t / 0.12, 1.0));
                phase += 2 * Math.PI * hz / Rate;
                var anschlag = Math.Min(1.0, t / 0.008);                     // 8 ms weicher Einsatz
                var ausklang = Math.Min(1.0, (n.Dauer - t) / 0.03);          // 30 ms Ausblenden
                var huelle = anschlag * ausklang * Math.Exp(-t / n.Abklingen);
                double s = 0;
                foreach (var (faktor, staerke) in n.Teiltoene)
                    s += staerke * Math.Sin(phase * faktor) * Math.Exp(-t * (faktor - 1) * 1.5); // hohe Teiltöne klingen schneller ab
                puffer[start + i] += 0.28 * huelle * s;
            }
        }
        var max = Math.Max(1e-9, puffer.Max(Math.Abs));
        var skala = max > 0.9 ? 0.9 / max : 1.0;
        var tmp = datei + ".tmp";
        using (var f = new BinaryWriter(File.Create(tmp)))
        {
            var daten = puffer.Length * 2;
            f.Write("RIFF"u8); f.Write(36 + daten); f.Write("WAVE"u8);
            f.Write("fmt "u8); f.Write(16); f.Write((short)1); f.Write((short)1); f.Write(Rate); f.Write(Rate * 2);
            f.Write((short)2); f.Write((short)16);
            f.Write("data"u8); f.Write(daten);
            foreach (var x in puffer) f.Write((short)Math.Round(Math.Clamp(x * skala, -1, 1) * 32000));
        }
        File.Move(tmp, datei, overwrite: true);
        return datei;
    }
}
