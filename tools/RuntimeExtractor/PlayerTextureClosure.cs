using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

// A deliberately narrow proof for the public player texture set. Unknown game
// calls and changed official binders are errors, never permission to prune.
internal static class PlayerTextureClosure
{
    private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    private static readonly OpCode[] One = new OpCode[256], Two = new OpCode[256];
    private static readonly HashSet<string> BaseFamilies = new HashSet<string>(StringComparer.Ordinal) { "Item", "Tile", "Wall", "Npc" };
    private static readonly Dictionary<string, string> PlayerArmorDomains = new Dictionary<string, string>(StringComparer.Ordinal) {
        { "ArmorHead", "Head" }, { "ArmorBodyComposite", "Body" }, { "ArmorLeg", "Legs" },
        { "Wings", "Wing" }, { "AccHandsOnComposite", "HandOn" }, { "AccHandsOffComposite", "HandOff" },
        { "AccBack", "Back" }, { "AccFront", "Front" }, { "AccShoes", "Shoe" },
        { "AccWaist", "Waist" }, { "AccShield", "Shield" }, { "AccNeck", "Neck" },
        { "AccFace", "Face" }, { "AccBalloon", "Balloon" }, { "AccBeard", "Beard" }
    };
    private static readonly Dictionary<string, string[]> OfficialAmbientConcats = new Dictionary<string, string[]>(StringComparer.Ordinal) {
        { "AirBalloonSkyEntity", new[] { "72ebb97f57e0945d164c9e4c3b135e3c57ea6bff0cb7c229a9e6ce84357da272", "Images/Backgrounds/Ambience/AirBalloons_" } },
        { "BatsGroupSkyEntity", new[] { "d6bd68cf31e843b1671c913644c12e2266cf84eb8ffb4fd4975075959c70ca47", "Images/Backgrounds/Ambience/Bat" } },
        { "ButterfliesSkyEntity", new[] { "39807c0bc1ffde176eddab47d820fa41e8e4811e3a7159442fe6ab50904a8d6a", "Images/Backgrounds/Ambience/ButterflySwarm" } },
        { "CrimeraSkyEntity", new[] { "fd96900a71702833803843a5d16ee588d270cdae62a3ab1a9fc3145d26366ff0", "Images/Backgrounds/Ambience/Crimera" } },
        { "EOSSkyEntity", new[] { "19303f355f96ff8886867f2f4aeddff6af4da709160fcc97e53ee74c17023ae1", "Images/Backgrounds/Ambience/EOS" } },
        { "HellBatsGoupSkyEntity", new[] { "1b62c70e900737c0ac644b7b7aae4908084db436108489361c4733393a97305d", "Images/Backgrounds/Ambience/HellBat" } },
        { "PixiePosseSkyEntity", new[] { "51c592449b003e01a70e44b2a16f8df97fc4a649c475cfab3f3e30111d3402aa", "Images/Backgrounds/Ambience/PixiePosse" } }
    };
    private static readonly string[] RootTypes = {
        "Terraria.DataStructures.PlayerDrawLayers", "Terraria.DataStructures.PlayerDrawSet",
        "Terraria.Graphics.Renderers.LegacyPlayerRenderer", "Terraria.Mount",
        "Terraria.Initializers.DyeInitializer",
        "Terraria.GameContent.PlayerRainbowWingsTextureContent",
        "Terraria.GameContent.PlayerQueenSlimeMountTextureContent",
        "Terraria.GameContent.PlayerTitaniumStormBuffTextureContent"
    };

    static PlayerTextureClosure()
    {
        foreach (FieldInfo f in typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static))
        {
            OpCode op = (OpCode)f.GetValue(null);
            ushort value = unchecked((ushort)op.Value);
            if (value < 256) One[value] = op;
            else if ((value >> 8) == 0xfe) Two[value & 255] = op;
        }
    }

    internal sealed class Result
    {
        internal string SelectedPath = "public-texture-ids.ndjson";
        internal string BindingsPath = "player-texture-bindings.ndjson";
        internal string ProofPath = "player-texture-closure-proof.json";
        internal int Count, BindingCount, SourceCount;
        internal string Sha256, BindingsSha256;
        internal readonly List<string> Errors = new List<string>();
        internal readonly Dictionary<string, object> Capabilities = new Dictionary<string, object>();
    }

    private sealed class Instruction
    {
        internal int Offset;
        internal OpCode Op;
        internal object Value;
    }

    private sealed class State
    {
        internal readonly Assembly Game;
        internal readonly string Output;
        internal readonly Result Result;
        internal readonly Dictionary<string, string> Sources = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        internal readonly Dictionary<string, int[]> Dimensions = new Dictionary<string, int[]>(StringComparer.OrdinalIgnoreCase);
        internal readonly SortedSet<string> Selected = new SortedSet<string>(StringComparer.Ordinal);
        internal readonly SortedDictionary<string, object> Mappings = new SortedDictionary<string, object>(StringComparer.Ordinal);
        internal readonly SortedSet<string> Fields = new SortedSet<string>(StringComparer.Ordinal);
        internal readonly SortedSet<string> Scanned = new SortedSet<string>(StringComparer.Ordinal);
        internal readonly SortedSet<string> Literals = new SortedSet<string>(StringComparer.Ordinal);
        internal readonly List<string> Unresolved = new List<string>();
        internal readonly Dictionary<string, object> DomainEvidence = new Dictionary<string, object>();
        internal readonly Queue<MethodBase> Pending = new Queue<MethodBase>();
        internal readonly HashSet<string> Queued = new HashSet<string>(StringComparer.Ordinal);
        internal readonly Dictionary<string, string> Caller = new Dictionary<string, string>(StringComparer.Ordinal);
        internal readonly HashSet<string> Dispatched = new HashSet<string>(StringComparer.Ordinal);
        internal Type[] GameTypes;
        internal Type TextureAssets;
        internal State(Assembly game, string output, Result result) { Game = game; Output = output; Result = result; }
        internal void Error(string value) { if (!Unresolved.Contains(value)) Unresolved.Add(value); }
    }

    internal static Result Export(Assembly game, string output, string dimensionsPath)
    {
        var result = new Result();
        var state = new State(game, output, result);
        try
        {
            LoadSources(state, dimensionsPath);
            state.TextureAssets = RequiredType(game, "Terraria.GameContent.TextureAssets");
            ScanGame(state);
            SelectBaseTextures(state);
            BindPlayerFields(state);
            SelectDirectPaths(state);
            CheckLegacyArmorSlots(state);
            CheckDrawPlanSubset(state);
        }
        catch (Exception ex)
        {
            while (ex is TargetInvocationException && ex.InnerException != null) ex = ex.InnerException;
            state.Error("closure exception: " + ex.GetType().FullName + ": " + ex.Message + " @ " + ex.StackTrace);
        }
        result.SourceCount = state.Sources.Count;
        result.Errors.AddRange(state.Unresolved.OrderBy(x => x, StringComparer.Ordinal));
        // The selected ID file is usable only when the proof is complete.
        WriteLines(Path.Combine(output, result.SelectedPath), result.Errors.Count == 0
            ? state.Selected.Select(id => Json.Serialize(id)) : Enumerable.Empty<string>());
        result.Count = result.Errors.Count == 0 ? state.Selected.Count : 0;
        WriteLines(Path.Combine(output, result.BindingsPath), state.Mappings.Values.Select(Json.Serialize));
        result.BindingCount = state.Mappings.Count;
        result.Sha256 = Hash(Path.Combine(output, result.SelectedPath));
        result.BindingsSha256 = Hash(Path.Combine(output, result.BindingsPath));
        var proof = new Dictionary<string, object> {
            { "assembly", game.FullName }, { "roots", RootTypes }, { "scannedMethods", state.Scanned.ToArray() },
            { "selectedFields", state.Fields.ToArray() }, { "directPathLiterals", state.Literals.ToArray() },
            { "domainEvidence", state.DomainEvidence },
            { "sourceTextures", state.Sources.Count }, { "selectedAssetKeys", state.Selected.ToArray() },
            { "unresolved", result.Errors.ToArray() }
        };
        File.WriteAllText(Path.Combine(output, result.ProofPath), Json.Serialize(proof) + "\n", new UTF8Encoding(false));
        result.Capabilities["available"] = result.Errors.Count == 0;
        result.Capabilities["sourceTextures"] = result.SourceCount;
        result.Capabilities["selectedTextures"] = result.Count;
        result.Capabilities["scannedMethods"] = state.Scanned.Count;
        result.Capabilities["selectedFields"] = state.Fields.Count;
        result.Capabilities["unresolvedCount"] = result.Errors.Count;
        result.Capabilities["proofPath"] = result.ProofPath;
        result.Capabilities["source"] = "uploaded game assembly IL and official texture binders";
        return result;
    }

    private static void LoadSources(State state, string path)
    {
        foreach (string line in File.ReadLines(path))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string, object>>(line);
            string id = Convert.ToString(row["id"], CultureInfo.InvariantCulture);
            int width = Convert.ToInt32(row["width"], CultureInfo.InvariantCulture);
            int height = Convert.ToInt32(row["height"], CultureInfo.InvariantCulture);
            if (id.Length == 0 || width < 1 || height < 1 || state.Sources.ContainsKey(id))
                throw new InvalidDataException("Invalid or case-colliding source texture: " + id);
            state.Sources.Add(id, id);
            state.Dimensions.Add(id, new[] { width, height });
        }
        if (state.Sources.Count == 0) throw new InvalidDataException("Empty texture dimensions index");
    }

    private static Type RequiredType(Assembly game, string name)
    {
        Type type = game.GetType(name, false);
        if (type == null) throw new MissingMemberException("Game type missing: " + name);
        return type;
    }

    private static string MethodId(MethodBase method)
    {
        MethodInfo info = method as MethodInfo;
        string instantiation = info != null && info.IsGenericMethod && !info.IsGenericMethodDefinition
            ? "<" + string.Join(",", info.GetGenericArguments().Select(t => t.FullName ?? t.Name)) + ">" : "";
        return method.DeclaringType.FullName + "." + method.Name + instantiation + "#" +
            method.MetadataToken.ToString("x8", CultureInfo.InvariantCulture);
    }

    private static void Enqueue(State state, MethodBase method, string caller = "root")
    {
        if (method == null || method.DeclaringType == null || method.DeclaringType.Assembly != state.Game) return;
        string id = MethodId(method);
        if (state.Queued.Add(id)) { state.Pending.Enqueue(method); state.Caller[id] = caller; }
    }

    private static void ScanGame(State state)
    {
        foreach (string name in RootTypes)
        {
            Type root = RequiredType(state.Game, name);
            foreach (MethodBase method in root.GetMethods(Any | BindingFlags.DeclaredOnly))
            {
                if (name != "Terraria.Mount" || method.Name == "Initialize" || method.Name == "Draw") Enqueue(state, method);
                else InspectMountMethodDirectly(state, method);
            }
            foreach (MethodBase ctor in root.GetConstructors(Any | BindingFlags.DeclaredOnly)) Enqueue(state, ctor);
        }
        Type main = RequiredType(state.Game, "Terraria.Main");
        foreach (string name in new[] { "DrawProj", "DrawProjDirect" })
        {
            MethodInfo[] methods = main.GetMethods(Any).Where(m => m.Name == name).ToArray();
            if (methods.Length == 0) state.Error("missing draw root Main." + name);
            foreach (MethodInfo method in methods) Enqueue(state, method);
        }
        while (state.Pending.Count != 0)
        {
            MethodBase method = state.Pending.Dequeue();
            string id = MethodId(method);
            if (!state.Scanned.Add(id)) continue;
            if (method.DeclaringType.FullName == "Terraria.Initializers.DyeInitializer" &&
                method.Name == "LoadArmorDyes" &&
                !HasOfficialIl(method, "b4a58e84685632f519693c24a2279fb2ee74ea5b1c43d4642335c28802e3eff5"))
                state.Error("DyeInitializer.LoadArmorDyes IL changed; shader image adapter needs review");
            if (method.DeclaringType.FullName == "Terraria.Initializers.DyeInitializer" &&
                method.Name == "LoadHairDyes" &&
                !HasOfficialIl(method, "78dd322c91139d26616b5b132f5d05bc8de8a851f3fa9ead898bea8f2de7e900"))
                state.Error("DyeInitializer.LoadHairDyes IL changed; shader image adapter needs review");
            List<Instruction> code;
            try { code = Decode(method); }
            catch (Exception ex) { state.Error("IL decode " + id + ": " + ex.Message); continue; }
            for (int i = 0; i < code.Count; i++)
            {
                Instruction ins = code[i];
                FieldInfo field = ins.Value as FieldInfo;
                if (field != null && field.DeclaringType == state.TextureAssets)
                    state.Fields.Add(field.Name);
                string literal = ins.Value as string;
                if (literal != null && IsImageLiteral(literal)) state.Literals.Add(Normalize(literal));
                if (ins.Op == OpCodes.Calli)
                { state.Error("indirect call " + id + "@" + ins.Offset); continue; }
                MethodBase target = ins.Value as MethodBase;
                if (target == null) continue;
                if (ins.Op == OpCodes.Ldvirtftn)
                    state.Error("dynamic method target " + id + "@" + ins.Offset);
                if (ins.Op == OpCodes.Call || ins.Op == OpCodes.Callvirt || ins.Op == OpCodes.Newobj || ins.Op == OpCodes.Ldftn)
                {
                    if (target.DeclaringType != null && target.DeclaringType.Assembly == state.Game)
                    {
                        MethodInfo info = target as MethodInfo;
                        if (ins.Op == OpCodes.Callvirt && info != null && info.IsVirtual && !info.IsFinal && !info.DeclaringType.IsSealed)
                            EnqueueVirtualTargets(state, info, id);
                        Enqueue(state, target, id);
                    }
                    if (IsShaderImageMethod(target) && !HasProvenImageArgument(code, i, target, method))
                        state.Error("dynamic shader image argument " + id + "@" + ins.Offset + " -> " + target.Name + " [" + Context(code, i) + "]");
                    if (IsAssetRequest(target) && !HasProvenImageArgument(code, i, target, method) && !HasNearbyBoundAssetName(code, i) &&
                        !IsShaderImageMethod(method))
                        state.Error("dynamic asset request " + id + "@" + ins.Offset + " -> " + target.Name + " [" + Context(code, i) + "]");
                }
            }
        }
    }

    private static void InspectMountMethodDirectly(State state, MethodBase method)
    {
        string id = MethodId(method);
        List<Instruction> code;
        try { code = Decode(method); }
        catch (Exception ex) { state.Error("Mount direct IL decode " + id + ": " + ex.Message); return; }
        state.Scanned.Add("direct:" + id);
        foreach (Instruction ins in code)
        {
            FieldInfo field = ins.Value as FieldInfo;
            if (field != null && field.DeclaringType == state.TextureAssets) state.Fields.Add(field.Name);
            string literal = ins.Value as string;
            if (literal != null && IsImageLiteral(literal)) state.Literals.Add(Normalize(literal));
        }
    }

    private static void EnqueueVirtualTargets(State state, MethodInfo target, string caller)
    {
        if (!state.Dispatched.Add(MethodId(target))) return;
        if (state.GameTypes == null) state.GameTypes = state.Game.GetTypes();
        int found = 0;
        foreach (Type definition in state.GameTypes)
        {
            Type type = definition;
            if (definition.IsGenericTypeDefinition && target.DeclaringType.IsGenericType &&
                !target.DeclaringType.ContainsGenericParameters &&
                definition.GetGenericArguments().Length == target.DeclaringType.GetGenericArguments().Length)
            {
                try { type = definition.MakeGenericType(target.DeclaringType.GetGenericArguments()); }
                catch (ArgumentException) { continue; }
            }
            if (type.IsAbstract || !target.DeclaringType.IsAssignableFrom(type)) continue;
            if (target.DeclaringType.IsInterface)
            {
                try
                {
                    InterfaceMapping map = type.GetInterfaceMap(target.DeclaringType);
                    for (int i = 0; i < map.InterfaceMethods.Length; i++)
                        if (map.InterfaceMethods[i].Name == target.Name &&
                            map.InterfaceMethods[i].GetParameters().Length == target.GetParameters().Length)
                        { Enqueue(state, map.TargetMethods[i], caller); found++; }
                }
                catch (ArgumentException) { }
            }
            else
                foreach (MethodInfo method in type.GetMethods(Any))
                    if (method.IsVirtual && method.GetBaseDefinition().MetadataToken == target.GetBaseDefinition().MetadataToken &&
                        method.GetBaseDefinition().Module == target.GetBaseDefinition().Module)
                    { Enqueue(state, method, caller); found++; }
        }
        if (found == 0 && (target.IsAbstract || target.DeclaringType.IsInterface))
            state.Error("virtual target has no game implementation: " + MethodId(target) + " via " + CallerPath(state, caller));
    }

    private static string CallerPath(State state, string current)
    {
        var path = new List<string>();
        for (int i = 0; i < 12 && current != "root"; i++)
        {
            path.Add(current);
            if (!state.Caller.TryGetValue(current, out current)) break;
        }
        path.Reverse();
        return string.Join(" -> ", path);
    }

    private static bool IsAssetRequest(MethodBase method)
    {
        string name = method.Name;
        string type = method.DeclaringType == null ? "" : method.DeclaringType.FullName;
        return (name == "Request" || name == "LoadAsset") &&
            (type.Contains("Asset") || type.Contains("Content")) &&
            method.GetParameters().Length != 0 && method.GetParameters()[0].ParameterType == typeof(string);
    }

    private static bool IsShaderImageMethod(MethodBase method)
    {
        string type = method.DeclaringType == null ? "" : method.DeclaringType.FullName;
        return method.Name.StartsWith("UseImage", StringComparison.Ordinal) && type.Contains("ShaderData") &&
            method.GetParameters().Length != 0 && method.GetParameters()[0].ParameterType == typeof(string);
    }

    private static bool HasNearbyBoundAssetName(List<Instruction> code, int at)
    {
        // Request<T>(asset.Name, mode): the name getter must produce this
        // call's first argument. A distant getter in the same method proves
        // nothing about a dynamic request.
        if (at < 2 || !IntConstant(code[at - 1]).HasValue) return false;
        MethodBase getter = code[at - 2].Value as MethodBase;
        if (getter == null || getter.Name != "get_Name" || getter.DeclaringType == null ||
            !getter.DeclaringType.FullName.StartsWith("ReLogic.Content.Asset", StringComparison.Ordinal)) return false;
        for (int i = at - 3; i >= Math.Max(0, at - 9); i--)
        {
            FieldInfo field = code[i].Value as FieldInfo;
            if (code[i].Op == OpCodes.Ldsfld && field != null &&
                field.DeclaringType != null && field.DeclaringType.FullName == "Terraria.GameContent.TextureAssets") return true;
            if (code[i].Value is MethodBase) break;
        }
        return false;
    }

    private static bool HasOfficialIl(MethodBase method, string expected)
    {
        MethodBody body = method.GetMethodBody();
        if (body == null) return false;
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(body.GetILAsByteArray())).Replace("-", "")
                .Equals(expected, StringComparison.OrdinalIgnoreCase);
    }

    private static string Context(List<Instruction> code, int at)
    {
        return string.Join(" ", code.Skip(Math.Max(0, at - 8)).Take(Math.Min(at + 1, 9)).Select(x =>
            x.Op.Name + (x.Value == null ? "" : ":" + (x.Value is MemberInfo ? ((MemberInfo)x.Value).Name : x.Value.ToString()))));
    }

    private static bool HasProvenImageArgument(List<Instruction> code, int at, MethodBase target, MethodBase caller)
    {
        int parameters = target.GetParameters().Length;
        if (parameters < 1) return false;
        int argumentEnd = at - parameters;
        if (argumentEnd < 0) return false;
        for (int i = argumentEnd + 1; i < at; i++)
            if (!IntConstant(code[i]).HasValue && code[i].Op != OpCodes.Ldnull) return false;
        if (code[argumentEnd].Op == OpCodes.Ldstr && code[argumentEnd].Value is string &&
            IsImageLiteral((string)code[argumentEnd].Value)) return true;
        // A directly supplied String.Concat(prefix, boxed Int16) is an exact
        // path, not a nearby literal guess. Check every instruction that
        // pushes the two arguments and the overload signature.
        MethodBase concat = code[argumentEnd].Value as MethodBase;
        if (concat == null || concat.DeclaringType != typeof(string) || concat.Name != "Concat" ||
            concat.GetParameters().Length != 2) return false;
        if (IsOfficialAmbientConcat(code, caller)) return true;
        if (argumentEnd < 3 ||
            concat.GetParameters()[0].ParameterType != typeof(object) ||
            concat.GetParameters()[1].ParameterType != typeof(object) ||
            code[argumentEnd - 3].Op != OpCodes.Ldstr ||
            !(code[argumentEnd - 3].Value is string) ||
            !IsImageLiteral((string)code[argumentEnd - 3].Value) ||
            !IntConstant(code[argumentEnd - 2]).HasValue ||
            code[argumentEnd - 1].Op != OpCodes.Box ||
            code[argumentEnd - 1].Value as Type != typeof(short)) return false;
        return true;
    }

    private static bool IsOfficialAmbientConcat(List<Instruction> code, MethodBase caller)
    {
        string type = caller.DeclaringType.FullName;
        const string prefix = "Terraria.GameContent.Skies.AmbientSky+";
        if (caller.Name != ".ctor" || !type.StartsWith(prefix, StringComparison.Ordinal)) return false;
        string[] expected;
        if (!OfficialAmbientConcats.TryGetValue(type.Substring(prefix.Length), out expected) ||
            !HasOfficialIl(caller, expected[0])) return false;
        return code.Count(x => x.Value is MethodBase && IsAssetRequest((MethodBase)x.Value)) == 1 &&
            code.Any(x => x.Op == OpCodes.Ldstr && string.Equals(x.Value as string, expected[1], StringComparison.Ordinal));
    }

    private static bool IsImageLiteral(string value)
    {
        return value.StartsWith("Images/", StringComparison.OrdinalIgnoreCase) ||
            value.StartsWith("Images\\", StringComparison.OrdinalIgnoreCase);
    }

    private static string Normalize(string value)
    {
        value = value.Replace('\\', '/');
        return value.StartsWith("Images/", StringComparison.OrdinalIgnoreCase) ? value.Substring(7) : value;
    }

    private static List<Instruction> Decode(MethodBase method)
    {
        MethodBody body = method.GetMethodBody();
        var result = new List<Instruction>();
        if (body == null) return result;
        byte[] il = body.GetILAsByteArray();
        Type[] typeArgs = method.DeclaringType.IsGenericType ? method.DeclaringType.GetGenericArguments() : Type.EmptyTypes;
        MethodInfo info = method as MethodInfo;
        Type[] methodArgs = info != null && info.IsGenericMethod ? info.GetGenericArguments() : Type.EmptyTypes;
        for (int p = 0; p < il.Length; )
        {
            int offset = p;
            int lead = il[p++];
            OpCode op = lead == 0xfe ? Two[ReadByte(il, ref p)] : One[lead];
            if (op.Name == null) throw new InvalidDataException("Unknown opcode at " + offset);
            object value = null;
            int size = 0;
            switch (op.OperandType)
            {
                case OperandType.InlineNone: break;
                case OperandType.ShortInlineBrTarget:
                case OperandType.ShortInlineI:
                case OperandType.ShortInlineVar: size = 1; break;
                case OperandType.InlineVar: size = 2; break;
                case OperandType.InlineBrTarget:
                case OperandType.InlineI:
                case OperandType.ShortInlineR: size = 4; break;
                case OperandType.InlineI8:
                case OperandType.InlineR: size = 8; break;
                case OperandType.InlineSwitch:
                    int count = ReadInt32(il, ref p);
                    if (count < 0 || count > (il.Length - p) / 4) throw new InvalidDataException("Invalid switch table");
                    size = count * 4;
                    break;
                case OperandType.InlineField:
                case OperandType.InlineMethod:
                case OperandType.InlineType:
                case OperandType.InlineTok:
                    int token = ReadInt32(il, ref p);
                    try { value = method.Module.ResolveMember(token, typeArgs, methodArgs); }
                    catch (Exception ex) { throw new InvalidDataException("Cannot resolve member token " + token.ToString("x8") + " at " + offset, ex); }
                    break;
                case OperandType.InlineString:
                    int stringToken = ReadInt32(il, ref p);
                    try { value = method.Module.ResolveString(stringToken); }
                    catch (Exception ex) { throw new InvalidDataException("Cannot resolve string token at " + offset, ex); }
                    break;
                case OperandType.InlineSig:
                    ReadInt32(il, ref p); // Signature length is fixed; calli remains unsupported.
                    break;
                default: throw new InvalidDataException("Unsupported operand " + op.OperandType);
            }
            if (p + size > il.Length) throw new InvalidDataException("Truncated operand at " + offset);
            if (op == OpCodes.Ldc_I4_S) value = unchecked((sbyte)il[p]);
            else if (op == OpCodes.Ldc_I4) value = BitConverter.ToInt32(il, p);
            p += size;
            result.Add(new Instruction { Offset = offset, Op = op, Value = value });
        }
        return result;
    }

    private static byte ReadByte(byte[] il, ref int p)
    {
        if (p >= il.Length) throw new InvalidDataException("Truncated opcode");
        return il[p++];
    }

    private static int ReadInt32(byte[] il, ref int p)
    {
        if (p > il.Length - 4) throw new InvalidDataException("Truncated token or count");
        int value = BitConverter.ToInt32(il, p);
        p += 4;
        return value;
    }

    private static void SelectBaseTextures(State state)
    {
        foreach (string source in state.Sources.Values)
            if (source.StartsWith("Item_", StringComparison.OrdinalIgnoreCase) ||
                source.StartsWith("Tiles_", StringComparison.OrdinalIgnoreCase) ||
                source.StartsWith("Wall_", StringComparison.OrdinalIgnoreCase) ||
                source.StartsWith("NPC_", StringComparison.OrdinalIgnoreCase))
                state.Selected.Add(source);
        // The four domains are mandatory even if no player draw method touches them.
        foreach (string prefix in new[] { "Item_", "Tiles_", "Wall_", "NPC_" })
            if (!state.Selected.Any(id => id.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)))
                state.Error("base texture family absent: " + prefix);
        string references = Path.Combine(state.Output, "texture-references.ndjson");
        if (!File.Exists(references)) { state.Error("texture-references.ndjson missing"); return; }
        foreach (string line in File.ReadLines(references))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string, object>>(line);
            object value;
            if (!row.TryGetValue("assetId", out value) || value == null) continue;
            string id = Convert.ToString(value, CultureInfo.InvariantCulture);
            string canonical;
            if (!state.Sources.TryGetValue(id, out canonical)) state.Error("texture-reference source absent: " + id);
            else state.Selected.Add(canonical);
        }
    }

    private static void BindPlayerFields(State state)
    {
        foreach (string field in PlayerArmorDomains.Keys) state.Fields.Add(field);
        foreach (string field in new[] { "PlayerHair", "PlayerHairAlt", "Extra", "Players" }) state.Fields.Add(field);
        foreach (FieldInfo field in state.TextureAssets.GetFields(Any))
            if (field.Name.IndexOf("Mount", StringComparison.Ordinal) >= 0 || field.Name == "RudolphMount")
                state.Fields.Add(field.Name);
        var selected = state.Fields.Where(name => !BaseFamilies.Contains(name)).ToArray();
        Dictionary<string, int[]> shapes;
        SortedDictionary<string, string> bindings = OfficialTextureBindings.Load(state.Game, selected, state.Dimensions, out shapes);
        foreach (var pair in bindings)
        {
            string[] parts = pair.Key.Substring("TextureAssets.".Length).Split(':');
            // The official loader creates asset handles for sparse Content slots.
            // A handle without an XNB is not a publishable texture. Required
            // drawable domains are checked against the mapped source set below.
            if (!state.Sources.ContainsKey(pair.Value))
            {
                if (parts[0] == "Players" || parts.Length == 1)
                    state.Error("official binding source absent: " + pair.Key + " -> " + pair.Value);
                continue;
            }
            AddSource(state, parts[0], parts.Skip(1).Select(x => int.Parse(x, CultureInfo.InvariantCulture)).ToArray(),
                pair.Value, "AssetInitializer.LoadTextures/final TextureAssets.Name");
        }
        ValidatePlayerArrayDomains(state, shapes);
        state.DomainEvidence["officialLoadTexturesReturned"] = true;
        state.DomainEvidence["officialBindingCount"] = bindings.Count;
    }

    private static void ValidatePlayerArrayDomains(State state, IDictionary<string, int[]> shapes)
    {
        var counts = new Dictionary<string, int>(StringComparer.Ordinal);
        Type body = RequiredType(state.Game, "Terraria.ID.ArmorIDs+Body");
        Type bodySets = RequiredType(state.Game, "Terraria.ID.ArmorIDs+Body+Sets");
        bool[] modernBody = bodySets.GetField("UsesNewFramingCode", Any).GetValue(null) as bool[];
        if (modernBody == null) { state.Error("ArmorIDs.Body.Sets.UsesNewFramingCode absent"); return; }
        foreach (var pair in PlayerArmorDomains)
        {
            Type domain = RequiredType(state.Game, "Terraria.ID.ArmorIDs+" + pair.Value);
            int count = Convert.ToInt32(domain.GetField("Count", Any).GetValue(null), CultureInfo.InvariantCulture);
            int[] shape;
            if (!shapes.TryGetValue(pair.Key, out shape) || shape.Length != 1 || shape[0] != count)
            { state.Error("official equip array domain changed: " + pair.Key + " / " + pair.Value + ".Count=" + count); continue; }
            counts[pair.Key] = count;
            for (int index = 1; index < count; index++)
            {
                if (pair.Key == "ArmorBodyComposite" && (index >= modernBody.Length || !modernBody[index])) continue;
                if (!state.Mappings.ContainsKey("TextureAssets." + pair.Key + ":" + index))
                    state.Error("official equip source absent: TextureAssets." + pair.Key + ":" + index);
            }
        }
        if (modernBody.Length != Convert.ToInt32(body.GetField("Count", Any).GetValue(null), CultureInfo.InvariantCulture))
            state.Error("ArmorIDs.Body modern framing domain changed");
        int extraCount = Convert.ToInt32(RequiredType(state.Game, "Terraria.ID.ExtrasID").GetField("Count", Any).GetValue(null), CultureInfo.InvariantCulture);
        int[] extra;
        if (!shapes.TryGetValue("Extra", out extra) || extra.Length != 1 || extra[0] != extraCount) state.Error("official Extra domain changed: " + extraCount);
        else
        {
            counts["Extra"] = extraCount;
            for (int i = 0; i < extraCount; i++)
                if (!state.Mappings.ContainsKey("TextureAssets.Extra:" + i))
                    state.Error("official Extra source absent: TextureAssets.Extra:" + i);
        }
        int? hairCount = null;
        foreach (string field in new[] { "PlayerHair", "PlayerHairAlt" })
        {
            int[] hair;
            if (!shapes.TryGetValue(field, out hair) || hair.Length != 1 || hair[0] == 0) { state.Error("official hair array absent: " + field); continue; }
            counts[field] = hair[0];
            if (hairCount.HasValue && hair[0] != hairCount.Value) state.Error("hair/alt array length differs");
            hairCount = hair[0];
            for (int i = 0; i < hair[0]; i++)
                if (!state.Mappings.ContainsKey("TextureAssets." + field + ":" + i))
                    state.Error("official hair source absent: TextureAssets." + field + ":" + i);
        }
        FieldInfo maxHair = RequiredType(state.Game, "Terraria.Main").GetField("maxHairStyles", Any);
        if (maxHair != null && hairCount.HasValue && Convert.ToInt32(maxHair.IsLiteral ? maxHair.GetRawConstantValue() : maxHair.GetValue(null), CultureInfo.InvariantCulture) != hairCount.Value)
            state.Error("Main.maxHairStyles disagrees with official hair array length");
        int[] players;
        int variants = Convert.ToInt32(RequiredType(state.Game, "Terraria.ID.PlayerVariantID").GetField("Count", Any).GetValue(null), CultureInfo.InvariantCulture);
        int pieces = Convert.ToInt32(RequiredType(state.Game, "Terraria.ID.PlayerTextureID").GetField("Count", Any).GetValue(null), CultureInfo.InvariantCulture);
        if (!shapes.TryGetValue("Players", out players) || players.Length != 2 || players[0] != variants || players[1] != pieces)
            state.Error("official Players domain changed: " + variants + "x" + pieces);
        else counts["Players"] = variants * pieces;
        state.DomainEvidence["verifiedPlayerArrayCounts"] = counts;
        state.DomainEvidence["modernCompositeIds"] = Enumerable.Range(1, modernBody.Length - 1).Where(x => modernBody[x]).ToArray();
    }

    private static int? IntConstant(Instruction ins)
    {
        if (ins.Op == OpCodes.Ldc_I4_M1) return -1;
        if (ins.Op == OpCodes.Ldc_I4_0) return 0;
        if (ins.Op == OpCodes.Ldc_I4_1) return 1;
        if (ins.Op == OpCodes.Ldc_I4_2) return 2;
        if (ins.Op == OpCodes.Ldc_I4_3) return 3;
        if (ins.Op == OpCodes.Ldc_I4_4) return 4;
        if (ins.Op == OpCodes.Ldc_I4_5) return 5;
        if (ins.Op == OpCodes.Ldc_I4_6) return 6;
        if (ins.Op == OpCodes.Ldc_I4_7) return 7;
        if (ins.Op == OpCodes.Ldc_I4_8) return 8;
        if (ins.Op == OpCodes.Ldc_I4 || ins.Op == OpCodes.Ldc_I4_S) return Convert.ToInt32(ins.Value, CultureInfo.InvariantCulture);
        return null;
    }

    private static void AddSource(State state, string field, int[] indices, string requested, string source)
    {
        string actual;
        if (!state.Sources.TryGetValue(requested, out actual))
        { state.Error("official binding source absent: " + field + " -> " + requested); return; }
        state.Selected.Add(actual);
        string id = "TextureAssets." + field + (indices.Length == 0 ? "" : ":" + string.Join(":", indices));
        object existing;
        if (state.Mappings.TryGetValue(id, out existing))
        {
            string old = Convert.ToString(((Dictionary<string, object>)existing)["assetId"], CultureInfo.InvariantCulture);
            if (old != actual) state.Error("conflicting official binding: " + id + " -> " + old + "/" + actual);
            return;
        }
        state.Mappings.Add(id, new Dictionary<string, object> { { "id", id }, { "field", field },
            { "indices", indices }, { "assetId", actual }, { "source", source } });
    }

    private static void SelectDirectPaths(State state)
    {
        foreach (string literal in state.Literals)
        {
            string actual;
            if (state.Sources.TryGetValue(literal, out actual)) { state.Selected.Add(actual); continue; }
            int matches = 0;
            foreach (string id in state.Sources.Values)
                if (id.StartsWith(literal, StringComparison.OrdinalIgnoreCase)) { state.Selected.Add(id); matches++; }
            if (matches == 0) state.Error("referenced Images path has no source: " + literal);
        }
    }

    private static void CheckLegacyArmorSlots(State state)
    {
        string armorPath = Path.Combine(state.Output, "armor-sets.ndjson");
        string itemsPath = Path.Combine(state.Output, "items.ndjson");
        if (!File.Exists(armorPath) || !File.Exists(itemsPath))
        { state.Error("armor-sets/items missing for old-frame reachability check"); return; }
        var modernBody = new Dictionary<int, bool>();
        var oldHandsOn = new SortedSet<int>();
        var oldHandsOff = new SortedSet<int>();
        foreach (string line in File.ReadLines(armorPath))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string, object>>(line);
            string domain = Convert.ToString(row["domain"], CultureInfo.InvariantCulture);
            int id = Convert.ToInt32(row["numericId"], CultureInfo.InvariantCulture);
            var sets = (Dictionary<string, object>)row["sets"];
            object flag;
            if (domain == "Body" && sets.TryGetValue("UsesNewFramingCode", out flag))
                modernBody[id] = Convert.ToBoolean(flag, CultureInfo.InvariantCulture);
            if (sets.TryGetValue("UsesOldFramingTexturesForWalking", out flag) &&
                Convert.ToBoolean(flag, CultureInfo.InvariantCulture))
            {
                if (domain == "HandOn") oldHandsOn.Add(id);
                else if (domain == "HandOff") oldHandsOff.Add(id);
            }
        }
        if (modernBody.Count == 0) { state.Error("ArmorIDs.Body.Sets.UsesNewFramingCode unavailable"); return; }
        int equipableBodySlots = 0, oldBodySlots = 0;
        foreach (string line in File.ReadLines(itemsPath))
        {
            if (line.Length == 0) continue;
            var row = Json.Deserialize<Dictionary<string, object>>(line);
            var gameplay = (Dictionary<string, object>)row["gameplay"];
            int bodySlot = Convert.ToInt32(gameplay["bodySlot"], CultureInfo.InvariantCulture);
            if (bodySlot <= 0) continue;
            equipableBodySlots++;
            bool modern;
            if (!modernBody.TryGetValue(bodySlot, out modern))
                state.Error("item body slot outside ArmorIDs.Body: " + bodySlot);
            else if (!modern)
            {
                oldBodySlots++;
                foreach (string field in new[] { "ArmorBody", "ArmorArm", "FemaleBody" })
                    if (!state.Mappings.ContainsKey("TextureAssets." + field + ":" + bodySlot))
                        state.Error("equipable old-frame body source missing: " + field + ":" + bodySlot);
            }
        }
        foreach (int id in oldHandsOn)
            if (!state.Mappings.ContainsKey("TextureAssets.AccHandsOn:" + id))
                state.Error("old-frame walking hand source missing: AccHandsOn:" + id);
        foreach (int id in oldHandsOff)
            if (!state.Mappings.ContainsKey("TextureAssets.AccHandsOff:" + id))
                state.Error("old-frame walking hand source missing: AccHandsOff:" + id);
        state.DomainEvidence["bodyDomainEntries"] = modernBody.Count;
        state.DomainEvidence["equipableBodyItemCount"] = equipableBodySlots;
        state.DomainEvidence["oldFrameBodyItemCount"] = oldBodySlots;
        state.DomainEvidence["oldFrameWalkingHandsOn"] = oldHandsOn.ToArray();
        state.DomainEvidence["oldFrameWalkingHandsOff"] = oldHandsOff.ToArray();
    }

    private static void CheckDrawPlanSubset(State state)
    {
        string path = Path.Combine(state.Output, "player-draw-plans.ndjson");
        if (!File.Exists(path)) { state.Error("player draw plans unavailable for subset check"); return; }
        var asset = new Regex("\\\"assetId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"", RegexOptions.CultureInvariant);
        int references = 0;
        foreach (string line in File.ReadLines(path))
            foreach (Match match in asset.Matches(line))
            {
                references++;
                string id = match.Groups[1].Value, actual;
                if (!state.Sources.TryGetValue(id, out actual)) state.Error("draw plan asset absent from source: " + id);
                else if (!state.Selected.Contains(actual)) state.Error("draw plan asset outside closure: " + actual);
            }
        if (references == 0) state.Error("draw plan contains no asset references");
    }

    private static void WriteLines(string path, IEnumerable<string> lines)
    {
        using (var writer = new StreamWriter(path, false, new UTF8Encoding(false)))
            foreach (string line in lines) writer.WriteLine(line);
    }

    private static string Hash(string path)
    {
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
}
