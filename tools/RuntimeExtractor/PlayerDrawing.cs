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

// CPU-only interception of the official renderer immediately before SpriteBatch submission.
internal static class PlayerDrawing
{
    internal sealed class Result
    {
        public string Path;
        public int Count;
        public string Sha256;
        public Dictionary<string, object> Capabilities = new Dictionary<string, object>();
        public List<string> Errors = new List<string>();
    }

    private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    private static readonly Dictionary<string, string> Arrays = new Dictionary<string, string> {
        {"Item", "Item_"}, {"Projectile", "Projectile_"}, {"Extra", "Extra_"}, {"GlowMask", "Glow_"}, {"Flames", "Flame_"}, {"BackPack", "BackPack_"},
        {"Wings", "Wings_"}, {"RudolphMount", "Rudolph_"}, {"PlayerHair", "Player_Hair_"}, {"PlayerHairAlt", "Player_HairAlt_"},
        {"ArmorHead", "Armor_Head_"}, {"FemaleBody", "Female_Body_"}, {"ArmorBody", "Armor_Body_"},
        {"ArmorBodyComposite", "Armor/Armor_"}, {"ArmorArm", "Armor_Arm_"}, {"ArmorLeg", "Armor_Legs_"},
        {"AccHandsOn", "Acc_HandsOn_"}, {"AccHandsOff", "Acc_HandsOff_"},
        {"AccHandsOnComposite", "Accessories/Acc_HandsOn_"}, {"AccHandsOffComposite", "Accessories/Acc_HandsOff_"},
        {"AccBack", "Acc_Back_"}, {"AccFront", "Acc_Front_"}, {"AccShoes", "Acc_Shoes_"},
        {"AccWaist", "Acc_Waist_"}, {"AccShield", "Acc_Shield_"}, {"AccNeck", "Acc_Neck_"},
        {"AccFace", "Acc_Face_"}, {"AccBalloon", "Acc_Balloon_"}, {"AccBeard", "Acc_Beard_"}
    };
    private static readonly Dictionary<string, string> Slots = new Dictionary<string, string> {
        {"head", "ArmorHead"}, {"body", "ArmorBody"}, {"coat", "ArmorBodyComposite"}, {"legs", "ArmorLeg"},
        {"handon", "AccHandsOn"}, {"handoff", "AccHandsOff"}, {"back", "AccBack"}, {"backpack", "AccBack"}, {"tail", "AccBack"},
        {"front", "AccFront"}, {"shoe", "AccShoes"}, {"waist", "AccWaist"},
        {"shield", "AccShield"}, {"neck", "AccNeck"}, {"face", "AccFace"}, {"faceHead", "AccFace"},
        {"faceFlower", "AccFace"}, {"faceMask", "AccFace"}, {"balloon", "AccBalloon"},
        {"balloonFront", "AccBalloon"}, {"beard", "AccBeard"}, {"wings", "Wings"}
    };

    internal static Result Export(Assembly game, string output, string textureDimensionsPath)
    {
        var result = new Result { Path = "player-draw-plans.ndjson" };
        string path = System.IO.Path.Combine(output, result.Path);
        var dimensions = new Dictionary<string, int[]>(StringComparer.OrdinalIgnoreCase);
        var sourceIds = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (string line in File.ReadLines(textureDimensionsPath))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string, object>>(line);
            string id = Convert.ToString(row["id"], CultureInfo.InvariantCulture);
            int width = Convert.ToInt32(row["width"], CultureInfo.InvariantCulture);
            int height = Convert.ToInt32(row["height"], CultureInfo.InvariantCulture);
            if (width < 1 || height < 1 || dimensions.ContainsKey(id)) throw new InvalidDataException("Invalid texture dimensions: " + id);
            dimensions.Add(id, new[] { width, height });
            sourceIds.Add(id, id);
        }
        var runtime = new Runtime(game, dimensions, sourceIds, result);
        try
        {
            runtime.Bind();
            using (var writer = new StreamWriter(path, false, new UTF8Encoding(false)))
                runtime.WritePlans(writer);
        }
        catch (Exception ex)
        {
            result.Errors.Add("player draw setup: " + Root(ex));
            if (!File.Exists(path)) File.WriteAllText(path, "", new UTF8Encoding(false));
        }
        finally { runtime.Restore(); }
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
            result.Sha256 = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        result.Capabilities["officialDrawData"] = result.Count > 0;
        result.Capabilities["gpuSubmissionSkipped"] = true;
        result.Capabilities["dynamicShadersAndRenderTargets"] = false;
        result.Capabilities["sampledProfilesOnly"] = true;
        result.Capabilities["compositionComplete"] = false;
        result.Capabilities["peakRssBytes"] = PeakRss();
        result.Capabilities["frames"] = runtime.FrameCount;
        result.Capabilities["hairStyles"] = runtime.HairCount;
        result.Capabilities["equipmentSlots"] = runtime.SlotCounts;
        result.Capabilities["equipmentSlotCoverage"] = runtime.SlotCoverage;
        result.Capabilities["skippedProfiles"] = runtime.Skipped;
        result.Capabilities["failureKinds"] = runtime.FailureKinds;
        result.Capabilities["requiredSourceOrFrameErrors"] = result.Errors.Count;
        result.Capabilities["sampledProfilesComplete"] = result.Count > 0 && result.Errors.Count == 0 && runtime.Skipped == 0;
        result.Capabilities["renderTargetProfiles"] = runtime.RenderTargetProfiles;
        result.Capabilities["mountProfiles"] = runtime.MountProfiles;
        result.Capabilities["optionalDynamicLimitations"] = new[] {
            "Arbitrary equipment/color combinations require client-side rendering adaptation.",
            "Shaders and requested render targets require live rendering; DrawData alone is not a finished bitmap."
        };
        return result;
    }

    private static string Root(Exception ex)
    {
        while (ex is TargetInvocationException && ex.InnerException != null) ex = ex.InnerException;
        return ex.GetType().FullName + ": " + ex.Message + " @ " + ex.StackTrace;
    }

    private static long PeakRss()
    {
        try
        {
            foreach (string line in File.ReadLines("/proc/self/status"))
                if (line.StartsWith("VmHWM:", StringComparison.Ordinal))
                    return long.Parse(line.Substring(6).Trim().Split(' ')[0], CultureInfo.InvariantCulture) * 1024;
        }
        catch (IOException) { }
        return Process.GetCurrentProcess().PeakWorkingSet64;
    }

    private sealed class Runtime
    {
        private readonly Assembly game;
        private readonly Dictionary<string, int[]> dimensions;
        private readonly Dictionary<string, string> sourceIds;
        private readonly Result result;
        private readonly Dictionary<object, string> textureIds = new Dictionary<object, string>();
        private readonly List<KeyValuePair<FieldInfo, object>> oldFields = new List<KeyValuePair<FieldInfo, object>>();
        private readonly Dictionary<string, object> slotCounts = new Dictionary<string, object>();
        private readonly Dictionary<string, object> slotCoverage = new Dictionary<string, object>();
        private readonly Dictionary<string, int> failureKinds = new Dictionary<string, int>();
        private readonly List<object> renderTargets = new List<object>();
        private readonly List<KeyValuePair<FieldInfo, object>> oldMountFields = new List<KeyValuePair<FieldInfo, object>>();
        private Type main, playerType, textureAssets, textureType, assetType, drawSetType, drawDataType;
        private FieldInfo mainInstance, mainPlayer, mainNpc, mainRand, mainNetMode, mainDedServ;
        private object oldInstance, oldRand, oldPlayers, oldNpcs, oldNetMode, oldDedServ;
        private FieldInfo mountsField;
        private object oldMounts, mount0Data;
        private object sentinel;
        private MethodInfo boring, drawLayers, transform;
        private int skipped, frameCount, hairCount, renderTargetProfiles, mountProfiles;
        internal int Skipped { get { return skipped; } }
        internal int FrameCount { get { return frameCount; } }
        internal int HairCount { get { return hairCount; } }
        internal Dictionary<string, object> SlotCounts { get { return slotCounts; } }
        internal Dictionary<string, object> SlotCoverage { get { return slotCoverage; } }
        internal Dictionary<string, int> FailureKinds { get { return failureKinds; } }
        internal int RenderTargetProfiles { get { return renderTargetProfiles; } }
        internal int MountProfiles { get { return mountProfiles; } }

        internal Runtime(Assembly game, Dictionary<string, int[]> dimensions, Dictionary<string, string> sourceIds, Result result)
        { this.game = game; this.dimensions = dimensions; this.sourceIds = sourceIds; this.result = result; }

        private Type T(string name) { return game.GetType(name, true); }
        private static FieldInfo F(Type type, string name)
        {
            for (Type current = type; current != null; current = current.BaseType)
            {
                FieldInfo found = current.GetField(name, Any | BindingFlags.DeclaredOnly);
                if (found != null) return found;
            }
            throw new MissingFieldException(type.FullName, name);
        }
        private static object Get(object value, string field) { return F(value.GetType(), field).GetValue(value); }
        private static void Set(object value, string field, object next)
        {
            FieldInfo target = F(value.GetType(), field);
            target.SetValue(value, next.GetType() == target.FieldType ? next : Convert.ChangeType(next, target.FieldType, CultureInfo.InvariantCulture));
        }

        internal void Bind()
        {
            main = T("Terraria.Main"); playerType = T("Terraria.Player");
            textureAssets = T("Terraria.GameContent.TextureAssets");
            drawSetType = T("Terraria.DataStructures.PlayerDrawSet");
            drawDataType = T("Terraria.DataStructures.DrawData");
            assetType = F(textureAssets, "PlayerHair").FieldType.GetElementType();
            textureType = assetType.GetGenericArguments()[0];
            boring = drawSetType.GetMethod("BoringSetup", Any);
            drawLayers = T("Terraria.Graphics.Renderers.LegacyPlayerRenderer").GetMethod("DrawPlayer_UseNormalLayers", Any);
            transform = T("Terraria.DataStructures.PlayerDrawLayers").GetMethod("DrawPlayer_TransformDrawData", Any);
            if (boring == null || drawLayers == null || transform == null) throw new MissingMethodException("official player draw boundary");
            mainInstance = F(main, "instance"); mainPlayer = F(main, "player"); mainNpc = F(main, "npc"); mainRand = F(main, "rand");
            mainNetMode = F(main, "netMode"); mainDedServ = F(main, "dedServ");
            oldInstance = mainInstance.GetValue(null); oldPlayers = mainPlayer.GetValue(null); oldNpcs = mainNpc.GetValue(null);
            oldRand = mainRand.GetValue(null); oldNetMode = mainNetMode.GetValue(null); oldDedServ = mainDedServ.GetValue(null);
            if (oldInstance == null) mainInstance.SetValue(null, FormatterServices.GetUninitializedObject(main));
            mainNetMode.SetValue(null, 0); mainDedServ.SetValue(null, false);
            mainRand.SetValue(null, Activator.CreateInstance(T("Terraria.Utilities.UnifiedRandom"), 0));
            Array npcs = oldNpcs == null ? Array.CreateInstance(T("Terraria.NPC"), 200) : (Array)((Array)oldNpcs).Clone();
            for (int i = 0; i < npcs.Length; i++) if (npcs.GetValue(i) == null) npcs.SetValue(Activator.CreateInstance(T("Terraria.NPC")), i);
            mainNpc.SetValue(null, npcs);
            sentinel = Fake("<unbound>", 1, 1);

            // These field-to-Content patterns are the 1.4.5.8 AssetInitializer.LoadTextures bindings.
            foreach (FieldInfo field in textureAssets.GetFields(Any).Where(f => f.IsStatic && f.FieldType.IsArray && f.FieldType.GetElementType() == assetType))
            {
                Array original = (Array)field.GetValue(null);
                if (field.Name == "Players")
                {
                    Array players = BindPlayerVariants();
                    oldFields.Add(new KeyValuePair<FieldInfo, object>(field, original)); field.SetValue(null, players);
                    continue;
                }
                if (original == null || original.Rank != 1) continue;
                Array bound = (Array)original.Clone();
                string prefix;
                Arrays.TryGetValue(field.Name, out prefix);
                for (int i = 0; i < bound.Length; i++)
                    bound.SetValue(prefix == null ? sentinel : Asset(prefix + (i + (field.Name == "PlayerHair" || field.Name == "PlayerHairAlt" ? 1 : 0))), i);
                oldFields.Add(new KeyValuePair<FieldInfo, object>(field, original)); field.SetValue(null, bound);
            }
            foreach (FieldInfo field in textureAssets.GetFields(Any).Where(f => f.IsStatic && f.FieldType == assetType))
            {
                object original = field.GetValue(null);
                oldFields.Add(new KeyValuePair<FieldInfo, object>(field, original));
                field.SetValue(null, Asset(field.Name));
            }
            Type targets = T("Terraria.GameContent.TextureAssets+RenderTargets");
            foreach (FieldInfo field in targets.GetFields(Any).Where(f => f.IsStatic && f.FieldType.BaseType != null && f.FieldType.BaseType.Name == "ARenderTargetContentByRequest"))
            {
                object original = field.GetValue(null);
                object target = FormatterServices.GetUninitializedObject(field.FieldType);
                oldFields.Add(new KeyValuePair<FieldInfo, object>(field, original));
                field.SetValue(null, target);
                renderTargets.Add(target);
            }
        }

        private Array BindPlayerVariants()
        {
            int variants = Convert.ToInt32(F(T("Terraria.ID.PlayerVariantID"), "Count").GetValue(null));
            int pieces = Convert.ToInt32(F(T("Terraria.ID.PlayerTextureID"), "Count").GetValue(null));
            Array players = Array.CreateInstance(assetType, variants, pieces);
            Action<int, int> copy = (to, from) => { for (int part = 0; part < pieces; part++) players.SetValue(players.GetValue(from, part), to, part); };
            Action<int, int[]> load = (skin, parts) => {
                foreach (int part in parts)
                {
                    string key = "Player_" + skin + "_" + part;
                    players.SetValue(dimensions.ContainsKey(key) ? Asset(key) : Fake("<unbound:" + key + ">", 1, 1), skin, part);
                }
            };
            // Exact copy/load order from official PlayerDataInitializer.Load (1.4.5.8).
            load(0, new[] { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15 });
            copy(4, 0); load(4, new[] { 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 });
            copy(1, 0); load(1, new[] { 4, 6, 8, 11, 12, 13 });
            copy(2, 0); load(2, new[] { 4, 6, 8, 11, 12, 13 });
            copy(3, 0); load(3, new[] { 4, 6, 8, 11, 12, 13, 14 });
            copy(8, 0); load(8, new[] { 4, 6, 8, 11, 12, 13, 14 });
            copy(5, 4); load(5, new[] { 4, 6, 8, 11, 12, 13 });
            copy(6, 4); load(6, new[] { 4, 6, 8, 11, 12, 13 });
            copy(7, 4); load(7, new[] { 4, 6, 8, 11, 12, 13, 14 });
            copy(9, 4); load(9, new[] { 4, 6, 8, 11, 12, 13 });
            copy(10, 0); load(10, new[] { 0, 2, 3, 5, 7, 9, 10 });
            foreach (int part in new[] { 1, 4, 6, 8, 11, 12, 13, 15 }) players.SetValue(players.GetValue(10, 2), 10, part);
            copy(11, 10); load(11, new[] { 3, 5, 7, 9, 10 });
            foreach (int part in new[] { 1, 2, 4, 6, 8, 11, 12, 13, 15 }) players.SetValue(players.GetValue(10, 2), 11, part);
            return players;
        }

        private object Asset(string id)
        {
            int[] size;
            return dimensions.TryGetValue(id, out size) ? Fake(sourceIds[id], size[0], size[1]) : sentinel;
        }

        private object Fake(string id, int width, int height)
        {
            object texture = FormatterServices.GetUninitializedObject(textureType);
            F(textureType, "<Width>k__BackingField").SetValue(texture, width);
            F(textureType, "<Height>k__BackingField").SetValue(texture, height);
            GC.SuppressFinalize(texture);
            object asset = Activator.CreateInstance(assetType, Any, null, new object[] { "Images/" + id }, CultureInfo.InvariantCulture);
            F(assetType, "<Value>k__BackingField").SetValue(asset, texture);
            FieldInfo state = F(assetType, "<State>k__BackingField");
            state.SetValue(asset, Enum.Parse(state.FieldType, "Loaded"));
            textureIds.Add(texture, id);
            return asset;
        }

        internal void Restore()
        {
            if (mount0Data != null) for (int i = oldMountFields.Count - 1; i >= 0; i--) oldMountFields[i].Key.SetValue(mount0Data, oldMountFields[i].Value);
            if (mountsField != null) mountsField.SetValue(null, oldMounts);
            for (int i = oldFields.Count - 1; i >= 0; i--) oldFields[i].Key.SetValue(null, oldFields[i].Value);
            if (mainInstance != null) mainInstance.SetValue(null, oldInstance);
            if (mainPlayer != null) mainPlayer.SetValue(null, oldPlayers);
            if (mainNpc != null) mainNpc.SetValue(null, oldNpcs);
            if (mainRand != null) mainRand.SetValue(null, oldRand);
            if (mainNetMode != null) mainNetMode.SetValue(null, oldNetMode);
            if (mainDedServ != null) mainDedServ.SetValue(null, oldDedServ);
        }

        internal void WritePlans(StreamWriter writer)
        {
            int[] baseSize = dimensions["Player_0_0"];
            object firstFrame = Get(Activator.CreateInstance(playerType), "bodyFrame");
            int rowHeight = Convert.ToInt32(F(firstFrame.GetType(), "Height").GetValue(firstFrame), CultureInfo.InvariantCulture);
            // PlayerFrame selects 20 standard rows; texture 1118/1120 includes trimmed borders.
            frameCount = Math.Min(20, (baseSize[1] + rowHeight - 1) / rowHeight);
            for (int frame = 0; frame < frameCount; frame++) Write(writer, "base", 0, frame, null, 0, true, 1, 1f);
            for (int hair = 0; hair < ((Array)F(textureAssets, "PlayerHair").GetValue(null)).Length; hair++)
            {
                if (!dimensions.ContainsKey("Player_Hair_" + (hair + 1))) continue;
                hairCount++;
                for (int frame = 0; frame < frameCount; frame++) Write(writer, "hair", hair, frame, null, 0, true, 1, 1f);
            }
            foreach (var slot in Slots)
            {
                Array textures = (Array)F(textureAssets, slot.Value).GetValue(null);
                string newTextureField = slot.Key == "body" ? "ArmorBodyComposite" :
                    slot.Key == "handon" ? "AccHandsOnComposite" :
                    slot.Key == "handoff" ? "AccHandsOffComposite" : null;
                Array newTextures = newTextureField == null ? null : (Array)F(textureAssets, newTextureField).GetValue(null);
                string armorSet = slot.Key == "body" ? "Body" : slot.Key == "handon" ? "HandOn" : slot.Key == "handoff" ? "HandOff" : null;
                bool[] newFraming = armorSet == null ? null : (bool[])F(T("Terraria.ID.ArmorIDs+" + armorSet + "+Sets"), "UsesNewFramingCode").GetValue(null);
                if (newTextures != null && (newTextures.Length != textures.Length || newFraming.Length != textures.Length))
                    throw new InvalidDataException("Official slot arrays disagree: " + slot.Key);
                Type slotType = F(playerType, slot.Key).FieldType;
                int maxId = slotType == typeof(sbyte) ? sbyte.MaxValue : slotType == typeof(byte) ? byte.MaxValue : slotType == typeof(short) ? short.MaxValue : int.MaxValue;
                int available = 0, sampled = 0, missingRequired = 0;
                var excludedNoSource = new List<int>();
                var excludedFieldRange = new List<int>();
                var missingRequiredIds = new List<int>();
                for (int id = 1; id < textures.Length; id++)
                {
                    if (id > maxId) { excludedFieldRange.Add(id); continue; }
                    bool usesNew = newFraming != null && newFraming[id];
                    object selected = (usesNew ? newTextures : textures).GetValue(id);
                    if (selected == sentinel)
                    {
                        if (newTextures != null && (usesNew ? textures : newTextures).GetValue(id) != sentinel)
                        {
                            missingRequired++;
                            missingRequiredIds.Add(id);
                            result.Errors.Add(slot.Key + "." + id + ": missing " + (usesNew ? newTextureField : slot.Value) + " texture required by ArmorIDs framing rule");
                        }
                        else excludedNoSource.Add(id);
                        continue;
                    }
                    available++;
                    int before = result.Count;
                    for (int frame = 0; frame < frameCount; frame++) Write(writer, slot.Key, id, frame, slot.Key, id, true, 1, 1f);
                    if (slot.Key == "body") for (int frame = 0; frame < frameCount; frame++) Write(writer, slot.Key, id, frame, slot.Key, id, false, 1, 1f);
                    if (result.Count - before == frameCount * (slot.Key == "body" ? 2 : 1)) sampled++;
                }
                slotCounts[slot.Key] = sampled;
                slotCoverage[slot.Key] = new {
                    declaredIds = textures.Length - 1, available, sampled,
                    excludedNoSourceIds = excludedNoSource, excludedFieldRangeIds = excludedFieldRange,
                    missingRequiredSourceIds = missingRequiredIds, missingRequiredSourceCount = missingRequired,
                    selection = newTextureField == null ? slot.Value : "ArmorIDs." + armorSet + ".Sets.UsesNewFramingCode ? " + newTextureField + " : " + slot.Value
                };
            }
            for (int frame = 0; frame < frameCount; frame++)
            {
                Write(writer, "base-female", 0, frame, null, 0, false, 1, 1f);
                Write(writer, "base-left", 0, frame, null, 0, true, -1, 1f);
                Write(writer, "base-inverted-gravity", 0, frame, null, 0, true, 1, -1f);
            }
            try
            {
                BindMount0();
                for (int mountFrame = 0; mountFrame < 12; mountFrame++)
                {
                    int before = result.Count;
                    Write(writer, "mount", 0, 0, null, 0, true, 1, 1f, mountFrame);
                    if (result.Count > before) mountProfiles++;
                }
            }
            catch (Exception ex) { result.Errors.Add("mount.0 setup: " + Root(ex)); }
        }

        private void BindMount0()
        {
            Type mount = T("Terraria.Mount");
            mountsField = F(mount, "mounts"); oldMounts = mountsField.GetValue(null);
            Array data = (Array)oldMounts;
            if (data == null || data.GetValue(0) == null)
            {
                mainNetMode.SetValue(null, 2); mainDedServ.SetValue(null, true);
                try { mount.GetMethod("Initialize", Any).Invoke(null, null); }
                finally { mainNetMode.SetValue(null, 0); mainDedServ.SetValue(null, false); }
                data = (Array)mountsField.GetValue(null);
            }
            mount0Data = data.GetValue(0);
            Array textures = (Array)F(textureAssets, "RudolphMount").GetValue(null);
            if (textures.GetValue(0) == sentinel || textures.GetValue(1) == sentinel || textures.GetValue(2) == sentinel)
                throw new InvalidDataException("Rudolph_0..2 source textures required for mount 0");
            SaveMount("backTexture", textures.GetValue(0));
            SaveMount("frontTexture", textures.GetValue(1));
            SaveMount("frontTextureExtra", textures.GetValue(2));
            SaveMount("textureWidth", dimensions["Rudolph_0"][0]);
            SaveMount("textureHeight", dimensions["Rudolph_0"][1]);
        }

        private void SaveMount(string field, object next)
        {
            FieldInfo target = F(mount0Data.GetType(), field);
            oldMountFields.Add(new KeyValuePair<FieldInfo, object>(target, target.GetValue(mount0Data)));
            target.SetValue(mount0Data, next);
        }

        private void Write(StreamWriter writer, string profile, int id, int frame, string slot, int slotId, bool male, int direction, float gravDir, int mountFrame = -1)
        {
            try
            {
                object player = Activator.CreateInstance(playerType);
                foreach (object target in renderTargets) target.GetType().GetMethod("Reset", Any).Invoke(target, null);
                Set(player, "hair", profile == "hair" ? id : 0);
                if (slot != null) Set(player, slot, slotId);
                Set(player, "direction", direction); Set(player, "gravDir", gravDir);
                PropertyInfo maleProperty = playerType.GetProperty("Male", Any);
                if (maleProperty != null && maleProperty.CanWrite) maleProperty.SetValue(player, male, null);
                object body = Get(player, "bodyFrame"); Type rectangle = body.GetType();
                int width = Convert.ToInt32(F(rectangle, "Width").GetValue(body), CultureInfo.InvariantCulture);
                int height = Convert.ToInt32(F(rectangle, "Height").GetValue(body), CultureInfo.InvariantCulture);
                object rect = Activator.CreateInstance(rectangle, 0, frame * height, width, height);
                Set(player, "bodyFrame", rect); Set(player, "legFrame", rect);
                if (mountFrame >= 0)
                {
                    object mount = Get(player, "mount");
                    // SetMount also spawns world dust and sends player state; its draw state for mount 0 is these fields.
                    F(mount.GetType(), "_active").SetValue(mount, true);
                    F(mount.GetType(), "_type").SetValue(mount, 0);
                    F(mount.GetType(), "_data").SetValue(mount, mount0Data);
                    F(mount.GetType(), "_frame").SetValue(mount, mountFrame);
                }
                Array players = Array.CreateInstance(playerType, 256); players.SetValue(player, 0); mainPlayer.SetValue(null, players);
                Type vector = boring.GetParameters()[4].ParameterType;
                object zero = Activator.CreateInstance(vector);
                object list = Activator.CreateInstance(typeof(List<>).MakeGenericType(drawDataType));
                object setup = Activator.CreateInstance(drawSetType);
                boring.Invoke(setup, new object[] { player, list, new List<int>(), new List<int>(), zero, 0f, 0f, zero, null });
                object[] byref = { setup }; drawLayers.Invoke(null, byref);
                transform.Invoke(null, byref);
                IList cache = (IList)list;
                var operations = new List<object>(cache.Count);
                for (int i = 0; i < cache.Count; i++) operations.Add(Operation(cache[i], i));
                bool renderTargetRequested = renderTargets.Any(target => Convert.ToBoolean(Get(target, "_wasRequested")));
                if (renderTargetRequested) renderTargetProfiles++;
                var row = new Dictionary<string, object> {
                    {"id", mountFrame >= 0 ? "mount.0.frame." + mountFrame : profile + "." + id + ".frame." + frame + (male ? "" : ".female")},
                    {"context", new { profile, id, frame, slot, slotId, male, direction, gravDir, hair = Get(player, "hair"), mountType = mountFrame >= 0 ? (int?)0 : null, mountFrame = mountFrame >= 0 ? (int?)mountFrame : null }},
                    {"anchor", new[] { 0, 0 }}, {"operations", operations}, {"renderTargetRequested", renderTargetRequested},
                    {"source", "LegacyPlayerRenderer.DrawPlayer_UseNormalLayers + PlayerDrawLayers.DrawPlayer_TransformDrawData"}
                };
                writer.WriteLine(Json.Serialize(row)); result.Count++;
            }
            catch (Exception ex)
            {
                skipped++;
                while (ex is TargetInvocationException && ex.InnerException != null) ex = ex.InnerException;
                string kind = ex.GetType().FullName + ": " + ex.Message + " @ " + (ex.StackTrace ?? "").Split('\n')[0].Trim();
                int count;
                failureKinds.TryGetValue(kind, out count);
                failureKinds[kind] = count + 1;
                if (count == 0 && result.Errors.Count < 40) result.Errors.Add(profile + "." + id + "." + frame + ": " + Root(ex));
            }
        }

        private object Operation(object data, int layer)
        {
            object texture = Get(data, "texture"); string id = null;
            if (texture == null || !textureIds.TryGetValue(texture, out id) || id.StartsWith("<unbound", StringComparison.Ordinal))
                throw new InvalidDataException("DrawData uses unbound texture " + (id ?? "<null>") + " at layer " + layer);
            int[] size = dimensions[id];
            object rect = Get(data, "sourceRect");
            int[] source = rect == null ? new[] { 0, 0, size[0], size[1] } : Rectangle(rect);
            if (source[0] < 0 || source[1] < 0 || source[0] >= size[0] || source[1] >= size[1] || source[2] < 0 || source[3] < 0)
                throw new InvalidDataException("DrawData source outside " + id + ": " + string.Join(",", source));
            int[] requested = (int[])source.Clone();
            source[2] = Math.Min(source[2], size[0] - source[0]);
            source[3] = Math.Min(source[3], size[1] - source[1]);
            object color = Get(data, "color"); Type colorType = color.GetType();
            object position = Get(data, "position"), origin = Get(data, "origin"), scale = Get(data, "scale");
            var operation = new Dictionary<string, object> {
                {"layer", layer}, {"assetId", id}, {"sourceRect", source},
                {"requestedSourceRect", requested.SequenceEqual(source) ? null : requested}, {"position", Vector(position)},
                {"destinationRect", Convert.ToBoolean(Get(data, "useDestinationRectangle")) ? Rectangle(Get(data, "destinationRectangle")) : null},
                {"origin", Vector(origin)}, {"scale", Vector(scale)}, {"rotation", Get(data, "rotation")},
                {"effect", Convert.ToInt32(Get(data, "effect"), CultureInfo.InvariantCulture)},
                {"color", new[] { Convert.ToInt32(colorType.GetProperty("R").GetValue(color, null)), Convert.ToInt32(colorType.GetProperty("G").GetValue(color, null)), Convert.ToInt32(colorType.GetProperty("B").GetValue(color, null)), Convert.ToInt32(colorType.GetProperty("A").GetValue(color, null)) }},
                {"shader", Get(data, "shader")}, {"ignorePlayerRotation", Get(data, "ignorePlayerRotation")}
            };
            if (source[2] == 0 || source[3] == 0) operation["zeroArea"] = true;
            return operation;
        }

        private static int[] Rectangle(object value) { Type t = value.GetType(); return new[] { (int)F(t, "X").GetValue(value), (int)F(t, "Y").GetValue(value), (int)F(t, "Width").GetValue(value), (int)F(t, "Height").GetValue(value) }; }
        private static float[] Vector(object value) { Type t = value.GetType(); return new[] { (float)F(t, "X").GetValue(value), (float)F(t, "Y").GetValue(value) }; }
    }
}
