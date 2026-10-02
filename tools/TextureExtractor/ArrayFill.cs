namespace TConvert.Util;
internal static class ArrayFill {
 public static void Fill<T>(this T[] array, T value) => System.Array.Fill(array, value);
}
