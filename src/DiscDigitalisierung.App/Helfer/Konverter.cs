using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.App.Helfer;

static class Farben
{
    static SolidColorBrush B(string hex)
    {
        var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        b.Freeze();
        return b;
    }
    public static readonly Brush Gruen = B("#2E9E4F"), Rot = B("#D13438"), Gelb = B("#E0A800"), Grau = B("#8A8A8A"),
        Blau = B("#2F6FDE"), Orange = B("#E8710A"), Tuerkis = B("#128C8C"), Text = B("#1F2933");
}

/// <summary>ProzessZustand → Farbpunkt.</summary>
public sealed class ZustandZuBrush : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c) => value switch
    {
        ProzessZustand.Laeuft => Farben.Gruen,
        ProzessZustand.Startet => Farben.Gelb,
        ProzessZustand.Abgestuerzt => Farben.Rot,
        _ => Farben.Grau,
    };
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>Disc-Status (Text) → Farbe.</summary>
public sealed class StatusZuBrush : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c) => (value as string)?.ToLowerInvariant() switch
    {
        "einlesen" or "eingelesen" => Farben.Blau,
        "umgewandelt" => Farben.Tuerkis,
        "einsortiert" or "geprueft" => Farben.Gruen,
        "problem" or "unlesbar" => Farben.Rot,
        "pruefen" => Farben.Orange,
        _ => Farben.Grau,
    };
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>Schwere 0 normal, 1 Hinweis, 2 Warnung, 3 Fehler → Textfarbe.</summary>
public sealed class SchwereZuBrush : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c) => value is int s
        ? s switch { >= 3 => Farben.Rot, 2 => Farben.Orange, 1 => Farben.Blau, _ => Farben.Text }
        : Farben.Text;
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>bool → Brush (Parameter "Gruen|Rot"), true = erster Wert.</summary>
public sealed class BoolZuBrush : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c)
    {
        var teile = (p as string ?? "Gruen|Rot").Split('|');
        var name = value is true ? teile[0] : teile[1];
        return name switch { "Gruen" => Farben.Gruen, "Rot" => Farben.Rot, "Orange" => Farben.Orange, _ => Farben.Grau };
    }
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>bool → Sichtbarkeit, mit Parameter "!" invertiert.</summary>
public sealed class BoolZuSichtbarkeit : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c)
    {
        var an = value is true;
        if (p as string == "!") an = !an;
        return an ? Visibility.Visible : Visibility.Collapsed;
    }
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>Text leer/null → eingeklappt.</summary>
public sealed class TextZuSichtbarkeit : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c) =>
        string.IsNullOrWhiteSpace(value as string) ? Visibility.Collapsed : Visibility.Visible;
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}

/// <summary>Anzahl 0 → sichtbar (Platzhaltertext für leere Listen).</summary>
public sealed class LeerZuSichtbarkeit : IValueConverter
{
    public object Convert(object value, Type t, object p, CultureInfo c) =>
        value is int n && n == 0 ? Visibility.Visible : Visibility.Collapsed;
    public object ConvertBack(object value, Type t, object p, CultureInfo c) => throw new NotSupportedException();
}
