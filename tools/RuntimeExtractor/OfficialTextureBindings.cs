using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Runtime.Remoting.Messaging;
using System.Runtime.Remoting.Proxies;
using System.Runtime.Serialization;

// Execute the game's texture binder with a metadata-only asset repository.
// Only final TextureAssets slots are bindings; intercepted incidental requests are not.
internal static class OfficialTextureBindings
{
    private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

    internal static SortedDictionary<string, string> Load(Assembly game, IEnumerable<string> selectedFields,
        IDictionary<string, int[]> dimensions, out Dictionary<string, int[]> shapes, Action afterLoad = null)
    {
        Type main = Required(game, "Terraria.Main"), textureAssets = Required(game, "Terraria.GameContent.TextureAssets");
        Type texture = FindType(game, "Microsoft.Xna.Framework.Graphics.Texture2D");
        Type assetType = FindType(game, "ReLogic.Content.Asset`1").MakeGenericType(texture);
        FieldInfo assets = Field(main, "Assets"), instance = Field(main, "instance");
        FieldInfo resources = Field(main, "ResourceSetsManager"), minimap = Field(main, "MinimapFrameManagerInstance");
        object oldAssets = assets.GetValue(null), oldInstance = instance.GetValue(null);
        object oldResources = resources.GetValue(null), oldMinimap = minimap.GetValue(null);
        FieldInfo advisor = Field(main, "_achievementAdvisor");
        FieldInfo waterfall = Field(main, "waterfallManager");
        object temporaryInstance = FormatterServices.GetUninitializedObject(main);
        object oldAdvisor = advisor.GetValue(temporaryInstance);
        object oldWaterfall = waterfall.GetValue(temporaryInstance);
        var snapshot = textureAssets.GetFields(Any).Where(f => f.IsStatic && !f.IsLiteral)
            .Select(f => new KeyValuePair<FieldInfo, object>(f, f.GetValue(null))).ToArray();
        var arrayCopies = snapshot.Where(p => p.Value is Array)
            .Select(p => new KeyValuePair<Array, Array>((Array)p.Value, (Array)((Array)p.Value).Clone())).ToArray();
        shapes = new Dictionary<string, int[]>(StringComparer.Ordinal);
        try
        {
            assets.SetValue(null, new RepositoryProxy(assets.FieldType, texture, assetType, dimensions).GetTransparentProxy());
            instance.SetValue(null, temporaryInstance);
            resources.SetValue(null, Activator.CreateInstance(resources.FieldType));
            minimap.SetValue(null, Activator.CreateInstance(minimap.FieldType));
            advisor.SetValue(temporaryInstance, Activator.CreateInstance(advisor.FieldType));
            waterfall.SetValue(temporaryInstance, Activator.CreateInstance(waterfall.FieldType));
            Type initializer = Required(game, "Terraria.Initializers.AssetInitializer");
            MethodInfo load = initializer.GetMethod("LoadTextures", Any);
            if (load == null || load.GetParameters().Length != 1) throw new MissingMethodException("AssetInitializer.LoadTextures(mode)");
            Type mode = load.GetParameters()[0].ParameterType;
            load.Invoke(null, new[] { Enum.ToObject(mode, 0) });
            if (afterLoad != null) afterLoad();

            var result = new SortedDictionary<string, string>(StringComparer.Ordinal);
            foreach (string name in selectedFields.Concat(new[] { "Players" }).Distinct(StringComparer.Ordinal))
            {
                FieldInfo field = textureAssets.GetField(name, Any);
                if (field == null) throw new MissingFieldException("TextureAssets." + name);
                object value = field.GetValue(null);
                if (field.FieldType == assetType)
                    Add(result, "TextureAssets." + name, value, assetType);
                else if (field.FieldType.IsArray && field.FieldType.GetElementType() == assetType)
                {
                    Array array = value as Array;
                    if (array == null) throw new InvalidOperationException("TextureAssets." + name + " array is null after LoadTextures");
                    shapes.Add(name, Enumerable.Range(0, array.Rank).Select(array.GetLength).ToArray());
                    if (array.Rank == 1)
                        for (int i = 0; i < array.Length; i++) Add(result, "TextureAssets." + name + ":" + i, array.GetValue(i), assetType);
                    else if (array.Rank == 2)
                        for (int i = 0; i < array.GetLength(0); i++)
                            for (int j = 0; j < array.GetLength(1); j++)
                                Add(result, "TextureAssets." + name + ":" + i + ":" + j, array.GetValue(i, j), assetType);
                    else throw new InvalidOperationException("Unsupported TextureAssets rank: " + name);
                }
                else throw new InvalidOperationException("Selected TextureAssets field is not Texture2D: " + name);
            }
            return result;
        }
        finally
        {
            foreach (var pair in snapshot) pair.Key.SetValue(null, pair.Value);
            foreach (var pair in arrayCopies) Array.Copy(pair.Value, pair.Key, pair.Value.Length);
            advisor.SetValue(temporaryInstance, oldAdvisor);
            waterfall.SetValue(temporaryInstance, oldWaterfall);
            resources.SetValue(null, oldResources);
            minimap.SetValue(null, oldMinimap);
            instance.SetValue(null, oldInstance);
            assets.SetValue(null, oldAssets);
        }
    }

    private static void Add(IDictionary<string, string> result, string id, object value, Type assetType)
    {
        if (value == null) return;
        string path = assetType.GetProperty("Name", Any).GetValue(value, null) as string;
        if (String.IsNullOrEmpty(path)) return; // The game's Asset<T>.Empty has no Content source.
        path = path.Replace('\\', '/');
        if (!path.StartsWith("Images/", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Texture asset is outside Images: " + id + " -> " + path);
        result.Add(id, path.Substring("Images/".Length));
    }

    private static Type Required(Assembly game, string name)
    {
        Type result = game.GetType(name, false);
        if (result == null) throw new TypeLoadException(name);
        return result;
    }

    private static Type FindType(Assembly game, string name)
    {
        return game.GetType(name, false) ?? AppDomain.CurrentDomain.GetAssemblies()
            .Select(a => a.GetType(name, false)).First(t => t != null);
    }

    private static FieldInfo Field(Type type, string name)
    {
        FieldInfo result = type.GetField(name, Any);
        if (result == null) throw new MissingFieldException(type.FullName, name);
        return result;
    }

    private sealed class RepositoryProxy : RealProxy
    {
        private readonly Type texture, assetType;
        private readonly IDictionary<string, int[]> dimensions;
        private readonly Dictionary<string, object> cache = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

        internal RepositoryProxy(Type interfaceType, Type texture, Type assetType, IDictionary<string, int[]> dimensions)
            : base(interfaceType)
        { this.texture = texture; this.assetType = assetType; this.dimensions = dimensions; }

        public override IMessage Invoke(IMessage message)
        {
            IMethodCallMessage call = message as IMethodCallMessage;
            MethodInfo method = call == null ? null : call.MethodBase as MethodInfo;
            if (method == null || method.Name != "Request" || !method.IsGenericMethod ||
                method.GetGenericArguments().Length != 1 || method.GetGenericArguments()[0] != texture ||
                method.ReturnType != assetType || call.ArgCount != 2 || !(call.Args[0] is string) ||
                call.Args[1] == null || !call.Args[1].GetType().IsEnum)
                return new ReturnMessage(new NotSupportedException("Unexpected asset repository call: " +
                    (method == null ? "unknown" : method.ToString())), call);
            try
            {
                string path = ((string)call.Args[0]).Replace('\\', '/');
                if (!path.StartsWith("Images/", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Non-image texture request: " + path);
                object asset;
                if (!cache.TryGetValue(path, out asset))
                {
                    string id = path.Substring("Images/".Length);
                    int[] size;
                    if (!dimensions.TryGetValue(id, out size)) size = new[] { 1, 1 };
                    object value = FormatterServices.GetUninitializedObject(texture);
                    Field(texture, "<Width>k__BackingField").SetValue(value, size[0]);
                    Field(texture, "<Height>k__BackingField").SetValue(value, size[1]);
                    GC.SuppressFinalize(value);
                    asset = Activator.CreateInstance(assetType, Any, null, new object[] { path }, CultureInfo.InvariantCulture);
                    Field(assetType, "<Value>k__BackingField").SetValue(asset, value);
                    FieldInfo state = Field(assetType, "<State>k__BackingField");
                    state.SetValue(asset, Enum.Parse(state.FieldType, "Loaded"));
                    cache.Add(path, asset);
                }
                return new ReturnMessage(asset, null, 0, call.LogicalCallContext, call);
            }
            catch (Exception ex) { return new ReturnMessage(ex, call); }
        }
    }
}
