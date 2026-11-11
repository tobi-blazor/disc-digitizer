using System.Windows;
using System.Windows.Media;
using DiscDigitalisierung.App.ViewModels;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Transferraten-Kurve: Lesegeschwindigkeit (MB/s) über der Position auf der Disc (0–100 %).
/// Einbrüche zeigen schwer lesbare Stellen. Hilfslinien bei 4x, 8x, 12x, 16x (DVD) bzw. 16x … 48x (CD).</summary>
public sealed class GeschwindigkeitsGrafik : FrameworkElement
{
    public static readonly DependencyProperty QuelleProperty = DependencyProperty.Register(
        nameof(Quelle), typeof(Geschwindigkeit), typeof(GeschwindigkeitsGrafik),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender, (d, e) =>
        {
            var g = (GeschwindigkeitsGrafik)d;
            if (e.OldValue is Geschwindigkeit alt) alt.Geaendert -= g.InvalidateVisual;
            if (e.NewValue is Geschwindigkeit neu) neu.Geaendert += g.InvalidateVisual;
        }));

    public Geschwindigkeit? Quelle
    {
        get => (Geschwindigkeit?)GetValue(QuelleProperty);
        set => SetValue(QuelleProperty, value);
    }

    static SolidColorBrush B(string hex, byte alpha = 255)
    {
        var c = (Color)ColorConverter.ConvertFromString(hex);
        var b = new SolidColorBrush(Color.FromArgb(alpha, c.R, c.G, c.B));
        b.Freeze();
        return b;
    }

    static readonly Brush Hintergrund = B("#F6F8FA"), Flaeche = B("#2E9E4F", 60), Schrift = B("#5F6B7A");
    static readonly Pen Linie = Stift("#2E9E4F", 1.5), Hilfe = Stift("#DDE2E8", 1), Rand = Stift("#D5DAE1", 1);

    static Pen Stift(string hex, double d) { var p = new Pen(B(hex), d); p.Freeze(); return p; }

    protected override void OnRender(DrawingContext dc)
    {
        var w = ActualWidth;
        var h = ActualHeight;
        if (w < 20 || h < 20) return;
        var r = new Rect(0, 0, w, h);
        dc.DrawRoundedRectangle(Hintergrund, Rand, r, 3, 3);
        var q = Quelle;
        if (q == null) return;

        var skala = q.Skala;
        var einfach = q.IstCd ? 0.1764 : 1.385;
        var dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        foreach (var x in q.IstCd ? new[] { 16, 32, 48 } : new[] { 4, 8, 12, 16 })
        {
            var y = h - x * einfach / skala * (h - 4) - 2;
            if (y < 2) continue;
            dc.DrawLine(Hilfe, new Point(1, y), new Point(w - 1, y));
            var t = new FormattedText($"{x}x", Ui.Kultur, FlowDirection.LeftToRight, new Typeface("Segoe UI"), 9, Schrift, dpi);
            dc.DrawText(t, new Point(w - t.Width - 3, y - t.Height));
        }

        var punkte = q.Punkte;
        if (punkte.Count < 2) return;
        Point P(double pos, double mbs) => new(1 + pos * (w - 2), h - 2 - Math.Min(mbs, skala) / skala * (h - 4));
        var g = new StreamGeometry();
        using (var c = g.Open())
        {
            c.BeginFigure(new Point(P(punkte[0].Position, 0).X, h - 2), true, true);
            foreach (var (pos, mbs) in punkte) c.LineTo(P(pos, mbs), true, true);
            c.LineTo(new Point(P(punkte[^1].Position, 0).X, h - 2), true, true);
        }
        g.Freeze();
        dc.DrawGeometry(Flaeche, null, g);
        var linie = new StreamGeometry();
        using (var c = linie.Open())
        {
            c.BeginFigure(P(punkte[0].Position, punkte[0].MbS), false, false);
            foreach (var (pos, mbs) in punkte.Skip(1)) c.LineTo(P(pos, mbs), true, true);
        }
        linie.Freeze();
        dc.DrawGeometry(null, Linie, linie);
    }
}
