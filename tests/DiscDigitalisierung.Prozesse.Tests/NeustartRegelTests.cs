using DiscDigitalisierung.Kern;
using Xunit;

namespace DiscDigitalisierung.Prozesse.Tests;

public class NeustartRegelTests
{
    static readonly DateTimeOffset T0 = new(2026, 11, 10, 6, 48, 17, TimeSpan.FromHours(1));

    [Fact]
    public void Wartezeit_steigt_und_endet_nach_drei_Abstuerzen()
    {
        var r = new NeustartRegel();
        Assert.Equal(TimeSpan.FromSeconds(10), r.Absturz(T0));
        Assert.Equal(TimeSpan.FromSeconds(30), r.Absturz(T0.AddMinutes(1)));
        Assert.Equal(TimeSpan.FromSeconds(60), r.Absturz(T0.AddMinutes(2)));
        Assert.Null(r.Absturz(T0.AddMinutes(3)));
    }

    [Fact]
    public void Alte_Abstuerze_ausserhalb_des_Fensters_zaehlen_nicht()
    {
        var r = new NeustartRegel();
        r.Absturz(T0);
        r.Absturz(T0.AddMinutes(1));
        r.Absturz(T0.AddMinutes(2));
        // eine Nacht später: wieder kurze Wartezeit
        Assert.Equal(TimeSpan.FromSeconds(10), r.Absturz(T0.AddHours(8)));
        Assert.Equal(1, r.Anzahl);
    }

    [Fact]
    public void Start_durch_Nutzer_setzt_zurueck()
    {
        var r = new NeustartRegel();
        for (var i = 0; i < 4; i++) r.Absturz(T0.AddSeconds(i));
        r.Zuruecksetzen();
        Assert.Equal(TimeSpan.FromSeconds(10), r.Absturz(T0.AddMinutes(1)));
    }
}
