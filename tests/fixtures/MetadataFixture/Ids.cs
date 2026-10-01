namespace Terraria.ID;
public static class ItemID
{
    public const short Synthetic = 1;
    public const ulong ExactLarge = 18446744073709551615UL;
    // Executing a type initializer must never be necessary for metadata reads.
    public static readonly string RuntimeOnly = ThrowIfExecuted();
    private static string ThrowIfExecuted() => throw new InvalidOperationException("Untrusted code was executed");
}
