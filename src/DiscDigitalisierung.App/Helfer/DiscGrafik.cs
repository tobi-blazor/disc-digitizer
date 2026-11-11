using System.Globalization;
using System.Windows;
using System.Windows.Media;
using DiscDigitalisierung.App.ViewModels;

namespace DiscDigitalisierung.App.Helfer;

/// <summary>Zeichnet eine Disc (Draufsicht) mit dem Lesestand an den echten Sektorpositionen: Datenbereich von
/// innen (24 mm, Sektor 0) nach außen (58 mm, Kapazität des Rohlings). Die Ringe haben gleiche Kapazität (gleiche
/// Fläche, die Spur ist überall gleich dicht), ihr Radius entspricht also der echten Lage. Innerhalb eines Rings
/// laufen die Abschnitte im Uhrzeigersinn ab 12 Uhr (der Winkel ist schematisch: ein Ring sind tausende Umdrehungen).</summary>
public sealed class DiscGrafik : FrameworkElement
{
    public static readonly DependencyProperty KarteProperty = DependencyProperty.Register(
        nameof(Karte), typeof(DiscKarte), typeof(DiscGrafik),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender, (d, e) =>
        {
            var g = (DiscGrafik)d;
            if (e.OldValue is DiscKarte alt) alt.Geaendert -= g.Neu;
            if (e.NewValue is DiscKarte neu) neu.Geaendert += g.Neu;
        }));

    public DiscKarte? Karte
    {
        get => (DiscKarte?)GetValue(KarteProperty);
        set => SetValue(KarteProperty, value);
    }

    /// <summary>Prozentzahl in der Mitte anzeigen.</summary>
    public bool MitText { get; set; } = true;

    public int Ringe { get; set; } = 26;
    public int AbschnitteAussen { get; set; } = 110;

    const double Aussen = 60, DatenAussen = 58, DatenInnen = 24, Nabe = 16.5, Loch = 7.5; // mm

    static SolidColorBrush B(string hex) { var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); b.Freeze(); return b; }

    public static readonly Brush Unbeschrieben = B("#E9EDF1"), NichtGelesen = B("#CDD5DE"), Geplant = B("#8C9AAB"),
        Teilweise = B("#9FD8B0"), Ok = B("#2E9E4F"), Nachlesen = B("#F2C230"), Uebersprungen = B("#E8930C"),
        Fehler = B("#D13438");
    static readonly Brush Nabenfarbe = B("#F4F6F8"), Text = B("#1F2933");
    static readonly Brush Koerper = Rund();
    static readonly Pen Rand = Stift("#9AA5B1", 1), Marke = Stift("#1F2933", 2);

    static Pen Stift(string hex, double d) { var p = new Pen(B(hex), d); p.Freeze(); return p; }

    static Brush Rund()
    {
        var b = new RadialGradientBrush(
            [new GradientStop(Color.FromRgb(0xFA, 0xFB, 0xFC), 0.25), new GradientStop(Color.FromRgb(0xE3, 0xE7, 0xEC), 1)]);
        b.Freeze();
        return b;
    }

    void Neu() => InvalidateVisual();

    // Geometrie-Zwischenspeicher je Größe: (Anteil von, Anteil bis, Geometrie)
    double _cacheGroesse = -1;
    List<(double Von, double Bis, Geometry G)> _zellen = [];

    protected override Size MeasureOverride(Size verfuegbar)
    {
        var s = Math.Min(double.IsInfinity(verfuegbar.Width) ? 200 : verfuegbar.Width,
                         double.IsInfinity(verfuegbar.Height) ? 200 : verfuegbar.Height);
        return new Size(s, s);
    }

    protected override void OnRender(DrawingContext dc)
    {
        var groesse = Math.Min(ActualWidth, ActualHeight);
        if (groesse < 20) return;
        var m = new Point(ActualWidth / 2, ActualHeight / 2);
        var r = groesse / 2 - 1;
        double Mm(double mm) => mm / Aussen * r;

        dc.DrawEllipse(Koerper, Rand, m, r, r);
        if (Math.Abs(_cacheGroesse - groesse) > 0.5) Zellen(groesse);

        var karte = Karte;
        dc.PushTransform(new TranslateTransform(m.X, m.Y));
        foreach (var (von, bis, g) in _zellen)
        {
            var z = karte?.Zustand(von, bis) ?? DiscKarte.Feld.Unbeschrieben;
            dc.DrawGeometry(z switch
            {
                DiscKarte.Feld.Ok => Ok,
                DiscKarte.Feld.Teilweise => Teilweise,
                DiscKarte.Feld.Uebersprungen => Uebersprungen,
                DiscKarte.Feld.Fehler => Fehler,
                DiscKarte.Feld.Nachlesen => Nachlesen,
                DiscKarte.Feld.Geplant => Geplant,
                DiscKarte.Feld.NichtGelesen => NichtGelesen,
                _ => Unbeschrieben,
            }, null, g);
        }
        dc.Pop();

        // Nabe und Loch
        dc.DrawEllipse(Nabenfarbe, Rand, m, Mm(Nabe), Mm(Nabe));
        dc.DrawEllipse(Brushes.White, Rand, m, Mm(Loch), Mm(Loch));

        // Leseposition als Strich quer über den Ring
        if (karte is { HatLage: true, Position: >= 0 })
        {
            var f = Math.Min(0.999999, (double)karte.Position / karte.SektorenMax);
            var (ra, rb, w) = Lage(f, r);
            var richtung = new Vector(Math.Sin(w), -Math.Cos(w));
            dc.DrawLine(Marke, m + richtung * (ra - 2), m + richtung * (rb + 2));
        }

        if (MitText && karte != null && (karte.HatLage || karte.Prozent > 0))
        {
            var prozent = karte.HatLage && karte.GeplantSektoren > 0
                ? Math.Min(100.0, 100.0 * karte.OkSektoren / karte.GeplantSektoren) : karte.Prozent;
            var t = new FormattedText($"{prozent:0} %", Ui.Kultur, FlowDirection.LeftToRight,
                new Typeface("Segoe UI Semibold"), Math.Max(9, Mm(Nabe) * 0.5), Text, VisualTreeHelper.GetDpi(this).PixelsPerDip);
            dc.DrawText(t, new Point(m.X - t.Width / 2, m.Y + Mm(Loch) + 1));
        }
    }

    /// <summary>Radius-Band und Winkel zum Anteil f (0..1) der Disc.</summary>
    (double RInnen, double RAussen, double Winkel) Lage(double f, double r)
    {
        double Mm(double mm) => mm / Aussen * r;
        double ri = Mm(DatenInnen), ra = Mm(DatenAussen);
        var ring = Math.Min(Ringe - 1, (int)(f * Ringe));
        double R(int k) => Math.Sqrt(ri * ri + (double)k / Ringe * (ra * ra - ri * ri));
        var imRing = f * Ringe - ring;
        return (R(ring), R(ring + 1), imRing * 2 * Math.PI);
    }

    void Zellen(double groesse)
    {
        _cacheGroesse = groesse;
        _zellen = [];
        var r = groesse / 2 - 1;
        double ri = DatenInnen / Aussen * r, ra = DatenAussen / Aussen * r;
        double R(int k) => Math.Sqrt(ri * ri + (double)k / Ringe * (ra * ra - ri * ri)); // gleiche Fläche = gleiche Kapazität
        for (var k = 0; k < Ringe; k++)
        {
            double r0 = R(k), r1 = R(k + 1);
            var abschnitte = Math.Max(12, (int)Math.Round(AbschnitteAussen * (r0 + r1) / 2 / ra));
            for (var j = 0; j < abschnitte; j++)
            {
                double a0 = 2 * Math.PI * j / abschnitte, a1 = 2 * Math.PI * (j + 1) / abschnitte;
                var g = new StreamGeometry();
                using (var c = g.Open())
                {
                    Point P(double rr, double w) => new(rr * Math.Sin(w), -rr * Math.Cos(w));
                    c.BeginFigure(P(r0, a0), true, true);
                    c.LineTo(P(r1, a0), false, false);
                    c.ArcTo(P(r1, a1), new Size(r1, r1), 0, false, SweepDirection.Clockwise, false, false);
                    c.LineTo(P(r0, a1), false, false);
                    c.ArcTo(P(r0, a0), new Size(r0, r0), 0, false, SweepDirection.Counterclockwise, false, false);
                }
                g.Freeze();
                double von = (k + (double)j / abschnitte) / Ringe, bis = (k + (double)(j + 1) / abschnitte) / Ringe;
                _zellen.Add((von, bis, g));
            }
        }
    }
}
