using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

internal static class Program
{
    private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    private static Assembly game;
    private static string output;
    private static readonly Dictionary<string, object> families = new Dictionary<string, object>();
    private static readonly Dictionary<string, object> capabilities = new Dictionary<string, object>();
    private static object publicTextures;
    private static readonly List<string> missing = new List<string>();
    private static readonly List<string> warnings = new List<string>();
    private static readonly List<string> errors = new List<string>();
    private static readonly List<object> memoryStages = new List<object>();
    private static int[] paintIds = new int[0];
    private static readonly Dictionary<string,string> actualTextureKeys = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);

    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--oracle") return GameOracleProbe.Run(args.Skip(1).ToArray());
        if (args.Length != 4 || args[0] != "--server" || args[2] != "--output")
        {
            Console.Error.WriteLine("Usage: RuntimeExtractor --server /input/TerrariaServer.exe --output /output");
            return 2;
        }
        string server = Path.GetFullPath(args[1]);
        output = Path.GetFullPath(args[3]);
        if (!File.Exists(server)) { Console.Error.WriteLine("Server assembly missing: " + server); return 2; }
        Directory.CreateDirectory(output);
        File.Delete(Path.Combine(output, "result.json"));
        try
        {
            string directory = Path.GetDirectoryName(server);
            AppDomain.CurrentDomain.AssemblyResolve += (sender, ev) => {
                string name = new AssemblyName(ev.Name).Name;
                // Only bundled official siblings, never arbitrary workspace DLLs.
                if (name.IndexOfAny(new[] {'/', '\\'}) >= 0) return null;
                string sibling = Path.Combine(directory, name + ".dll");
                if (File.Exists(sibling)) return Assembly.LoadFrom(sibling);
                if (game == null) return null;
                string resource = game.GetManifestResourceNames().FirstOrDefault(n => n.EndsWith(name + ".dll", StringComparison.OrdinalIgnoreCase));
                if (resource == null) return null;
                using (var stream = game.GetManifestResourceStream(resource))
                using (var bytes = new MemoryStream()) { stream.CopyTo(bytes); return Assembly.Load(bytes.ToArray()); }
            };
            game = Assembly.LoadFrom(server);
            Console.Error.WriteLine("Loaded " + game.FullName);
            RecordMemory("assembly-loaded");
            Bootstrap();
            RecordMemory("bootstrap");
            Export();
            RecordMemory("export-complete");
            var manifest = Row("protocol", 1, "gameVersion", game.GetName().Version.ToString(),
                "assemblySha256", Sha256(server), "families", families, "publicTextures", publicTextures, "capabilities", capabilities,
                "missing", missing, "requiredMissing", missing, "warnings", warnings, "errors", errors,
                "idDomains", IdDomains(), "mapLayout", MapLayout(),
                "metrics", Row("stages", memoryStages, "peakRssBytes", PeakRss(), "cgroupPeakBytes", CgroupPeak()));
            File.WriteAllText(Path.Combine(output, "result.json"), Json.Serialize(manifest) + "\n", new UTF8Encoding(false));
            Console.Error.WriteLine("Completed " + families.Count + " families");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }

    internal static void BootstrapOracle(Assembly assembly, string directory)
    {
        game = assembly;
        output = directory;
        Bootstrap();
    }

    private static void Bootstrap()
    {
        Type program = TypeOf("Terraria.Program"), main = TypeOf("Terraria.Main");
        Field(program, "SavePath").SetValue(null, output);
        Field(program, "LaunchParameters").SetValue(null, new Dictionary<string, string>());
        Field(main, "dedServ").SetValue(null, false);
        Field(main, "myPlayer").SetValue(null, 0);
        Field(main, "rand").SetValue(null, Activator.CreateInstance(TypeOf("Terraria.Utilities.UnifiedRandom"), 0));
        Array players = Array.CreateInstance(TypeOf("Terraria.Player"), 256);
        players.SetValue(Activator.CreateInstance(TypeOf("Terraria.Player")), 0);
        Field(main, "player").SetValue(null, players);
        Type profileType = TypeOf("Terraria.GameInput.PlayerInputProfile");
        object profile = Activator.CreateInstance(profileType, "Runtime Extractor");
        Call(profile, "Initialize", Enum.Parse(TypeOf("Terraria.GameInput.PresetProfiles"), "Redigit"));
        Field(TypeOf("Terraria.GameInput.PlayerInput"), "_currentProfile").SetValue(null, profile);
        SetLanguage("en-US", false);
        Call(main, "Initialize_TileAndNPCData1");
        Call(main, "Initialize_TileAndNPCData2");
        Call(TypeOf("Terraria.ObjectData.TileObjectData"), "Initialize");
        InitializeDyeRegistries();
        Call(TypeOf("Terraria.ID.ContentSamples"), "Initialize");
        Call(main, "InitializeItemAnimations");
        Type armorSets = TypeOf("Terraria.DataStructures.ArmorSetBonuses");
        Call(armorSets, "Initialize");
        Call(armorSets, "BuildLookup");
        Call(TypeOf("Terraria.Map.MapHelper"), "Initialize");
        capabilities["runtimeInitialized"] = true;
    }

    private static void Export()
    {
        string dimensionsPath = Environment.GetEnvironmentVariable("TRP_TEXTURE_DIMENSIONS");
        var idNames = ExportIds();
        RecordMemory("ids");
        ExportLocalization();
        RecordMemory("localization");
        ExportItems(idNames.ContainsKey("ItemID") ? idNames["ItemID"] : new Dictionary<int,string>());
        RecordMemory("items");
        ExportMap(idNames);
        RecordMemory("map");
        ExportTileRules(idNames);
        RecordMemory("tile-rules");
        ExportMountLayouts(idNames.ContainsKey("MountID") ? idNames["MountID"] : new Dictionary<int,string>(), dimensionsPath);
        RecordMemory("mount-layouts");
        ExportArmorSets();
        RecordMemory("armor-sets");
        if (!string.IsNullOrEmpty(dimensionsPath))
        {
            if (!File.Exists(dimensionsPath)) throw new FileNotFoundException("Texture dimensions index missing", dimensionsPath);
            ExportTextureReferences(dimensionsPath, idNames);
            RecordMemory("texture-references");
        }
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        ExportSimpleNames(idNames);
        RecordMemory("bestiary-and-frames");
        ExportDyeShaders();
        RecordMemory("dye-shaders");
        if (!string.IsNullOrEmpty(dimensionsPath))
        {
            PlayerDrawing.Result drawPlans = PlayerDrawing.Export(game, output, dimensionsPath);
            families["player-draw-plans"] = Row("path", drawPlans.Path, "count", drawPlans.Count, "sha256", drawPlans.Sha256);
            object drawPeakRss = drawPlans.Capabilities["peakRssBytes"];
            drawPlans.Capabilities.Remove("peakRssBytes");
            capabilities["playerDrawPlans"] = drawPlans.Capabilities;
            errors.AddRange(drawPlans.Errors);
            missing.AddRange(drawPlans.Errors.Select(error => "player draw plan: " + error));
            RecordMemory("player-draw-plans");
            ((Dictionary<string, object>)memoryStages[memoryStages.Count - 1])["drawPeakRssBytes"] = drawPeakRss;
            PlayerTextureClosure.Result closure = PlayerTextureClosure.Export(game, output, dimensionsPath);
            publicTextures = Row("path", closure.SelectedPath, "count", closure.Count, "sha256", closure.Sha256);
            families["player-texture-bindings"] = Row("path", closure.BindingsPath, "count", closure.BindingCount, "sha256", closure.BindingsSha256);
            capabilities["publicTextureClosure"] = closure.Capabilities;
            foreach (string failure in closure.Errors.Take(20))
            {
                errors.Add("public texture closure: " + failure);
                missing.Add("public texture closure: " + failure);
            }
            if (closure.Errors.Count > 20) missing.Add("public texture closure: " + closure.Errors.Count + " unresolved; see " + closure.ProofPath);
            RecordMemory("public-texture-closure");
        }
        else
        {
            capabilities["playerDrawPlans"] = Row("officialDrawData", false, "reason", "TRP_TEXTURE_DIMENSIONS index not supplied");
            missing.Add("player draw plans require a Content texture dimensions index");
        }
        warnings.Add("player draw plans sample finite profiles; arbitrary game state and shader/render-target effects need local client composition");
        warnings.Add("NPC rendered frames and animation states are outside this metadata extractor");
        capabilities["playerCompositeFrames"] = false;
        capabilities["npcRenderedFrames"] = false;
        capabilities["fullDisplayTooltips"] = false;
        capabilities["source"] = "server assembly initialized via reflection";
    }

    private static Dictionary<string, Dictionary<int, string>> ExportIds()
    {
        var result = new Dictionary<string, Dictionary<int, string>>();
        using (var file = OpenFamily("ids"))
        {
            foreach (Type type in game.GetTypes().Where(t => t.Namespace == "Terraria.ID").OrderBy(t => t.FullName))
            {
                string familyName = type.FullName.Substring("Terraria.ID.".Length);
                var names = new Dictionary<int, string>();
                foreach (FieldInfo field in type.GetFields(BindingFlags.Public | BindingFlags.Static).Where(f => f.IsLiteral && f.FieldType.IsPrimitive).OrderBy(f => f.Name))
                {
                    object raw = field.GetRawConstantValue();
                    if (raw is bool) continue;
                    long id = Convert.ToInt64(raw, CultureInfo.InvariantCulture);
                    file.Write(Row("id", familyName + ":" + field.Name, "family", familyName, "name", field.Name, "value", id));
                    if (id >= int.MinValue && id <= int.MaxValue && !names.ContainsKey((int)id)) names[(int)id] = field.Name;
                }
                if (names.Count != 0) result[familyName] = names;
            }
        }
        return result;
    }

    private static void ExportLocalization()
    {
        Type managerType = TypeOf("Terraria.Localization.LanguageManager");
        object manager = Field(managerType, "Instance").GetValue(null);
        var languages = new Dictionary<string, Dictionary<string, string>>();
        foreach (string culture in new[] {"en-US", "zh-Hans"})
        {
            SetLanguage(culture, true);
            var texts = (IDictionary)Field(managerType, "_localizedTexts").GetValue(manager);
            var values = new Dictionary<string,string>();
            foreach (DictionaryEntry entry in texts)
                values[(string)entry.Key] = Convert.ToString(Member(entry.Value, "UnformattedValue"), CultureInfo.InvariantCulture);
            languages[culture] = values;
        }
        using (var file = OpenFamily("localization"))
        {
            foreach (string key in languages["en-US"].Keys.Union(languages["zh-Hans"].Keys).OrderBy(k => k, StringComparer.Ordinal))
                file.Write(Row("id", key, "en-US", languages["en-US"].ContainsKey(key) ? languages["en-US"][key] : null,
                    "zh-Hans", languages["zh-Hans"].ContainsKey(key) ? languages["zh-Hans"][key] : null));
        }
        capabilities["localization"] = Row("available", true, "source", "LanguageManager._localizedTexts.UnformattedValue after SetLanguage");
    }

    private static bool CanExport(Type type)
    {
        Type nullable = Nullable.GetUnderlyingType(type);
        if (nullable != null) return CanExport(nullable);
        if (type.IsEnum || type == typeof(string) || type == typeof(decimal)) return true;
        if (type.IsPrimitive) return type != typeof(IntPtr) && type != typeof(UIntPtr);
        if (type.IsArray) return type.GetArrayRank() == 1 && CanExport(type.GetElementType());
        return type.FullName == "Terraria.Audio.LegacySoundStyle"
            || type.FullName == "Microsoft.Xna.Framework.Color" || type.FullName == "Microsoft.Xna.Framework.Vector2"
            || type.FullName == "Microsoft.Xna.Framework.Point" || type.FullName == "Microsoft.Xna.Framework.Rectangle";
    }

    private static object GameValue(object value, Type type)
    {
        if (value == null) return null;
        Type nullable = Nullable.GetUnderlyingType(type);
        if (nullable != null) return GameValue(value, nullable);
        if (type.IsEnum) return Row("name", value.ToString(), "value", Convert.ToInt64(value, CultureInfo.InvariantCulture));
        if (type.IsArray)
        {
            Array array = (Array)value;
            if (array.Length > 1024) return Row("length", array.Length, "omitted", "array exceeds 1024 values");
            var values = new object[array.Length];
            for (int i = 0; i < values.Length; i++) values[i] = GameValue(array.GetValue(i), type.GetElementType());
            return values;
        }
        if (type.FullName == "Microsoft.Xna.Framework.Color")
            return Row("r", ByteMember(value, "R"), "g", ByteMember(value, "G"), "b", ByteMember(value, "B"), "a", ByteMember(value, "A"));
        if (type.FullName == "Microsoft.Xna.Framework.Vector2" || type.FullName == "Microsoft.Xna.Framework.Point")
            return Row("x", Member(value, "X"), "y", Member(value, "Y"));
        if (type.FullName == "Microsoft.Xna.Framework.Rectangle")
            return Row("x", Member(value, "X"), "y", Member(value, "Y"), "width", Member(value, "Width"), "height", Member(value, "Height"));
        if (type.FullName == "Terraria.Audio.LegacySoundStyle")
            return Row("soundId", Member(value, "SoundId"), "styleStart", Member(value, "_style"),
                "variations", Member(value, "Variations"), "type", Member(value, "Type").ToString(),
                "volume", Member(value, "Volume"), "pitchVariance", Member(value, "PitchVariance"),
                "maxTrackedInstances", Member(value, "MaxTrackedInstances"));
        if (type == typeof(float) && (float.IsNaN((float)value) || float.IsInfinity((float)value))) return null;
        if (type == typeof(double) && (double.IsNaN((double)value) || double.IsInfinity((double)value))) return null;
        return value;
    }

    private static void ExportItems(Dictionary<int,string> names)
    {
        IDictionary samples = (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "ItemsByType").GetValue(null);
        Type lang = TypeOf("Terraria.Lang");
        var localized = new Dictionary<string, Dictionary<int,string>>();
        var tooltips = new Dictionary<string, Dictionary<int,string[]>>();
        var uiTooltips = new Dictionary<string, Dictionary<int,object>>();
        int uiSuccesses = 0, uiFailures = 0;
        MethodInfo uiLines = TypeOf("Terraria.Main").GetMethod("MouseText_DrawItemTooltip_GetLinesInfo", BindingFlags.Public | BindingFlags.Static);
        Type colorType = TypeOf("Terraria.Main").Assembly.GetType("Microsoft.Xna.Framework.Color", false)
            ?? AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Microsoft.Xna.Framework.Color", false)).First(t => t != null);
        foreach (string culture in new[] {"en-US", "zh-Hans"})
        {
            SetLanguage(culture, true);
            var itemNames = new Dictionary<int,string>();
            var itemTooltips = new Dictionary<int,string[]>();
            var itemUiTooltips = new Dictionary<int,object>();
            foreach (DictionaryEntry entry in samples)
            {
                int id = Convert.ToInt32(entry.Key, CultureInfo.InvariantCulture);
                if (id <= 0 || entry.Value == null) continue;
                itemNames[id] = Convert.ToString(Call(lang, "GetItemNameValue", id), CultureInfo.InvariantCulture);
                object tip = Call(lang, "GetTooltip", id);
                int count = Convert.ToInt32(Property(tip.GetType(), "Lines").GetValue(tip, null), CultureInfo.InvariantCulture);
                var lines = new string[count];
                for (int i = 0; i < count; i++) lines[i] = Convert.ToString(Call(tip, "GetLine", i), CultureInfo.InvariantCulture);
                itemTooltips[id] = lines;
                if (uiLines != null)
                {
                    var textLines = new string[64];
                    object[] parameters = {entry.Value, 0, Member(entry.Value, "knockBack"), 1, textLines, Array.CreateInstance(colorType, 64)};
                    try
                    {
                        uiLines.Invoke(null, parameters);
                        int lineCount = Math.Min(Convert.ToInt32(parameters[3], CultureInfo.InvariantCulture), textLines.Length);
                        itemUiTooltips[id] = Row("lines", textLines.Take(lineCount).ToArray(), "error", null);
                        uiSuccesses++;
                    }
                    catch (TargetInvocationException ex)
                    {
                        itemUiTooltips[id] = Row("lines", new string[0], "error", ex.InnerException == null ? ex.GetType().Name : ex.InnerException.GetType().Name);
                        uiFailures++;
                    }
                }
            }
            localized[culture] = itemNames;
            tooltips[culture] = itemTooltips;
            uiTooltips[culture] = itemUiTooltips;
        }
        var blocked = (bool[])Field(TypeOf("Terraria.ID.ItemID+Sets"), "ItemsThatShouldNotBeInInventory").GetValue(null);
        FieldInfo[] itemFields = TypeOf("Terraria.Item").GetFields(BindingFlags.Public | BindingFlags.Instance).OrderBy(f => f.Name).ToArray();
        MethodInfo prefixStats = FieldlessPrefixStatsMethod();
        var prefixPools = TypeOf("Terraria.GameContent.Prefixes.PrefixLegacy+Prefixes")
            .GetFields(BindingFlags.Public | BindingFlags.Static).Where(field => field.FieldType == typeof(int[]))
            .ToDictionary(field => field.Name, field => (int[])field.GetValue(null), StringComparer.Ordinal);
        int prefixableItems = 0, effectivePrefixPairs = 0, unavailableSamples = 0, aliasedSamples = 0;
        using (var schema = OpenFamily("item-field-schema"))
            foreach (FieldInfo field in itemFields)
                schema.Write(Row("id", field.Name, "type", field.FieldType.FullName, "exported", CanExport(field.FieldType)));
        Type creative = TypeOf("Terraria.GameContent.Creative.CreativeItemSacrificesCatalog");
        object catalog = Field(creative, "Instance").GetValue(null);
        Call(catalog, "Initialize");
        IDictionary research = (IDictionary)Member(catalog, "SacrificeCountNeededByItemId");
        Type samplesType = TypeOf("Terraria.ID.ContentSamples");
        IDictionary persistentIds = Field(samplesType, "ItemPersistentIdsByNetIds").GetValue(null) as IDictionary;
        IDictionary researchOverrides = Field(samplesType, "CreativeResearchItemPersistentIdOverride").GetValue(null) as IDictionary;
        if (persistentIds == null || researchOverrides == null)
            throw new InvalidDataException("Official creative research persistent ID dictionaries unavailable");
        using (var items = OpenFamily("items"))
        using (var tips = OpenFamily("item-tooltips"))
        using (var ui = OpenFamily("item-ui-tooltips"))
        using (var counts = OpenFamily("research"))
        using (var paints = OpenFamily("paints"))
        {
            var paintIds = new HashSet<int>();
            foreach (DictionaryEntry entry in samples)
            {
                int id = Convert.ToInt32(entry.Key, CultureInfo.InvariantCulture);
                if (id <= 0 || entry.Value == null) continue;
                object item = entry.Value;
                int sampleType = Convert.ToInt32(Member(item, "type"), CultureInfo.InvariantCulture);
                if (sampleType == 0) unavailableSamples++;
                else if (sampleType != id) aliasedSamples++;
                var gameplay = new Dictionary<string,object>();
                foreach (FieldInfo field in itemFields)
                    if (CanExport(field.FieldType)) gameplay[field.Name] = GameValue(field.GetValue(item), field.FieldType);
                int[] rollable = (int[])Call(item, "GetRollablePrefixes") ?? new int[0];
                string poolName = rollable.Length == 0 ? null : prefixPools.FirstOrDefault(pair => Object.ReferenceEquals(pair.Value, rollable)).Key;
                if (rollable.Length != 0 && poolName == null) throw new InvalidDataException("Unknown official prefix pool for item " + id);
                var eligible = new List<int>();
                foreach (int prefix in rollable)
                    if (InvokePrefixStats(item, prefixStats, prefix, null)) eligible.Add(prefix);
                if (rollable.Length != 0) prefixableItems++;
                effectivePrefixPairs += eligible.Count;
                int sacrifice = research.Contains(id) ? Convert.ToInt32(research[id], CultureInfo.InvariantCulture) : 0;
                int persistentItemId = researchOverrides.Contains(id)
                    ? Convert.ToInt32(researchOverrides[id], CultureInfo.InvariantCulture) : id;
                string persistentId = persistentIds.Contains(persistentItemId)
                    ? persistentIds[persistentItemId] as string : null;
                if (persistentItemId <= 0 || string.IsNullOrEmpty(persistentId))
                    throw new InvalidDataException("Official creative research persistent ID unavailable for item " + id + " -> " + persistentItemId);
                string internalName = names.ContainsKey(id) ? names[id] : "Item_" + id;
                items.Write(Row("id", id, "internalName", internalName, "sampleType", sampleType,
                    "sampleStatus", sampleType == id ? "direct" : sampleType == 0 ? "unavailable" : "alias",
                    "name", Row("en-US", localized["en-US"][id], "zh-Hans", localized["zh-Hans"][id]),
                    "gameplay", gameplay, "research", sacrifice, "isDeprecated", id < blocked.Length && blocked[id],
                    "prefixPool", poolName, "rollablePrefixes", rollable, "eligiblePrefixes", eligible));
                counts.Write(Row("id", id, "required", sacrifice, "persistentItemId", persistentItemId,
                    "persistentId", persistentId));
                tips.Write(Row("id", id, "en-US", tooltips["en-US"][id], "zh-Hans", tooltips["zh-Hans"][id],
                    "context", "default keyboard input profile; no world; localized base tooltip",
                    "source", "Lang.GetTooltip(itemId).GetLine"));
                if (uiLines != null) ui.Write(Row("id", id, "en-US", uiTooltips["en-US"][id], "zh-Hans", uiTooltips["zh-Hans"][id],
                    "context", "default player and item sample; no world events, chest, equipment or UI inventory context",
                    "source", "Main.MouseText_DrawItemTooltip_GetLinesInfo"));
                int paint = Convert.ToInt32(Member(item, "paint"), CultureInfo.InvariantCulture);
                if (paint > 0 && paintIds.Add(paint))
                {
                    object color = Call(TypeOf("Terraria.WorldGen"), "paintColor", paint);
                    paints.Write(Row("id", paint, "itemId", id, "internalName", internalName,
                        "name", Row("en-US", localized["en-US"][id], "zh-Hans", localized["zh-Hans"][id]),
                        "color", color == null ? null : GameValue(color, color.GetType())));
                }
            }
            Program.paintIds = paintIds.OrderBy(n => n).ToArray();
        }
        capabilities["items"] = Row("available", true, "source", "ContentSamples.ItemsByType; Lang.GetItemNameValue/GetTooltip; CreativeItemSacrificesCatalog",
            "prefixableItems", prefixableItems, "effectivePrefixPairs", effectivePrefixPairs,
            "unavailableSamples", unavailableSamples, "aliasedSamples", aliasedSamples,
            "prefixSource", "Item.GetRollablePrefixes and Item.TryGetPrefixStatMultipliersForItem on default item samples");
        capabilities["localizedBaseTooltips"] = true;
        capabilities["defaultUiTooltips"] = Row("available", uiLines != null && uiSuccesses > 0,
            "successfulLocales", uiSuccesses, "failedLocales", uiFailures,
            "source", "Main.MouseText_DrawItemTooltip_GetLinesInfo", "context", "default player and sample item");
    }

    private static void ExportMap(Dictionary<string, Dictionary<int,string>> ids)
    {
        Type map = TypeOf("Terraria.Map.MapHelper"), lang = TypeOf("Terraria.Lang");
        Type mapTile = TypeOf("Terraria.Map.MapTile");
        MethodInfo makeTile = mapTile.GetMethod("Create", BindingFlags.Public | BindingFlags.Static);
        MethodInfo mapColor = map.GetMethods(BindingFlags.Public | BindingFlags.Static).Single(m => m.Name == "GetMapTileXnaColor");
        bool[] frameImportant = (bool[])Field(TypeOf("Terraria.Main"), "tileFrameImportant").GetValue(null);
        var colors = (Array)Field(map, "colorLookup").GetValue(null);
        var mapNames = new Dictionary<string, Dictionary<int, string>>();
        foreach (string culture in new[] {"en-US", "zh-Hans"})
        {
            SetLanguage(culture, true);
            var values = new Dictionary<int, string>();
            foreach (string kind in new[] {"tile", "wall"})
            {
                int[] optionCounts = (int[])Field(map, kind + "OptionCounts").GetValue(null);
                ushort[] lookup = (ushort[])Field(map, kind + "Lookup").GetValue(null);
                for (int id = 0; id < optionCounts.Length; id++)
                    for (int variant = 0; variant < optionCounts[id]; variant++)
                    {
                        int index = lookup[id] + variant;
                        if (!values.ContainsKey(index)) values[index] = Convert.ToString(Call(lang, "GetMapObjectName", index), CultureInfo.InvariantCulture);
                    }
            }
            mapNames[culture] = values;
        }
        using (var tiles = OpenFamily("tiles"))
        using (var walls = OpenFamily("walls"))
        using (var palette = OpenFamily("map"))
        using (var pixels = OpenFamily("pixel-candidates"))
        {
            foreach (string kind in new[] {"tile", "wall"})
            {
                int[] optionCounts = (int[])Field(map, kind + "OptionCounts").GetValue(null);
                ushort[] lookup = (ushort[])Field(map, kind + "Lookup").GetValue(null);
                string idFamily = kind == "tile" ? "TileID" : "WallID";
                FamilyWriter target = kind == "tile" ? tiles : walls;
                for (int id = 0; id < optionCounts.Length; id++)
                {
                    for (int variant = 0; variant < optionCounts[id]; variant++)
                    {
                        int mapIndex = lookup[id] + variant;
                        var names = Row("en-US", mapNames["en-US"][mapIndex], "zh-Hans", mapNames["zh-Hans"][mapIndex]);
                        object color = colors.GetValue(mapIndex);
                        var rgb = Row("r", ByteMember(color, "R"), "g", ByteMember(color, "G"), "b", ByteMember(color, "B"));
                        string key = id + ":" + variant;
                        target.Write(Row("id", key, "type", id, "variant", variant, "internalName", ids.ContainsKey(idFamily) && ids[idFamily].ContainsKey(id) ? ids[idFamily][id] : null,
                            "name", names, "mapIndex", mapIndex, "color", rgb));
                        palette.Write(Row("id", kind + ":" + key, "kind", kind, "type", id, "variant", variant, "mapIndex", mapIndex, "color", rgb));
                        string reason = StabilityExclusion(kind, id, variant, optionCounts[id], frameImportant, mapIndex);
                        bool stable = reason == null;
                        foreach (int paint in new[] {0}.Concat(paintIds))
                        {
                            object paintedMapTile = makeTile.Invoke(null, new object[] { checked((ushort)mapIndex), byte.MaxValue, checked((byte)paint) });
                            object paintedColor = mapColor.Invoke(null, mapColor.GetParameters().Length == 3
                                ? new object[] { paintedMapTile, 140, 140 } : new object[] { paintedMapTile });
                            pixels.Write(Row("id", kind + ":" + id + ":" + variant + ":" + paint,
                                "kind", kind, "type", id, "variant", variant, "paint", paint,
                                "color", Row("r", ByteMember(paintedColor, "R"), "g", ByteMember(paintedColor, "G"), "b", ByteMember(paintedColor, "B")),
                                "stable", stable, "exclusionReason", reason,
                                "source", "MapTile.Create; MapHelper.GetMapTileXnaColor"));
                        }
                    }
                }
            }
        }
        using (var fullPalette = OpenFamily("map-palette"))
            for (int index = 0; index < colors.Length; index++)
            {
                object color = colors.GetValue(index);
                fullPalette.Write(Row("id", index, "color", Row("r", ByteMember(color, "R"), "g", ByteMember(color, "G"), "b", ByteMember(color, "B"), "a", ByteMember(color, "A"))));
            }
        using (var lookups = OpenFamily("map-lookup"))
            foreach (string kind in new[] {"tile", "wall"})
            {
                int[] optionCounts = (int[])Field(map, kind + "OptionCounts").GetValue(null);
                ushort[] lookup = (ushort[])Field(map, kind + "Lookup").GetValue(null);
                for (int id = 0; id < optionCounts.Length; id++)
                    lookups.Write(Row("id", kind + ":" + id, "kind", kind, "type", id,
                        "mapIndex", lookup[id], "optionCount", optionCounts[id]));
            }
        capabilities["map"] = Row("available", true, "source", "MapHelper.Initialize tile/wall lookup and colorLookup; Lang.GetMapObjectName");
        capabilities["paintedPixels"] = Row("available", true, "source", "MapTile.Create + MapHelper.GetMapTileXnaColor",
            "stable", "single option, non-frame-important, non-biome-flagged, 1x1 object data and CreateMapTile samples");
    }

    private static string StabilityExclusion(string kind, int id, int variant, int options, bool[] frameImportant, int mapIndex)
    {
        if (variant != 0 || options != 1) return "multiple-map-options";
        if (kind == "tile")
        {
            if (id < frameImportant.Length && frameImportant[id]) return "frame-important";
            Type sets = TypeOf("Terraria.ID.TileID+Sets");
            foreach (string name in new[] {"Corrupt", "Crimson", "Hallow", "SpreadsCorruption", "SpreadsCrimson", "SpreadsHallow"})
                if (SetFlag(sets, name, id)) return "biome-or-spread-flag:" + name;
            object data = Call(TypeOf("Terraria.ObjectData.TileObjectData"), "GetTileData", id, 0, 0);
            if (data != null && (Convert.ToInt32(Member(data, "Width")) != 1 || Convert.ToInt32(Member(data, "Height")) != 1))
                return "multi-cell-object";
        }
        else
        {
            Type sets = TypeOf("Terraria.ID.WallID+Sets");
            foreach (string name in new[] {"SpreadsCorruption", "SpreadsCrimson", "SpreadsHallow"})
                if (SetFlag(sets, name, id)) return "biome-or-spread-flag:" + name;
        }
        foreach (var point in new[] { new[] {140,140}, new[] {141,140}, new[] {140,141}, new[] {140,1000} })
        {
            try
            {
                Array tiles = (Array)Field(TypeOf("Terraria.Main"), "tile").GetValue(null);
                int x = point[0], y = Math.Min(point[1], tiles.GetLength(1) - 2);
                if (x < 2 || y < 2) return "sample-area-too-small";
                for (int nx = x - 1; nx <= x + 1; nx++)
                    for (int ny = y - 1; ny <= y + 1; ny++)
                        tiles.SetValue(Activator.CreateInstance(TypeOf("Terraria.Tile")), nx, ny);
                object tile = tiles.GetValue(x, y);
                if (kind == "tile")
                {
                    Call(tile, "active", true);
                    Field(tile.GetType(), "type").SetValue(tile, checked((ushort)id));
                }
                else Field(tile.GetType(), "wall").SetValue(tile, checked((ushort)id));
                object actual = Call(TypeOf("Terraria.Map.MapHelper"), "CreateMapTile", x, y, byte.MaxValue, 0);
                if (Convert.ToInt32(Member(actual, "Type"), CultureInfo.InvariantCulture) != mapIndex) return "context-dependent-map-type";
            }
            catch (Exception) { return "sample-failed"; }
        }
        return null;
    }
    private static bool SetFlag(Type sets, string name, int id)
    {
        FieldInfo field = sets.GetField(name, Any);
        if (field == null || !(field.GetValue(null) is bool[] values)) return false;
        return id >= 0 && id < values.Length && values[id];
    }

    private static Dictionary<string,object> IdDomains()
    {
        var domains = new Dictionary<string,object>();
        foreach (Type type in game.GetTypes().Where(t => t.Namespace == "Terraria.ID").OrderBy(t => t.FullName))
        {
            FieldInfo count = type.GetField("Count", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            if (count == null || !count.FieldType.IsPrimitive) continue;
            try { domains[type.FullName.Substring("Terraria.ID.".Length)] = Convert.ToInt64(count.GetValue(null), CultureInfo.InvariantCulture); }
            catch (Exception ex) { errors.Add("domain " + type.FullName + ": " + ex.GetType().Name); }
        }
        return domains;
    }

    private static Dictionary<string,object> MapLayout()
    {
        Type map = TypeOf("Terraria.Map.MapHelper");
        var layout = new Dictionary<string,object>();
        foreach (string name in new[] {"tilePosition", "wallPosition", "liquidPosition", "skyPosition", "dirtPosition", "rockPosition", "hellPosition"})
            layout[name] = Convert.ToInt32(Field(map, name).GetValue(null), CultureInfo.InvariantCulture);
        layout["lookupCount"] = ((Array)Field(map, "colorLookup").GetValue(null)).Length;
        layout["curRelease"] = Convert.ToInt32(Field(TypeOf("Terraria.Main"), "curRelease").GetRawConstantValue(), CultureInfo.InvariantCulture);
        return layout;
    }

    private static void ExportTileRules(Dictionary<string, Dictionary<int,string>> ids)
    {
        ExportSets("tile", ids.ContainsKey("TileID") ? ids["TileID"] : new Dictionary<int,string>());
        ExportSets("wall", ids.ContainsKey("WallID") ? ids["WallID"] : new Dictionary<int,string>());
        Type tileDataType = TypeOf("Terraria.ObjectData.TileObjectData");
        IList data = (IList)Field(tileDataType, "_data").GetValue(null);
        IDictionary samples = (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "ItemsByType").GetValue(null);
        var stylesByTile = new Dictionary<int, SortedSet<int>>();
        foreach (DictionaryEntry entry in samples)
        {
            object item = entry.Value;
            if (item == null) continue;
            int tile = Convert.ToInt32(Member(item, "createTile"), CultureInfo.InvariantCulture);
            if (tile < 0) continue;
            int style = Math.Max(0, Convert.ToInt32(Member(item, "placeStyle"), CultureInfo.InvariantCulture));
            if (!stylesByTile.ContainsKey(tile)) stylesByTile[tile] = new SortedSet<int>();
            stylesByTile[tile].Add(style);
        }
        int coveredIds = 0, variants = 0;
        var definedIds = new HashSet<int>();
        using (var file = OpenFamily("tile-object-data"))
        {
            for (int tile = 0; tile < data.Count; tile++)
            {
                object baseData = data[tile];
                if (baseData == null) continue;
                coveredIds++;
                definedIds.Add(tile);
                var styles = new SortedSet<int> {0};
                if (stylesByTile.TryGetValue(tile, out var itemStyles)) styles.UnionWith(itemStyles);
                IList subTiles = Member(baseData, "SubTiles") as IList;
                if (subTiles != null)
                    for (int i = 0; i < subTiles.Count; i++) if (subTiles[i] != null) styles.Add(i);
                foreach (int style in styles)
                {
                    object first = Call(tileDataType, "GetTileData", tile, style, 0);
                    if (first == null) continue;
                    IList alternates = Member(first, "Alternates") as IList;
                    int alternateCount = alternates == null ? 0 : alternates.Count;
                    for (int alternate = 0; alternate <= alternateCount; alternate++)
                    {
                        if (alternate > 0 && alternates[alternate - 1] == null) continue;
                        object variant = alternate == 0 ? first : Call(tileDataType, "GetTileData", tile, style, alternate);
                        if (variant == null) continue;
                        int randomCount = Math.Max(1, Convert.ToInt32(Member(variant, "RandomStyleRange"), CultureInfo.InvariantCulture));
                        if (randomCount > 256) throw new InvalidDataException("TileObjectData random range exceeds 256 for tile " + tile);
                        for (int random = 0; random < randomCount; random++)
                        {
                            int placementStyle = Convert.ToInt32(Call(variant, "CalculatePlacementStyle", style, alternate, random), CultureInfo.InvariantCulture);
                            int fullWidth = Convert.ToInt32(Member(variant, "CoordinateFullWidth"), CultureInfo.InvariantCulture);
                            int fullHeight = Convert.ToInt32(Member(variant, "CoordinateFullHeight"), CultureInfo.InvariantCulture);
                            int wrap = Convert.ToInt32(Member(variant, "StyleWrapLimit"), CultureInfo.InvariantCulture);
                            int lineSkip = Convert.ToInt32(Member(variant, "StyleLineSkip"), CultureInfo.InvariantCulture);
                            bool horizontal = Convert.ToBoolean(Member(variant, "StyleHorizontal"), CultureInfo.InvariantCulture);
                            int frameStyle = wrap > 0 ? placementStyle % wrap : placementStyle;
                            int frameLine = wrap > 0 ? placementStyle / wrap * lineSkip : 0;
                            int frameX = horizontal ? fullWidth * frameStyle : fullWidth * frameLine;
                            int frameY = horizontal ? fullHeight * frameLine : fullHeight * frameStyle;
                            file.Write(Row("id", tile + ":" + style + ":" + alternate + ":" + random,
                                "tile", tile, "internalName", ids.ContainsKey("TileID") && ids["TileID"].ContainsKey(tile) ? ids["TileID"][tile] : null,
                                "style", style, "alternate", alternate, "random", random, "placementStyle", placementStyle,
                                "frameX", frameX, "frameY", frameY,
                                "width", Member(variant, "Width"), "height", Member(variant, "Height"),
                                "coordinateWidth", Member(variant, "CoordinateWidth"), "coordinateHeights", Member(variant, "CoordinateHeights"),
                                "coordinatePadding", Member(variant, "CoordinatePadding"),
                                "coordinateFullWidth", fullWidth, "coordinateFullHeight", fullHeight,
                                "styleBase", Member(variant, "Style"), "styleHorizontal", horizontal,
                                "styleWrapLimit", wrap, "styleLineSkip", lineSkip, "styleMultiplier", Member(variant, "StyleMultiplier"),
                                "randomStyleRange", Member(variant, "RandomStyleRange"),
                                "drawYOffset", Member(variant, "DrawYOffset"), "drawStepDown", Member(variant, "DrawStepDown"),
                                "source", "TileObjectData.GetTileData and CalculatePlacementStyle"));
                            variants++;
                        }
                    }
                }
            }
        }
        bool[] frameImportant = (bool[])Field(TypeOf("Terraria.Main"), "tileFrameImportant").GetValue(null);
        int[] missingFrameImportant = Enumerable.Range(0, Math.Min(frameImportant.Length, data.Count))
            .Where(id => frameImportant[id] && !definedIds.Contains(id)).ToArray();
        capabilities["tileObjectData"] = Row("available", true, "coveredTileIds", coveredIds,
            "totalTileIds", data.Count, "variants", variants,
            "frameImportantWithData", definedIds.Count(id => frameImportant[id]),
            "frameImportantWithoutData", missingFrameImportant,
            "source", "TileObjectData._data/GetTileData/CalculatePlacementStyle; ContentSamples item placeStyle");
    }

    private static void ExportSets(string kind, Dictionary<int,string> names)
    {
        string idType = kind == "tile" ? "TileID" : "WallID";
        Type sets = TypeOf("Terraria.ID." + idType + "+Sets");
        int count = Convert.ToInt32(Field(TypeOf("Terraria.ID." + idType), "Count").GetValue(null), CultureInfo.InvariantCulture);
        var indexedFields = new List<KeyValuePair<string,Array>>();
        foreach (FieldInfo field in sets.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static).OrderBy(f => f.Name))
        {
            if (!field.FieldType.IsArray || field.FieldType.GetArrayRank() != 1 || !CanExport(field.FieldType.GetElementType())) continue;
            Array values = field.GetValue(null) as Array;
            if (values != null && values.Length >= count) indexedFields.Add(new KeyValuePair<string,Array>(field.Name, values));
        }
        bool[] frameImportant = kind == "tile" ? (bool[])Field(TypeOf("Terraria.Main"), "tileFrameImportant").GetValue(null) : null;
        using (var file = OpenFamily(kind + "-sets"))
            for (int id = 0; id < count; id++)
            {
                var values = new Dictionary<string,object>();
                foreach (var field in indexedFields) values[field.Key] = GameValue(field.Value.GetValue(id), field.Value.GetType().GetElementType());
                if (frameImportant != null) values["Main.tileFrameImportant"] = frameImportant[id];
                file.Write(Row("id", id, "internalName", names.ContainsKey(id) ? names[id] : null,
                    "sets", values, "source", "Terraria.ID." + idType + ".Sets"));
            }
        capabilities[kind + "Sets"] = Row("available", true, "indexedFieldCount", indexedFields.Count,
            "entityCount", count, "source", "Terraria.ID." + idType + ".Sets static arrays");
    }

    private static bool CanArmorValue(Type type, int depth)
    {
        if (CanExport(type)) return true;
        if (depth >= 3 || !type.IsValueType || type.IsPrimitive || type.IsEnum) return false;
        FieldInfo[] fields = type.GetFields(BindingFlags.Public | BindingFlags.Instance);
        return fields.Length > 0 && fields.Length <= 32 && fields.All(field => CanArmorValue(field.FieldType, depth + 1));
    }

    private static object ArmorValue(object value, Type type, int depth)
    {
        if (value == null) return null;
        if (CanExport(type)) return GameValue(value, type);
        if (!CanArmorValue(type, depth)) throw new NotSupportedException("Armor set value type: " + type.FullName);
        var fields = new Dictionary<string,object>();
        foreach (FieldInfo field in type.GetFields(BindingFlags.Public | BindingFlags.Instance).OrderBy(field => field.Name))
            fields[field.Name] = ArmorValue(field.GetValue(value), field.FieldType, depth + 1);
        return fields;
    }

    private static void ExportArmorSets()
    {
        // Main.Initialize normally invokes this. The metadata bootstrap bypasses Main.Initialize.
        Call(TypeOf("Terraria.Initializers.WingStatsInitializer"), "Load");
        Type armor = TypeOf("Terraria.ID.ArmorIDs");
        var domainCounts = new Dictionary<string,object>();
        var omittedFields = new List<string>();
        int domains = 0, indexedFields = 0;
        using (var file = OpenFamily("armor-sets"))
            foreach (Type domain in armor.GetNestedTypes(BindingFlags.Public).OrderBy(type => type.Name))
            {
                FieldInfo countField = domain.GetField("Count", BindingFlags.Public | BindingFlags.Static);
                var names = new Dictionary<int,string>();
                foreach (FieldInfo field in domain.GetFields(BindingFlags.Public | BindingFlags.Static)
                    .Where(field => field.IsLiteral && field.FieldType.IsPrimitive).OrderBy(field => field.Name))
                {
                    object raw = field.GetRawConstantValue();
                    if (raw is bool) continue;
                    int id = Convert.ToInt32(raw, CultureInfo.InvariantCulture);
                    if (id >= 0 && !names.ContainsKey(id)) names[id] = field.Name;
                }
                int count = countField == null ? (names.Count == 0 ? 0 : names.Keys.Max() + 1)
                    : Convert.ToInt32(countField.GetValue(null), CultureInfo.InvariantCulture);
                string countSource = countField == null ? "public ID constants max + 1 (no Count field)"
                    : domain.FullName + ".Count";
                domainCounts[domain.Name] = Row("count", count, "source", countSource);
                Type sets = domain.GetNestedType("Sets", BindingFlags.Public | BindingFlags.NonPublic);
                var arrays = new List<KeyValuePair<string,Array>>();
                var scalars = new Dictionary<string,object>();
                if (sets != null)
                    foreach (FieldInfo field in sets.GetFields(BindingFlags.Public | BindingFlags.Static).OrderBy(field => field.Name))
                    {
                        if (field.Name == "Factory") continue;
                        if (field.FieldType.IsArray && field.FieldType.GetArrayRank() == 1 &&
                            CanArmorValue(field.FieldType.GetElementType(), 0))
                        {
                            Array values = field.GetValue(null) as Array;
                            if (values != null && values.Length >= count)
                            {
                                arrays.Add(new KeyValuePair<string,Array>(field.Name, values));
                                indexedFields++;
                            }
                            else omittedFields.Add(domain.Name + ".Sets." + field.Name + ": uninitialized or shorter than domain");
                        }
                        else if (CanArmorValue(field.FieldType, 0))
                            scalars[field.Name] = ArmorValue(field.GetValue(null), field.FieldType, 0);
                        else if (field.FieldType == typeof(List<int>))
                            scalars[field.Name] = ((List<int>)field.GetValue(null)).ToArray();
                        else omittedFields.Add(domain.Name + ".Sets." + field.Name + ": unsupported type " + field.FieldType.FullName);
                    }
                for (int id = 0; id < count; id++)
                {
                    var values = new Dictionary<string,object>();
                    foreach (var pair in arrays)
                        values[pair.Key] = ArmorValue(pair.Value.GetValue(id), pair.Value.GetType().GetElementType(), 0);
                    file.Write(Row("id", domain.Name + ":" + id, "domain", domain.Name, "numericId", id,
                        "internalName", names.ContainsKey(id) ? names[id] : null,
                        "domainCount", count, "domainCountSource", countSource,
                        "sets", values, "setScalars", scalars, "source", "Terraria.ID.ArmorIDs nested domain and Sets"));
                }
                domains++;
            }
        capabilities["armorSets"] = Row("available", true, "domains", domains, "indexedFieldCount", indexedFields,
            "domainCounts", domainCounts, "omittedFields", omittedFields,
            "source", "Terraria.ID.ArmorIDs nested Sets; WingStatsInitializer.Load");
    }

    private static void ExportTextureReferences(string dimensionsPath, Dictionary<string, Dictionary<int,string>> ids)
    {
        actualTextureKeys.Clear();
        var dimensions = new Dictionary<string,int[]>(StringComparer.OrdinalIgnoreCase);
        foreach (string line in File.ReadLines(dimensionsPath))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string,object>>(line);
            string key = Convert.ToString(row["id"], CultureInfo.InvariantCulture);
            if (string.IsNullOrEmpty(key) || actualTextureKeys.ContainsKey(key))
                throw new InvalidDataException("Duplicate or empty texture key ignoring case: " + key);
            int width = Convert.ToInt32(row["width"], CultureInfo.InvariantCulture);
            int height = Convert.ToInt32(row["height"], CultureInfo.InvariantCulture);
            if (width <= 0 || height <= 0) throw new InvalidDataException("Invalid texture dimensions for " + key);
            actualTextureKeys.Add(key, key);
            dimensions.Add(key, new[] {width, height});
        }
        Type mainType = TypeOf("Terraria.Main");
        Array itemAnimations = (Array)Field(mainType, "itemAnimations").GetValue(null);
        Type animationBase = TypeOf("Terraria.DataStructures.DrawAnimation");
        MethodInfo getFrame = animationBase.GetMethod("GetFrame", Any);
        if (getFrame == null || getFrame.GetParameters().Length != 2)
            throw new MissingMethodException("DrawAnimation.GetFrame(Texture2D, frameCounterOverride)");
        Type textureType = getFrame.GetParameters()[0].ParameterType;
        FieldInfo textureWidth = Field(textureType, "<Width>k__BackingField");
        FieldInfo textureHeight = Field(textureType, "<Height>k__BackingField");
        int[] itemCopy = (int[])Field(TypeOf("Terraria.ID.ItemID+Sets"), "TextureCopyLoad").GetValue(null);
        var missingAssets = new List<string>();
        var caseAdjusted = new List<object>();
        var counts = new Dictionary<string,object>();
        int aliases = 0;
        int animated = 0;
        using (var file = OpenFamily("texture-references"))
            foreach (string kind in new[] {"Item", "NPC", "Tile", "Wall"})
            {
                string idFamily = kind == "Tile" ? "TileID" : kind == "Wall" ? "WallID" : kind + "ID";
                int count = Convert.ToInt32(Field(TypeOf("Terraria.ID." + idFamily), "Count").GetValue(null), CultureInfo.InvariantCulture);
                string prefix = kind == "Tile" ? "Tiles_" : kind == "Wall" ? "Wall_" : kind + "_";
                int present = 0, absent = 0, excluded = 0;
                for (int id = 0; id < count; id++)
                {
                    string requested = null, actual = null, status;
                    int sourceId = id;
                    if (kind == "Wall" && id == 0)
                    {
                        status = "not-drawn-sentinel";
                        excluded++;
                    }
                    else
                    {
                        if (kind == "Item")
                        {
                            int hops = 0;
                            while (sourceId >= 0 && sourceId < itemCopy.Length && itemCopy[sourceId] != -1)
                            {
                                sourceId = itemCopy[sourceId];
                                if (++hops > itemCopy.Length || sourceId >= id || sourceId < 0)
                                    throw new InvalidDataException("Invalid ItemID.Sets.TextureCopyLoad chain for " + id);
                            }
                            if (sourceId >= itemCopy.Length) throw new InvalidDataException("Item texture copy outside domain: " + id);
                            if (sourceId != id) aliases++;
                        }
                        requested = prefix + sourceId;
                        if (actualTextureKeys.TryGetValue(requested, out actual))
                        {
                            status = "present";
                            present++;
                            if (!String.Equals(requested, actual, StringComparison.Ordinal))
                                caseAdjusted.Add(Row("id", kind + ":" + id, "requested", requested, "actual", actual));
                        }
                        else
                        {
                            status = "missing-source";
                            absent++;
                            missingAssets.Add(kind + ":" + id + " -> " + requested);
                        }
                    }
                    string name = ids.ContainsKey(idFamily) && ids[idFamily].ContainsKey(id) ? ids[idFamily][id] : null;
                    object animation = null, sourceRect = null;
                    if (kind == "Item" && status == "present")
                    {
                        int[] size = dimensions[actual];
                        int x = 0, y = 0, width = size[0], height = size[1];
                        object instance = id < itemAnimations.Length ? itemAnimations.GetValue(id) : null;
                        if (instance != null)
                        {
                            string animationType = instance.GetType().FullName;
                            if (animationType != "Terraria.DataStructures.DrawAnimationVertical" &&
                                animationType != "Terraria.DataStructures.DrawAnimationScryingOrb")
                                throw new InvalidDataException("Unknown official item animation for " + id + ": " + animationType);
                            int frames = Convert.ToInt32(Member(instance, "FrameCount"), CultureInfo.InvariantCulture);
                            int ticks = Convert.ToInt32(Member(instance, "TicksPerFrame"), CultureInfo.InvariantCulture);
                            if (frames <= 0 || ticks <= 0) throw new InvalidDataException("Invalid item animation timing for " + id);
                            object fakeTexture = FormatterServices.GetUninitializedObject(textureType);
                            textureWidth.SetValue(fakeTexture, size[0]);
                            textureHeight.SetValue(fakeTexture, size[1]);
                            GC.SuppressFinalize(fakeTexture);
                            object frame = getFrame.Invoke(instance, new[] { fakeTexture, (object)0 });
                            x = Convert.ToInt32(Member(frame, "X"), CultureInfo.InvariantCulture);
                            y = Convert.ToInt32(Member(frame, "Y"), CultureInfo.InvariantCulture);
                            width = Convert.ToInt32(Member(frame, "Width"), CultureInfo.InvariantCulture);
                            height = Convert.ToInt32(Member(frame, "Height"), CultureInfo.InvariantCulture);
                            bool vertical = animationType.EndsWith("DrawAnimationVertical", StringComparison.Ordinal);
                            animation = Row("type", animationType, "frameCount", frames, "ticksPerFrame", ticks,
                                "pingPong", vertical && Convert.ToBoolean(Member(instance, "PingPong"), CultureInfo.InvariantCulture),
                                "notActuallyAnimating", vertical && Convert.ToBoolean(Member(instance, "NotActuallyAnimating"), CultureInfo.InvariantCulture));
                            animated++;
                        }
                        if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > size[0] || y + height > size[1])
                            throw new InvalidDataException("Official item frame outside source texture for " + id + " -> " + actual);
                        sourceRect = new[] {x, y, width, height};
                    }
                    file.Write(Row("id", kind + ":" + id, "kind", kind, "numericId", id,
                        "internalName", name, "sourceId", sourceId, "requestedAssetId", requested,
                        "assetId", actual, "status", status, "animation", animation, "sourceRect", sourceRect,
                        "binding", kind == "Item" ? "AssetInitializer.LoadTextures and ItemID.Sets.TextureCopyLoad"
                            : kind == "Wall" && id == 0 ? "WallDrawing skips wall <= 0"
                            : "AssetInitializer.LoadTextures"));
                }
                counts[kind] = Row("domainCount", count, "present", present, "missing", absent, "excludedSentinel", excluded);
            }
        foreach (string failure in missingAssets.Take(40)) missing.Add("texture source: " + failure);
        if (missingAssets.Count > 40) missing.Add("texture source: " + missingAssets.Count + " total missing; see texture-references.ndjson");
        MethodInfo binder = TypeOf("Terraria.Initializers.AssetInitializer").GetMethod("LoadTextures", Any);
        if (binder == null) throw new MissingMethodException("Terraria.Initializers.AssetInitializer", "LoadTextures");
        byte[] binderIl = binder.GetMethodBody().GetILAsByteArray();
        string binderSha;
        using (var sha = SHA256.Create()) binderSha = BitConverter.ToString(sha.ComputeHash(binderIl)).Replace("-", "").ToLowerInvariant();
        capabilities["textureReferences"] = Row("available", true, "indexedAssets", actualTextureKeys.Count,
            "domains", counts, "itemTextureCopies", aliases, "caseAdjusted", caseAdjusted,
            "animatedItems", animated, "itemFrames", "Main.InitializeItemAnimations; DrawAnimation.GetFrame(fakeTexture, 0)",
            "missingCount", missingAssets.Count, "bindingMethodIlSha256", binderSha,
            "source", "AssetInitializer.LoadTextures and ItemID.Sets.TextureCopyLoad from game assembly; WallDrawing wall<=0 sentinel");
    }

    private static void ExportMountLayouts(Dictionary<int,string> names, string dimensionsPath)
    {
        Type main = TypeOf("Terraria.Main"), mount = TypeOf("Terraria.Mount");
        FieldInfo netMode = Field(main, "netMode"), dedicated = Field(main, "dedServ");
        object priorNetMode = netMode.GetValue(null), priorDedicated = dedicated.GetValue(null);
        Array mounts;
        try
        {
            netMode.SetValue(null, Convert.ChangeType(2, netMode.FieldType, CultureInfo.InvariantCulture));
            dedicated.SetValue(null, true);
            Call(mount, "Initialize");
            mounts = (Array)Field(mount, "mounts").GetValue(null);
        }
        finally
        {
            netMode.SetValue(null, priorNetMode);
            dedicated.SetValue(null, priorDedicated);
        }
        if (mounts == null) throw new InvalidDataException("Mount.Initialize returned no mount data");
        if (String.IsNullOrEmpty(dimensionsPath))
            throw new InvalidDataException("Mount layouts require the Content texture dimensions index");
        var dimensions = new Dictionary<string,int[]>(StringComparer.OrdinalIgnoreCase);
        foreach (string line in File.ReadLines(dimensionsPath))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string,object>>(line);
            string key = Convert.ToString(row["id"], CultureInfo.InvariantCulture);
            int width = Convert.ToInt32(row["width"], CultureInfo.InvariantCulture);
            int height = Convert.ToInt32(row["height"], CultureInfo.InvariantCulture);
            if (String.IsNullOrEmpty(key) || dimensions.ContainsKey(key) || width <= 0 || height <= 0)
                throw new InvalidDataException("Invalid or duplicate mount source dimensions: " + key);
            dimensions.Add(key, new[] { width, height });
        }
        var canonicalKeys = dimensions.Keys.ToDictionary(key => key, key => key, StringComparer.OrdinalIgnoreCase);
        FieldInfo mountArray = Field(mount, "mounts");
        var mountSnapshot = mount.GetFields(Any).Where(field => field.IsStatic && !field.IsLiteral && !field.IsInitOnly)
            .Select(field => new KeyValuePair<FieldInfo,object>(field, field.GetValue(null))).ToArray();
        var mountArrays = mountSnapshot.Where(pair => pair.Value is Array)
            .Select(pair => new KeyValuePair<Array,Array>((Array)pair.Value, (Array)((Array)pair.Value).Clone())).ToArray();
        Dictionary<string,int[]> shapes;
        string[] selected = TypeOf("Terraria.GameContent.TextureAssets").GetFields(Any)
            .Where(field => field.Name.IndexOf("Mount", StringComparison.Ordinal) >= 0 ||
                field.Name == "Extra" || field.Name == "GlowMask").Select(field => field.Name).ToArray();
        try
        {
            OfficialTextureBindings.Load(game, selected, dimensions, out shapes, () => {
                netMode.SetValue(null, Convert.ChangeType(0, netMode.FieldType, CultureInfo.InvariantCulture));
                dedicated.SetValue(null, false);
                Call(mount, "Initialize");
                WriteMountLayouts((Array)mountArray.GetValue(null), names, dimensions, canonicalKeys);
            });
        }
        finally
        {
            foreach (var pair in mountSnapshot) pair.Key.SetValue(null, pair.Value);
            foreach (var pair in mountArrays) Array.Copy(pair.Value, pair.Key, pair.Value.Length);
            netMode.SetValue(null, priorNetMode);
            dedicated.SetValue(null, priorDedicated);
        }
    }

    private static void WriteMountLayouts(Array mounts, Dictionary<int,string> names,
        IDictionary<string,int[]> dimensions, IDictionary<string,string> canonicalKeys)
    {
        if (mounts == null) throw new InvalidDataException("Client Mount.Initialize returned no mount data");
        int count = Convert.ToInt32(Field(TypeOf("Terraria.ID.MountID"), "Count").GetValue(null), CultureInfo.InvariantCulture);
        if (mounts.Length != count) throw new InvalidDataException("Mount.Initialize count differs from MountID.Count");
        int populated = 0, boundTextures = 0, textureless = 0;
        using (var file = OpenFamily("mount-layouts"))
            for (int id = 0; id < mounts.Length; id++)
            {
                object data = mounts.GetValue(id);
                if (data == null) throw new InvalidDataException("Mount.Initialize left an uninitialized entry: " + id);
                var fields = new Dictionary<string,object>();
                var textureSlots = new Dictionary<string,object>();
                if (data != null)
                {
                    populated++;
                    foreach (FieldInfo field in data.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance).OrderBy(f => f.Name))
                    {
                        if (CanExport(field.FieldType))
                            fields[field.Name] = GameValue(field.GetValue(data), field.FieldType);
                        else if (field.FieldType.IsGenericType && field.FieldType.GetGenericTypeDefinition().FullName == "ReLogic.Content.Asset`1")
                        {
                            object asset = field.GetValue(data);
                            object empty = field.FieldType.GetField("Empty", Any).GetValue(null);
                            bool assigned = asset != null && !Object.ReferenceEquals(asset, empty);
                            string name = asset == null ? null : Convert.ToString(Member(asset, "Name"), CultureInfo.InvariantCulture);
                            string assetId = null;
                            int[] size = new[] { 0, 0 };
                            if (assigned)
                            {
                                if (String.IsNullOrEmpty(name) || !name.StartsWith("Images/", StringComparison.OrdinalIgnoreCase) ||
                                    !canonicalKeys.TryGetValue(name.Substring("Images/".Length), out assetId) ||
                                    !dimensions.TryGetValue(assetId, out size))
                                    throw new InvalidDataException("Missing official mount texture: " + id + "/" + field.Name + " -> " + name);
                                boundTextures++;
                            }
                            textureSlots[field.Name] = Row("assigned", assigned, "name", name,
                                "assetId", assetId, "width", size[0], "height", size[1]);
                        }
                    }
                }
                bool hasTexture = textureSlots.Values.Cast<Dictionary<string,object>>().Any(slot => (bool)slot["assigned"]);
                int totalFrames = Convert.ToInt32(fields["totalFrames"], CultureInfo.InvariantCulture);
                Array offsets = Field(data.GetType(), "playerYOffsets").GetValue(data) as Array;
                if (totalFrames <= 0 || offsets == null || offsets.Length < totalFrames)
                    throw new InvalidDataException("Mount frame domain or playerYOffsets is incomplete: " + id);
                if (!hasTexture) textureless++;
                if (hasTexture && (Convert.ToInt32(fields["textureWidth"], CultureInfo.InvariantCulture) <= 0 ||
                    Convert.ToInt32(fields["textureHeight"], CultureInfo.InvariantCulture) <= 0))
                    throw new InvalidDataException("Mount has bound textures but invalid base dimensions: " + id);
                file.Write(Row("id", id, "internalName", names.ContainsKey(id) ? names[id] : null,
                    "initialized", data != null, "fields", fields, "textureSlots", textureSlots,
                    "source", "Mount.Initialize and Mount.mounts"));
            }
        capabilities["mountLayouts"] = Row("available", true, "entityCount", mounts.Length, "populated", populated,
            "source", "AssetInitializer.LoadTextures and Mount.Initialize with metadata-only loaded assets and Main.netMode=0",
            "textureDimensionsBound", true, "boundTextureSlots", boundTextures, "texturelessMounts", textureless);
    }

    private static void ExportSimpleNames(Dictionary<string, Dictionary<int,string>> ids)
    {
        Type lang = TypeOf("Terraria.Lang");
        foreach (var pair in new[] { new[] {"PrefixID", "prefixes"}, new[] {"BuffID", "buffs"} })
        {
            if (!ids.ContainsKey(pair[0])) continue;
            bool isPrefix = pair[0] == "PrefixID";
            int domainCount = Convert.ToInt32(Field(TypeOf("Terraria.ID." + pair[0]), "Count").GetValue(null), CultureInfo.InvariantCulture);
            var localized = new Dictionary<string, Dictionary<int,string>>();
            var descriptions = new Dictionary<string, Dictionary<int,string>>();
            foreach (string culture in new[] {"en-US", "zh-Hans"})
            {
                SetLanguage(culture, true);
                var names = new Dictionary<int,string>();
                var descriptionsForCulture = new Dictionary<int,string>();
                for (int id = 1; id < domainCount; id++)
                {
                    try
                    {
                        if (!isPrefix)
                        {
                            names[id] = Convert.ToString(Call(lang, "GetBuffName", id), CultureInfo.InvariantCulture);
                            descriptionsForCulture[id] = Convert.ToString(Call(lang, "GetBuffDescription", id), CultureInfo.InvariantCulture);
                        }
                        else
                        {
                            Array prefixes = (Array)Field(lang, "prefix").GetValue(null);
                            object text = id < prefixes.Length ? prefixes.GetValue(id) : null;
                            names[id] = text == null ? "" : Convert.ToString(Member(text, "Value"), CultureInfo.InvariantCulture);
                        }
                    }
                    catch (TargetInvocationException ex)
                    {
                        throw new InvalidOperationException(pair[1] + " localization failed for id=" + id + " culture=" + culture, ex.InnerException ?? ex);
                    }
                }
                localized[culture] = names;
                descriptions[culture] = descriptionsForCulture;
            }
            var prefixPools = isPrefix ? TypeOf("Terraria.GameContent.Prefixes.PrefixLegacy+Prefixes")
                .GetFields(BindingFlags.Public | BindingFlags.Static).Where(field => field.FieldType == typeof(int[]))
                .ToDictionary(field => field.Name, field => (int[])field.GetValue(null), StringComparer.Ordinal) : null;
            MethodInfo prefixMethod = isPrefix ? FieldlessPrefixStatsMethod() : null;
            IDictionary itemSamples = isPrefix ? (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "ItemsByType").GetValue(null) : null;
            object prefixSample = isPrefix ? itemSamples[1] : null;
            object prefixPlayer = isPrefix ? Activator.CreateInstance(TypeOf("Terraria.Player")) : null;
            object prefixItem = isPrefix ? Activator.CreateInstance(TypeOf("Terraria.Item")) : null;
            FieldInfo[] benefitFields = isPrefix ? TypeOf("Terraria.Player").GetFields(Any)
                .Where(field => !field.IsStatic && !field.IsInitOnly && field.FieldType.IsPrimitive &&
                    field.FieldType != typeof(bool) && field.FieldType != typeof(char) &&
                    field.FieldType != typeof(IntPtr) && field.FieldType != typeof(UIntPtr)).ToArray() : null;
            bool[] reducedNatural = isPrefix ? (bool[])Field(TypeOf("Terraria.ID.PrefixID+Sets"), "ReducedNaturalChance").GetValue(null) : null;
            var mainFlags = isPrefix ? null : TypeOf("Terraria.Main").GetFields(BindingFlags.Public | BindingFlags.Static)
                .Where(field => field.FieldType == typeof(bool[]) && ((bool[])field.GetValue(null)).Length == domainCount)
                .OrderBy(field => field.Name).ToArray();
            var buffSets = isPrefix ? null : TypeOf("Terraria.ID.BuffID+Sets").GetFields(BindingFlags.Public | BindingFlags.Static)
                .Where(field => field.FieldType.IsArray && field.FieldType.GetArrayRank() == 1 &&
                    CanExport(field.FieldType.GetElementType()) && ((Array)field.GetValue(null)).Length == domainCount)
                .OrderBy(field => field.Name).ToArray();
            IDictionary textHandlers = isPrefix ? null : (IDictionary)Field(TypeOf("Terraria.ID.BuffID+Sets"), "BuffTextHandlers").GetValue(null);
            using (var file = OpenFamily(pair[1]))
            {
                for (int id = 1; id < domainCount; id++)
                {
                    var row = Row("id", id, "internalName", ids[pair[0]].ContainsKey(id) ? ids[pair[0]][id] : null,
                        "name", Row("en-US", localized["en-US"][id], "zh-Hans", localized["zh-Hans"][id]));
                    if (isPrefix)
                    {
                        float value;
                        row["stats"] = PrefixStats(prefixSample, prefixMethod, id, out value);
                        row["valueMultiplier"] = value;
                        row["pools"] = prefixPools.Where(pool => pool.Value.Contains(id)).Select(pool => pool.Key)
                            .OrderBy(name => name, StringComparer.Ordinal).ToArray();
                        row["reducedNaturalChance"] = reducedNatural[id];
                        row["accessoryEffects"] = PrefixAccessoryEffects(prefixPlayer, prefixItem, benefitFields, id);
                    }
                    else
                    {
                        row["description"] = Row("en-US", descriptions["en-US"][id], "zh-Hans", descriptions["zh-Hans"][id]);
                        var flags = new Dictionary<string,object>();
                        foreach (FieldInfo flag in mainFlags) flags[flag.Name] = ((bool[])flag.GetValue(null))[id];
                        row["mainFlags"] = flags;
                        var sets = new Dictionary<string,object>();
                        foreach (FieldInfo set in buffSets)
                            sets[set.Name] = GameValue(((Array)set.GetValue(null)).GetValue(id), set.FieldType.GetElementType());
                        row["sets"] = sets;
                        row["dynamicTextHandler"] = textHandlers.Contains(id) ? textHandlers[id].GetType().Name : null;
                    }
                    file.Write(row);
                }
            }
            if (isPrefix)
                capabilities["prefixes"] = Row("available", true, "count", domainCount - 1,
                    "poolNames", prefixPools.Keys.OrderBy(name => name, StringComparer.Ordinal).ToArray(),
                    "source", "PrefixLegacy.Prefixes, Item.TryGetPrefixStatMultipliersForItem, Player.GrantPrefixBenefits");
            else
                capabilities["buffs"] = Row("available", true, "count", domainCount - 1,
                    "mainFlagFields", mainFlags.Select(field => field.Name).ToArray(),
                    "setFields", buffSets.Select(field => field.Name).ToArray(),
                    "dynamicTextHandlers", textHandlers.Count,
                    "source", "Lang.GetBuffName/GetBuffDescription, Main flags, BuffID.Sets");
        }
        ExportBestiary(ids.ContainsKey("NPCID") ? ids["NPCID"] : new Dictionary<int,string>());
        ExportNpcFrames(ids.ContainsKey("NPCID") ? ids["NPCID"] : new Dictionary<int,string>());
        ExportPlayerLayouts();
    }

    private static void ExportBestiary(Dictionary<int,string> npcNames)
    {
        Type dbType = TypeOf("Terraria.GameContent.Bestiary.BestiaryDatabase");
        object database = Activator.CreateInstance(dbType);
        object populator = Activator.CreateInstance(TypeOf("Terraria.GameContent.Bestiary.BestiaryDatabaseNPCsPopulator"));
        Call(populator, "Populate", database);
        Call(TypeOf("Terraria.ID.ContentSamples"), "RebuildBestiarySortingIDsByBestiaryDatabaseContents", database);
        IDictionary credits = (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "NpcBestiaryCreditIdsByNpcNetIds").GetValue(null);
        IDictionary sorting = (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "NpcBestiarySortingId").GetValue(null);
        IDictionary rarity = (IDictionary)Field(TypeOf("Terraria.ID.ContentSamples"), "NpcBestiaryRarityStars").GetValue(null);
        var goldIds = ((IEnumerable)Field(TypeOf("Terraria.ID.NPCID+Sets"), "GoldCrittersCollection").GetValue(null))
            .Cast<object>().Select(id => Convert.ToInt32(id, CultureInfo.InvariantCulture)).OrderBy(id => id).ToArray();
        var goldPersistentIds = goldIds.Select(id => Convert.ToString(credits[id], CultureInfo.InvariantCulture)).ToArray();
        var providerCounts = new SortedDictionary<string,int>(StringComparer.Ordinal);
        var unknownProviders = new SortedSet<string>(StringComparer.Ordinal);
        var worldConditionalIds = new List<int>();
        var entries = new Dictionary<int,object>();
        foreach (object entry in (IEnumerable)Property(dbType, "Entries").GetValue(database, null))
        {
            int? id = null;
            string key = null;
            foreach (object info in (IEnumerable)Property(entry.GetType(), "Info").GetValue(entry, null))
            {
                if (info.GetType().Name == "NPCNetIdBestiaryInfoElement") id = Convert.ToInt32(Member(info, "NetId"), CultureInfo.InvariantCulture);
                if (info.GetType().Name == "NamePlateInfoElement") key = Convert.ToString(Member(info, "_key"), CultureInfo.InvariantCulture);
            }
            if (id.HasValue)
            {
                object provider = Member(entry, "UIInfoProvider");
                string providerName = provider == null ? "<null>" : provider.GetType().Name;
                providerCounts[providerName] = providerCounts.ContainsKey(providerName) ? providerCounts[providerName] + 1 : 1;
                var trackers = new SortedSet<string>(StringComparer.Ordinal);
                var requirements = new List<object>();
                bool worldConditional = false;
                object unlockRule = BestiaryUnlockRule(provider, goldIds, goldPersistentIds,
                    trackers, requirements, unknownProviders, ref worldConditional, 0);
                if (worldConditional) worldConditionalIds.Add(id.Value);
                entries[id.Value] = Row("nameKey", key, "provider", providerName,
                    "npcType", BestiaryNpcType(provider), "trackerTypes", trackers.ToArray(),
                    "unlockRequirements", requirements, "unlockRule", unlockRule,
                    "worldConditional", worldConditional);
            }
        }
        var namesByCulture = new Dictionary<string, Dictionary<int,string>>();
        foreach (string culture in new[] {"en-US", "zh-Hans"})
        {
            SetLanguage(culture, true);
            var names = new Dictionary<int,string>();
            foreach (int id in entries.Keys)
            {
                string key = Convert.ToString(((Dictionary<string,object>)entries[id])["nameKey"], CultureInfo.InvariantCulture);
                string value = null;
                if (!string.IsNullOrEmpty(key))
                {
                    object text = Call(TypeOf("Terraria.Localization.Language"), "GetText", key);
                    value = Convert.ToString(Member(text, "Value"), CultureInfo.InvariantCulture);
                }
                if (string.IsNullOrEmpty(value)) value = Convert.ToString(Call(TypeOf("Terraria.Lang"), "GetNPCNameValue", id), CultureInfo.InvariantCulture);
                names[id] = value;
            }
            namesByCulture[culture] = names;
        }
        using (var file = OpenFamily("bestiary"))
            foreach (var pair in entries.OrderBy(e => e.Key))
            {
                var info = (Dictionary<string,object>)pair.Value;
                file.Write(Row("id", pair.Key, "internalName", npcNames.ContainsKey(pair.Key) ? npcNames[pair.Key] : null,
                    "persistentNpcId", credits.Contains(pair.Key) ? credits[pair.Key] : null,
                    "sortingId", sorting.Contains(pair.Key) ? sorting[pair.Key] : null,
                    "rarityStars", rarity.Contains(pair.Key) ? rarity[pair.Key] : null,
                    "nameKey", info["nameKey"], "provider", info["provider"],
                    "npcType", info["npcType"], "trackerTypes", info["trackerTypes"],
                    "unlockRequirements", info["unlockRequirements"], "unlockRule", info["unlockRule"],
                    "worldConditional", info["worldConditional"],
                    "name", Row("en-US", namesByCulture["en-US"][pair.Key], "zh-Hans", namesByCulture["zh-Hans"][pair.Key])));
            }
        capabilities["bestiary"] = Row("available", true, "source", "BestiaryDatabaseNPCsPopulator.Populate, ContentSamples sorting/credit IDs",
            "unlockRequirements", unknownProviders.Count == 0, "providerCounts", providerCounts,
            "unknownProviders", unknownProviders.ToArray(), "worldConditionalIds", worldConditionalIds,
            "worldConditionalSource", "SalamanderShellyDadUICollectionInfoProvider uses NPC.cavernMonsterType for the current world");
        foreach (string provider in unknownProviders) missing.Add("bestiary unsupported unlock provider: " + provider);
        if (worldConditionalIds.Count != 0)
            warnings.Add("bestiary Salamander/Shelly/Crawdad unlock state also depends on the current world's NPC.cavernMonsterType");
    }

    private static string BestiaryNpcType(object provider)
    {
        if (provider == null) return "unknown";
        switch (provider.GetType().Name)
        {
            case "CommonEnemyUICollectionInfoProvider":
            case "SalamanderShellyDadUICollectionInfoProvider": return "enemy";
            case "CritterUICollectionInfoProvider":
            case "GoldCritterUICollectionInfoProvider": return "critter";
            case "TownNPCUICollectionInfoProvider": return "town";
            case "HighestOfMultipleUICollectionInfoProvider":
                Array children = (Array)Member(provider, "_providers");
                int main = Convert.ToInt32(Member(provider, "_mainProviderIndex"), CultureInfo.InvariantCulture);
                return main >= 0 && main < children.Length ? BestiaryNpcType(children.GetValue(main)) : "unknown";
            default: return "unknown";
        }
    }

    private static object BestiaryUnlockRule(object provider, int[] goldIds, string[] goldPersistentIds,
        SortedSet<string> trackers, List<object> requirements, SortedSet<string> unknownProviders,
        ref bool worldConditional, int depth)
    {
        if (depth > 8) throw new InvalidDataException("Bestiary provider nesting exceeds 8");
        string kind = provider == null ? "<null>" : provider.GetType().Name;
        string id;
        switch (kind)
        {
            case "CommonEnemyUICollectionInfoProvider":
            case "SalamanderShellyDadUICollectionInfoProvider":
                id = Convert.ToString(Member(provider, "_persistentIdentifierToCheck"), CultureInfo.InvariantCulture);
                bool quick = kind == "CommonEnemyUICollectionInfoProvider" && Convert.ToBoolean(Member(provider, "_quickUnlock"), CultureInfo.InvariantCulture);
                int full = Convert.ToInt32(Member(provider, "_killCountNeededToFullyUnlock"), CultureInfo.InvariantCulture);
                trackers.Add("Kills");
                requirements.Add(Row("tracker", "Kills", "persistentNpcId", id, "count", quick ? 1 : full));
                if (kind == "SalamanderShellyDadUICollectionInfoProvider") worldConditional = true;
                return Row("kind", kind == "SalamanderShellyDadUICollectionInfoProvider" ? "world-conditional-kills" : "kills",
                    "tracker", "Kills", "persistentNpcId", id, "quickUnlock", quick,
                    "killCountNeededToFullyUnlock", full,
                    "thresholds", Row("portrait", 1, "stats", full / 5, "drops", full / 2, "dropRates", full),
                    "worldCondition", kind == "SalamanderShellyDadUICollectionInfoProvider"
                        ? "If absent from current world, state is minimum kill-unlock state across NPC.cavernMonsterType" : null);
            case "CritterUICollectionInfoProvider":
            case "TownNPCUICollectionInfoProvider":
                id = Convert.ToString(Member(provider, "_persistentIdentifierToCheck"), CultureInfo.InvariantCulture);
                string tracker = kind == "CritterUICollectionInfoProvider" ? "Sights" : "Chats";
                trackers.Add(tracker);
                requirements.Add(Row("tracker", tracker, "persistentNpcId", id, "count", 1));
                return Row("kind", tracker == "Sights" ? "sighting" : "chat",
                    "tracker", tracker, "persistentNpcId", id, "unlockedState", 4);
            case "GoldCritterUICollectionInfoProvider":
                string[] normals = (string[])Member(provider, "_normalCritterPersistentId");
                string gold = Convert.ToString(Member(provider, "_goldCritterPersistentId"), CultureInfo.InvariantCulture);
                trackers.Add("Sights");
                foreach (string persistent in normals.Concat(new[] { gold }).Concat(goldPersistentIds).Distinct(StringComparer.Ordinal))
                    requirements.Add(Row("tracker", "Sights", "persistentNpcId", persistent, "count", 1));
                return Row("kind", "gold-critter", "tracker", "Sights", "normalCritterPersistentIds", normals,
                    "goldCritterPersistentId", gold, "globalGoldNpcNetIds", goldIds,
                    "globalGoldPersistentIds", goldPersistentIds,
                    "condition", "any own-or-normal sighting AND any global gold-critter sighting");
            case "HighestOfMultipleUICollectionInfoProvider":
                Array children = (Array)Member(provider, "_providers");
                int main = Convert.ToInt32(Member(provider, "_mainProviderIndex"), CultureInfo.InvariantCulture);
                if (main < 0 || main >= children.Length) throw new InvalidDataException("Invalid Bestiary main provider index");
                var childRules = new List<object>();
                foreach (object child in children)
                    childRules.Add(BestiaryUnlockRule(child, goldIds, goldPersistentIds,
                        trackers, requirements, unknownProviders, ref worldConditional, depth + 1));
                return Row("kind", "highest-of-multiple", "mainProviderIndex", main, "children", childRules,
                    "condition", "maximum child unlock state; main child supplies other display data");
            default:
                unknownProviders.Add(kind);
                return Row("kind", "unknown", "provider", kind);
        }
    }

    private static void ExportNpcFrames(Dictionary<int,string> names)
    {
        Type main = TypeOf("Terraria.Main"), sets = TypeOf("Terraria.ID.NPCID+Sets");
        Array frameCounts = (Array)Field(main, "npcFrameCount").GetValue(null);
        IDictionary offsets = (IDictionary)Field(sets, "NPCBestiaryDrawOffset").GetValue(null);
        int customAssets = 0, missingAssets = 0, caseAdjusted = 0;
        using (var file = OpenFamily("npc-frames"))
            foreach (var pair in names.OrderBy(e => e.Key))
            {
                if (pair.Value == "NegativeIDCount" || pair.Value == "Count") continue;
                int id = pair.Key;
                int npcType = Convert.ToInt32(Call(TypeOf("Terraria.ID.NPCID"), "FromNetId", id), CultureInfo.InvariantCulture);
                if (npcType < 0 || npcType >= frameCounts.Length)
                    throw new InvalidDataException("NPCID.FromNetId outside Main.npcFrameCount: " + id + " -> " + npcType);
                object offset = offsets.Contains(id) ? offsets[id] : null;
                var draw = new Dictionary<string,object>();
                if (offset != null)
                    foreach (FieldInfo field in offset.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance).OrderBy(f => f.Name))
                        if (CanExport(field.FieldType)) draw[field.Name] = GameValue(field.GetValue(offset), field.FieldType);
                string custom = offset == null ? null : Convert.ToString(Member(offset, "CustomTexturePath"), CultureInfo.InvariantCulture);
                if (!string.IsNullOrEmpty(custom)) customAssets++;
                string requested = string.IsNullOrEmpty(custom) ? "NPC_" + npcType : custom.Replace('\\', '/');
                if (requested.StartsWith("Images/", StringComparison.OrdinalIgnoreCase)) requested = requested.Substring("Images/".Length);
                string actual = null;
                if (actualTextureKeys.Count != 0)
                {
                    if (!actualTextureKeys.TryGetValue(requested, out actual))
                    {
                        missingAssets++;
                        missing.Add("npc frame texture: " + id + " -> " + requested);
                    }
                    else if (!String.Equals(requested, actual, StringComparison.Ordinal)) caseAdjusted++;
                }
                file.Write(Row("id", id, "internalName", pair.Value,
                    "npcType", npcType, "frameCount", frameCounts.GetValue(npcType),
                    "requestedAssetId", requested, "assetId", actual,
                    "textureStatus", actualTextureKeys.Count == 0 ? "not-indexed" : actual == null ? "missing-source" : "present",
                    "bestiaryDraw", draw));
            }
        capabilities["npcFrames"] = Row("available", true, "source", "Main.npcFrameCount and NPCID.Sets.NPCBestiaryDrawOffset",
            "renderedFrames", false, "textureIndexAvailable", actualTextureKeys.Count != 0,
            "customAssets", customAssets, "missingAssets", missingAssets, "caseAdjusted", caseAdjusted);
        warnings.Add("NPC frame counts and draw offsets do not prescribe every client animation state");
    }

    private static void ExportDyeShaders()
    {
        Type shaders = TypeOf("Terraria.Graphics.Shaders.GameShaders");
        FieldInfo armorField = Field(shaders, "Armor"), hairField = Field(shaders, "Hair");
        int armorCount = 0, hairCount = 0, delegateCount = 0;
        using (var file = OpenFamily("dye-shaders"))
                foreach (string kind in new[] { "armor", "hair" })
                {
                    object registry = kind == "armor" ? armorField.GetValue(null) : hairField.GetValue(null);
                    IDictionary lookup = (IDictionary)Field(registry.GetType(), "_shaderLookupDictionary").GetValue(registry);
                    foreach (int itemId in lookup.Keys.Cast<object>()
                        .Select(key => Convert.ToInt32(key, CultureInfo.InvariantCulture)).OrderBy(id => id))
                    {
                        int shaderId = Convert.ToInt32(lookup[itemId], CultureInfo.InvariantCulture);
                        object shader = Call(registry, "GetShaderFromItemId", itemId);
                        if (shader == null || shaderId <= 0) throw new InvalidDataException("Dye shader lookup is incomplete for " + kind + ":" + itemId);
                        Type shaderType = shader.GetType();
                        object processor = shaderType.Name == "LegacyHairShaderData" ? Member(shader, "_colorProcessor") : null;
                        MethodInfo method = processor == null ? null : ((Delegate)processor).Method;
                        string methodHash = null;
                        if (method != null)
                        {
                            MethodBody body = method.GetMethodBody();
                            if (body == null) throw new InvalidDataException("Legacy hair delegate lacks IL: " + itemId);
                            using (var sha = SHA256.Create())
                                methodHash = BitConverter.ToString(sha.ComputeHash(body.GetILAsByteArray())).Replace("-", "").ToLowerInvariant();
                            delegateCount++;
                        }
                        file.Write(Row("id", kind + ":" + itemId, "kind", kind, "itemId", itemId,
                            "shaderId", shaderId, "shaderClass", shaderType.Name,
                            "pass", Field(TypeOf("Terraria.Graphics.Shaders.ShaderData"), "_passName").GetValue(shader),
                            "color", ShaderVector3(InheritedField(shader, "_uColor")),
                            "secondaryColor", ShaderVector3(InheritedField(shader, "_uSecondaryColor")),
                            "saturation", InheritedField(shader, "_uSaturation"), "opacity", InheritedField(shader, "_uOpacity"),
                            "delegateMethod", method == null ? null : method.Name,
                            "delegateMethodIlSha256", methodHash));
                        if (kind == "armor") armorCount++; else hairCount++;
                    }
                }
        capabilities["dyeShaders"] = Row("available", true, "armorCount", armorCount, "hairCount", hairCount,
            "legacyHairDelegateCount", delegateCount, "imageBindings", false,
            "source", "DyeInitializer.LoadArmorDyes/LoadHairDyes and GameShaders runtime registries in dedicated mode");
        warnings.Add("dye shader image bindings are disabled in dedicated mode; dynamic hair delegates and GPU pass behavior remain client runtime effects");
    }

    private static void InitializeDyeRegistries()
    {
        FieldInfo dedicated = Field(TypeOf("Terraria.Main"), "dedServ");
        Type shaders = TypeOf("Terraria.Graphics.Shaders.GameShaders");
        object armor = Field(shaders, "Armor").GetValue(null), hair = Field(shaders, "Hair").GetValue(null);
        if (((IDictionary)Field(armor.GetType(), "_shaderLookupDictionary").GetValue(armor)).Count != 0 ||
            ((IDictionary)Field(hair.GetType(), "_shaderLookupDictionary").GetValue(hair)).Count != 0)
            throw new InvalidDataException("Dye registry was initialized before RuntimeExtractor bootstrap");
        object priorDedicated = dedicated.GetValue(null);
        try
        {
            dedicated.SetValue(null, true);
            Call(TypeOf("Terraria.Initializers.DyeInitializer"), "LoadArmorDyes");
            Call(TypeOf("Terraria.Initializers.DyeInitializer"), "LoadHairDyes");
        }
        finally { dedicated.SetValue(null, priorDedicated); }
    }

    private static object ShaderVector3(object vector)
    {
        return Row("r", Member(vector, "X"), "g", Member(vector, "Y"), "b", Member(vector, "Z"));
    }

    private static object InheritedField(object instance, string name)
    {
        for (Type type = instance.GetType(); type != null; type = type.BaseType)
        {
            FieldInfo field = type.GetField(name, Any | BindingFlags.DeclaredOnly);
            if (field != null) return field.GetValue(instance);
        }
        throw new MissingFieldException(instance.GetType().FullName, name);
    }

    private static MethodInfo FieldlessPrefixStatsMethod()
    {
        MethodInfo method = TypeOf("Terraria.Item").GetMethod("TryGetPrefixStatMultipliersForItem", BindingFlags.Public | BindingFlags.Instance);
        if (method == null || method.GetParameters().Length != 11)
            throw new MissingMethodException("Terraria.Item", "TryGetPrefixStatMultipliersForItem/11");
        return method;
    }

    private static bool InvokePrefixStats(object item, MethodInfo method, int prefix, object[] outputs)
    {
        object[] args = { prefix, 0f, 0f, 0f, 0f, 0f, 0f, 0, 0, 0, 0f };
        bool valid = (bool)method.Invoke(item, args);
        if (outputs != null) Array.Copy(args, 1, outputs, 0, 10);
        return valid;
    }

    private static Dictionary<string,object> PrefixStats(object item, MethodInfo method, int prefix, out float value)
    {
        var outputs = new object[10];
        InvokePrefixStats(item, method, prefix, outputs);
        var stats = new Dictionary<string,object>();
        string[] names = { "dmg", "kb", "spd", "size", "shtspd", "mcst", "crt", "tagdmg", "arpen" };
        for (int i = 0; i < names.Length; i++)
        {
            double number = Convert.ToDouble(outputs[i], CultureInfo.InvariantCulture);
            if (number != (i < 6 ? 1d : 0d)) stats[names[i]] = outputs[i];
        }
        value = Convert.ToSingle(outputs[9], CultureInfo.InvariantCulture);
        return stats;
    }

    private static Dictionary<string,object> PrefixAccessoryEffects(object player, object item, FieldInfo[] numericFields, int prefix)
    {
        Field(TypeOf("Terraria.Item"), "prefix").SetValue(item, (byte)prefix);
        var before = numericFields.Select(field => field.GetValue(player)).ToArray();
        Call(player, "GrantPrefixBenefits", item);
        var changes = new Dictionary<string,object>();
        for (int i = 0; i < numericFields.Length; i++)
        {
            object after = numericFields[i].GetValue(player);
            if (Object.Equals(before[i], after)) continue;
            double delta = Convert.ToDouble(after, CultureInfo.InvariantCulture) - Convert.ToDouble(before[i], CultureInfo.InvariantCulture);
            changes[numericFields[i].Name] = Math.Round(delta, 6);
            numericFields[i].SetValue(player, before[i]);
        }
        return changes;
    }

    private static void ExportPlayerLayouts()
    {
        Type player = TypeOf("Terraria.Player"), main = TypeOf("Terraria.Main");
        Type layers = game.GetType("Terraria.DataStructures.PlayerDrawLayers", false);
        object sample = ((Array)Field(main, "player").GetValue(null)).GetValue(0);
        using (var file = OpenFamily("player-layouts"))
        {
            foreach (string name in new[] {"headFrame", "bodyFrame", "legFrame"})
            {
                FieldInfo field = player.GetField(name, Any);
                if (field != null && CanExport(field.FieldType))
                    file.Write(Row("id", "Player." + name, "kind", "frame-field", "type", field.FieldType.FullName,
                        "initialValue", GameValue(field.GetValue(sample), field.FieldType)));
            }
            foreach (string name in new[] {"OffsetsPlayerOnhand", "OffsetsPlayerOffhand", "OffsetsPlayerHeadgear"})
            {
                FieldInfo offsets = Field(main, name);
                if (!CanExport(offsets.FieldType)) throw new InvalidDataException("官方人物偏移格式无法导出：" + name);
                Array values = offsets.GetValue(null) as Array;
                if (values == null || values.Length != 20) throw new InvalidDataException("官方人物偏移帧数无效：" + name);
                file.Write(Row("id", "Main." + name, "kind", "frame-offsets", "type", offsets.FieldType.FullName,
                    "value", GameValue(values, offsets.FieldType)));
            }
            if (layers != null)
                foreach (MethodInfo method in layers.GetMethods(BindingFlags.Public | BindingFlags.Static).Where(m => m.DeclaringType == layers && m.Name.StartsWith("Draw", StringComparison.Ordinal)).OrderBy(m => m.Name))
                    file.Write(Row("id", "PlayerDrawLayers." + method.Name, "kind", "renderer-method", "method", method.Name));
        }
        capabilities["playerLayouts"] = Row("available", true, "source", "Player frame fields, Main offsets and renderer method inventory",
            "composition", false);
    }

    private static void SetLanguage(string culture, bool mapInitialized)
    {
        Type manager = TypeOf("Terraria.Localization.LanguageManager");
        Call(Field(manager, "Instance").GetValue(null), "SetLanguage", culture);
        Call(TypeOf("Terraria.Lang"), "InitializeLegacyLocalization");
        if (mapInitialized) Call(TypeOf("Terraria.Lang"), "BuildMapAtlas");
    }

    private static Type TypeOf(string name) { return game.GetType(name, true); }
    private static FieldInfo Field(Type type, string name) { return type.GetField(name, Any) ?? throw new MissingFieldException(type.FullName, name); }
    private static PropertyInfo Property(Type type, string name) { return type.GetProperty(name, Any) ?? throw new MissingMemberException(type.FullName, name); }
    private static object Member(object instance, string name)
    {
        Type type = instance.GetType();
        var field = type.GetField(name, Any);
        return field != null ? field.GetValue(instance) : Property(type, name).GetValue(instance, null);
    }
    private static object Call(Type type, string name, params object[] args) { return Invoke(type, null, name, args); }
    private static object Call(object instance, string name, params object[] args) { return Invoke(instance.GetType(), instance, name, args); }
    private static object Invoke(Type type, object instance, string name, object[] args)
    {
        var methods = type.GetMethods(Any).Where(m => m.Name == name && m.GetParameters().Length == args.Length).ToArray();
        MethodInfo method = methods.FirstOrDefault(m => m.GetParameters().Select((p, i) => args[i] == null || p.ParameterType.IsInstanceOfType(args[i])).All(b => b));
        if (method == null) throw new MissingMethodException(type.FullName, name + "/" + args.Length);
        return method.Invoke(instance, args);
    }
    private static int ByteMember(object value, string name) { return Convert.ToInt32(Member(value, name), CultureInfo.InvariantCulture); }
    private static Dictionary<string, object> Row(params object[] values)
    {
        var row = new Dictionary<string, object>();
        for (int i = 0; i < values.Length; i += 2) row[(string)values[i]] = values[i + 1];
        return row;
    }
    private static string Sha256(string path)
    {
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    private static FamilyWriter OpenFamily(string name) { return new FamilyWriter(name); }

    private static long ProcStatusBytes(string key)
    {
        if (File.Exists("/proc/self/status"))
            foreach (string line in File.ReadLines("/proc/self/status"))
                if (line.StartsWith(key + ":", StringComparison.Ordinal))
                {
                    string[] pieces = line.Substring(key.Length + 1).Trim().Split(new[] {' ', '\t'}, StringSplitOptions.RemoveEmptyEntries);
                    return long.Parse(pieces[0], CultureInfo.InvariantCulture) * 1024;
                }
        using (var process = Process.GetCurrentProcess())
            return key == "VmHWM" ? process.PeakWorkingSet64 : process.WorkingSet64;
    }
    private static long PeakRss() { return ProcStatusBytes("VmHWM"); }
    private static long CgroupPeak()
    {
        string path = "/sys/fs/cgroup/memory.peak";
        return File.Exists(path) ? long.Parse(File.ReadAllText(path).Trim(), CultureInfo.InvariantCulture) : 0;
    }
    private static void RecordMemory(string stage)
    {
        long rss = ProcStatusBytes("VmRSS"), peak = PeakRss();
        memoryStages.Add(Row("stage", stage, "rssBytes", rss, "peakRssBytes", peak,
            "cgroupPeakBytes", CgroupPeak()));
        Console.Error.WriteLine("stage=" + stage + " rssBytes=" + rss + " peakRssBytes=" + peak);
    }

    private sealed class FamilyWriter : IDisposable
    {
        private readonly string name;
        private readonly StreamWriter writer;
        private int count;
        public FamilyWriter(string name)
        {
            this.name = name;
            writer = new StreamWriter(Path.Combine(output, name + ".ndjson"), false, new UTF8Encoding(false));
        }
        public void Write(object row) { writer.WriteLine(Json.Serialize(row)); count++; }
        public void Dispose()
        {
            writer.Dispose();
            string path = name + ".ndjson";
            families[name] = Row("path", path, "count", count, "sha256", Sha256(Path.Combine(output, path)));
        }
    }
}
