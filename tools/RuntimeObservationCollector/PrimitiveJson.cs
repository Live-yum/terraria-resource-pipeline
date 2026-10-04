using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace RuntimeObservationCollector {
// Deliberately not an object serializer: no property traversal, converters,
// vendor ToString(), delegates, type tags or executable deserialization.
internal static class PrimitiveJson {
    internal const int MaximumBytes = 64 * 1024 * 1024;
    internal static byte[] Encode(object value) {
        using (MemoryStream stream = new MemoryStream()) {
            Write(stream, value, 0);
            return stream.ToArray();
        }
    }
    private static void Put(Stream s, string text) {
        byte[] bytes = new UTF8Encoding(false, true).GetBytes(text);
        if (s.Length + bytes.Length > MaximumBytes) throw new InvalidDataException("OUTPUT_BYTE_LIMIT");
        s.Write(bytes, 0, bytes.Length);
    }
    private static void String(Stream s, string value) {
        if (value.Length > 4096) throw new InvalidDataException("STRING_LIMIT");
        StringBuilder text = new StringBuilder("\"");
        foreach (char c in value) {
            if (c == '"' || c == '\\') text.Append('\\').Append(c);
            else if (c < 32 || c >= 127) text.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
            else text.Append(c);
        }
        // Validate even though escaping would otherwise hide an unpaired surrogate.
        new UTF8Encoding(false, true).GetByteCount(value);
        Put(s, text.Append('"').ToString());
    }
    private static void Write(Stream s, object value, int depth) {
        if (depth > 12) throw new InvalidDataException("JSON_DEPTH_LIMIT");
        if (value == null) { Put(s, "null"); return; }
        Type type = value.GetType();
        if (type == typeof(string)) { String(s, (string)value); return; }
        if (type == typeof(bool)) { Put(s, (bool)value ? "true" : "false"); return; }
        if (type == typeof(int)) { Put(s, ((int)value).ToString(CultureInfo.InvariantCulture)); return; }
        if (type == typeof(float)) {
            float n = (float)value;
            if (float.IsNaN(n) || float.IsInfinity(n)) throw new InvalidDataException("NONFINITE_NUMBER");
            Put(s, ((double)n).ToString("R", CultureInfo.InvariantCulture)); return;
        }
        if (type == typeof(SortedDictionary<string, object>)) {
            SortedDictionary<string, object> dictionary = (SortedDictionary<string, object>)value;
            if (dictionary.Count > 256) throw new InvalidDataException("OBJECT_MEMBER_LIMIT");
            Put(s, "{"); bool first = true;
            foreach (KeyValuePair<string, object> pair in dictionary) {
                if (!first) Put(s, ","); first = false;
                String(s, pair.Key); Put(s, ":"); Write(s, pair.Value, depth + 1);
            }
            Put(s, "}"); return;
        }
        if (type == typeof(object[]) || type == typeof(bool[]) || type == typeof(int[])) {
            Array array = (Array)value;
            if (array.Length > 65536) throw new InvalidDataException("ARRAY_LIMIT");
            Put(s, "[");
            for (int i = 0; i < array.Length; i++) { if (i != 0) Put(s, ","); Write(s, array.GetValue(i), depth + 1); }
            Put(s, "]"); return;
        }
        throw new InvalidDataException("UNAPPROVED_JSON_TYPE");
    }
    internal static SortedDictionary<string, object> Object(params object[] pairs) {
        if (pairs.Length % 2 != 0) throw new InvalidDataException("ODD_OBJECT_PAIRS");
        SortedDictionary<string, object> value = new SortedDictionary<string, object>(StringComparer.Ordinal);
        for (int i = 0; i < pairs.Length; i += 2) value.Add((string)pairs[i], pairs[i + 1]);
        return value;
    }
    internal static object Scalar(object value) {
        if (value == null) throw new InvalidDataException("MISSING_PRIMITIVE_FIELD");
        Type t = value.GetType();
        if (t == typeof(bool) || t == typeof(int) || t == typeof(float)) return value;
        if (t == typeof(byte)) return (int)(byte)value;
        if (t == typeof(sbyte)) return (int)(sbyte)value;
        if (t == typeof(short)) return (int)(short)value;
        if (t == typeof(ushort)) return (int)(ushort)value;
        throw new InvalidDataException("UNAPPROVED_ITEM_SCALAR");
    }
}}
