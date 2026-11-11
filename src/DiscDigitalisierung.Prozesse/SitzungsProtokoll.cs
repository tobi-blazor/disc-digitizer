using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Prozesse;

/// <summary>Schreibt alle Ereignisse als JSON-Zeilen (JSONL) in eine Sitzungsdatei:
/// {"ereignis":"Typname","daten":{...}}. Thread-sicher, Flush je Zeile.</summary>
public sealed class SitzungsProtokoll : IDisposable
{
    static readonly JsonSerializerOptions Optionen = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter() },
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    readonly object _sperre = new();
    readonly StreamWriter _datei;
    readonly List<(ISkriptProzess Prozess, EventHandler<Ereignis> Handler)> _abos = [];
    bool _frei;

    public string Pfad { get; }

    public static string StandardOrdner => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DiscDigitalisierung", "logs");

    public SitzungsProtokoll(string? pfad = null)
    {
        Pfad = pfad ?? Path.Combine(StandardOrdner, $"{DateTime.Now:yyyy-MM-dd_HHmmss}.jsonl");
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(Pfad))!);
        _datei = new StreamWriter(new FileStream(Pfad, FileMode.Append, FileAccess.Write, FileShare.ReadWrite),
            new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
    }

    public void Anhaengen(ISkriptProzess prozess)
    {
        EventHandler<Ereignis> h = (_, e) => Schreiben(e);
        lock (_sperre) _abos.Add((prozess, h));
        prozess.EreignisEmpfangen += h;
    }

    public void Schreiben(Ereignis e)
    {
        var zeile = "{\"ereignis\":" + JsonSerializer.Serialize(e.GetType().Name)
            + ",\"daten\":" + JsonSerializer.Serialize(e, e.GetType(), Optionen) + "}";
        lock (_sperre)
        {
            if (_frei) return;
            try { _datei.WriteLine(zeile); }
            catch (IOException) { /* Protokoll darf das Programm nie stören */ }
        }
    }

    public void Dispose()
    {
        (ISkriptProzess Prozess, EventHandler<Ereignis> Handler)[] abos;
        lock (_sperre)
        {
            if (_frei) return;
            _frei = true;
            abos = [.. _abos];
            _abos.Clear();
        }
        foreach (var (p, h) in abos) p.EreignisEmpfangen -= h;
        _datei.Dispose();
    }
}
