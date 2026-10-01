using System.Buffers.Binary;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;

// Data-only inspection: never Assembly.Load, invoke methods, run constructors,
// resolve imported DLLs, or execute the uploaded game's entry point.
if (args.Length != 2) { Console.Error.WriteLine("Usage: AssemblyInspector input.exe output.json"); return 2; }
try
{
    var input = Path.GetFullPath(args[0]);
    var info = new FileInfo(input);
    if (!info.Exists || info.Length > 128L * 1024 * 1024) throw new InvalidDataException("Input size limit");
    using var stream = File.OpenRead(input);
    var digest = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    stream.Position = 0;
    using var pe = new PEReader(stream);
    if (!pe.HasMetadata || pe.PEHeaders.CorHeader is null) throw new InvalidDataException("Not a managed PE assembly");
    var metadata = pe.GetMetadataReader();
    if (!metadata.IsAssembly) throw new InvalidDataException("Not an assembly");
    var assembly = metadata.GetAssemblyDefinition();
    var ids = new SortedDictionary<string, SortedDictionary<string, object>>();
    foreach (var handle in metadata.TypeDefinitions)
    {
        var type = metadata.GetTypeDefinition(handle);
        if (metadata.GetString(type.Namespace) != "Terraria.ID") continue;
        var constants = new SortedDictionary<string, object>();
        foreach (var fieldHandle in type.GetFields())
        {
            var field = metadata.GetFieldDefinition(fieldHandle);
            var defaultHandle = field.GetDefaultValue();
            if (defaultHandle.IsNil) continue;
            var constant = metadata.GetConstant(defaultHandle);
            var reader = metadata.GetBlobReader(constant.Value);
            object? value = constant.TypeCode switch
            {
                ConstantTypeCode.SByte => reader.ReadSByte(),
                ConstantTypeCode.Byte => reader.ReadByte(),
                ConstantTypeCode.Int16 => reader.ReadInt16(),
                ConstantTypeCode.UInt16 => reader.ReadUInt16(),
                ConstantTypeCode.Int32 => reader.ReadInt32(),
                ConstantTypeCode.UInt32 => reader.ReadUInt32(),
                ConstantTypeCode.Int64 => reader.ReadInt64(),
                ConstantTypeCode.UInt64 => reader.ReadUInt64(),
                _ => null
            };
            if (value is not null) constants.Add(metadata.GetString(field.Name), value);
        }
        if (constants.Count != 0) ids.Add(metadata.GetString(type.Name), constants);
    }
    var languages = new SortedDictionary<string, JsonNode?>();
    var resourceDirectory = pe.PEHeaders.CorHeader.ResourcesDirectory;
    if (resourceDirectory.Size > 64 * 1024 * 1024) throw new InvalidDataException("Resource data exceeds limit");
    if (resourceDirectory.Size > 0)
    {
        var resources = pe.GetSectionData(resourceDirectory.RelativeVirtualAddress).GetContent(0, resourceDirectory.Size);
        foreach (var handle in metadata.ManifestResources)
        {
            var resource = metadata.GetManifestResource(handle);
            var name = metadata.GetString(resource.Name);
            if (!resource.Implementation.IsNil || !name.StartsWith("Terraria.Localization.Content.", StringComparison.Ordinal)
                || !(name.Contains("zh-Hans", StringComparison.Ordinal) || name.Contains("en-US", StringComparison.Ordinal))
                || !name.EndsWith(".json", StringComparison.Ordinal)) continue;
            var offset = checked((int)resource.Offset);
            if (offset < 0 || offset > resources.Length - 4) throw new InvalidDataException("Resource offset out of bounds");
            var length = BinaryPrimitives.ReadInt32LittleEndian(resources.AsSpan(offset, 4));
            if (length < 0 || length > 16 * 1024 * 1024 || length > resources.Length - offset - 4) throw new InvalidDataException("Resource length out of bounds");
            languages.Add(name, JsonNode.Parse(resources.AsSpan(offset + 4, length), documentOptions: new JsonDocumentOptions
                { AllowTrailingCommas = true, CommentHandling = JsonCommentHandling.Skip, MaxDepth = 64 }));
        }
    }
    var output = new
    {
        schemaVersion = 1, extractor = "static-pe-metadata-v1", executedInput = false,
        inputSha256 = digest, assemblyName = metadata.GetString(assembly.Name),
        gameVersion = assembly.Version.ToString(), ids, languages,
        complete = false,
        supported = new[] { "literal-id-constants", "embedded-localization-json" },
        unsupported = new[] { "runtime-item-defaults", "resolved-dynamic-tooltips", "map-runtime-rules", "client-textures", "player-draw-rules" }
    };
    var destination = Path.GetFullPath(args[1]);
    Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
    File.WriteAllText(destination, JsonSerializer.Serialize(output));
    Console.WriteLine(JsonSerializer.Serialize(new { passed = true, executedInput = false, idGroups = ids.Count, localizationFiles = languages.Count }));
    return 0;
}
catch (Exception exception) when (exception is IOException or BadImageFormatException or InvalidDataException or JsonException or OverflowException or ArgumentException)
{
    Console.Error.WriteLine("Static metadata inspection failed: " + exception.GetType().Name);
    return 1;
}
