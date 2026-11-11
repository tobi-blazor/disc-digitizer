using System.Text.Json;

namespace DiscDigitalisierung.Prozesse;

/// <summary>Nachrichten C# → Python (stdin): je eine JSON-Zeile (ohne Zeilenende).</summary>
public static class NachrichtSerialisierer
{
    static readonly JsonSerializerOptions Optionen = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static string Antwort(string id, string wert) =>
        JsonSerializer.Serialize(new { typ = "antwort", id, wert }, Optionen);

    public static string Stopp() => "{\"typ\":\"stopp\"}";

    public static string Einstellung(string name, string wert) =>
        JsonSerializer.Serialize(new { typ = "einstellung", name, wert }, Optionen);
}
