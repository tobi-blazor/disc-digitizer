using System.Globalization;
using System.Windows;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Hilfen für den UI-Thread und Formatierung.</summary>
public static class Ui
{
    public static readonly CultureInfo Kultur = CultureInfo.GetCultureInfo("de-DE");

    /// <summary>Führt die Aktion auf dem UI-Thread aus (asynchron, wenn man nicht schon dort ist).</summary>
    public static void Aufrufen(Action aktion)
    {
        var d = Application.Current?.Dispatcher;
        if (d == null || d.CheckAccess()) aktion();
        else d.BeginInvoke(aktion);
    }

    public static string Dauer(double? sekunden)
    {
        if (sekunden is not { } s || s < 0) return "–";
        var t = TimeSpan.FromSeconds(Math.Round(s));
        return t.TotalHours >= 1 ? $"{(int)t.TotalHours}:{t.Minutes:00}:{t.Seconds:00}" : $"{t.Minutes}:{t.Seconds:00}";
    }

    public static string Groesse(long? bytes)
    {
        if (bytes is not { } b) return "–";
        double gb = b / 1e9;
        if (gb >= 1000) return (gb / 1000).ToString("0.0", Kultur) + " TB";
        if (gb >= 1) return gb.ToString("0.0", Kultur) + " GB";
        return (b / 1e6).ToString("0", Kultur) + " MB";
    }
}
