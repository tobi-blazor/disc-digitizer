using DiscDigitalisierung.Kern;

namespace DiscDigitalisierung.Prozesse;

public sealed class SkriptProzessFabrik(Einstellungen einstellungen) : ISkriptProzessFabrik
{
    public ISkriptProzess Erzeugen(SkriptArt art) => new SkriptProzess(art, einstellungen);
}
