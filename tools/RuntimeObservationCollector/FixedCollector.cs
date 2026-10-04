using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace RuntimeObservationCollector {
internal sealed partial class FixedCollector : IDisposable {
    private readonly string input, output;
    private Assembly game;
    private readonly Dictionary<string,MethodBase> methods=new Dictionary<string,MethodBase>(StringComparer.Ordinal);
    private readonly Dictionary<string,FieldInfo> fields=new Dictionary<string,FieldInfo>(StringComparer.Ordinal);
    private readonly Dictionary<string,string> pendingMemory=new Dictionary<string,string>(StringComparer.Ordinal);
    private readonly List<KeyValuePair<Assembly,string>> ownedMemory=new List<KeyValuePair<Assembly,string>>();
    private readonly SortedDictionary<string,object> loaded=PrimitiveJson.Object();
    private readonly List<object> stages=new List<object>();
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetDllDirectory(string path);
    internal FixedCollector(string inputPath,string outputPath) { input=inputPath; output=outputPath; }
    public void Dispose() {
        AppDomain.CurrentDomain.AssemblyResolve-=Resolve;
        AppDomain.CurrentDomain.AssemblyLoad-=OnLoad;
    }
    private static bool Framework(Assembly a) {
        string name=a.GetName().Name;
        string token=Program.Hex(a.GetName().GetPublicKeyToken());
        // XNA Core/Graphics declare this exact backward-compatibility assembly.
        // Its bytes belong to the externally accepted .NET image, not a guessed vendor pin.
        if (name=="Microsoft.VisualC") return a.GlobalAssemblyCache &&
            a.GetName().Version.ToString()=="10.0.0.0" && token=="b03f5f7f11d50a3a" &&
            String.IsNullOrEmpty(a.GetName().CultureInfo.Name);
        string[] names={"mscorlib","System","System.Core","System.Data","System.Xml","System.Xml.Linq","System.Drawing","System.Windows.Forms","System.Configuration","System.Numerics","System.Runtime.Serialization","WindowsBase","System.Security","System.Management"};
        return a.GlobalAssemblyCache && Array.IndexOf(names,name)>=0 &&
            (token=="b77a5c561934e089" || token=="b03f5f7f11d50a3a" || token=="31bf3856ad364e35");
    }
    private bool KnownMemory(Assembly a,out string hash) {
        foreach (KeyValuePair<Assembly,string> item in ownedMemory)
            if (Object.ReferenceEquals(a,item.Key)) { hash=item.Value; return true; }
        hash=null; return false;
    }
    private Assembly LoadVerifiedMemory(byte[] bytes,string name,string hash) {
        Program.VerifyHash(bytes,hash);
        Program.Need(!pendingMemory.ContainsKey(name),"REENTRANT_MEMORY_LOAD");
        pendingMemory.Add(name,hash);
        Assembly result;
        try { result=Assembly.Load(bytes); }
        finally { pendingMemory.Remove(name); }
        Program.Need(result.GetName().Name==name && String.IsNullOrEmpty(result.Location),"MEMORY_BINDING_IDENTITY_MISMATCH");
        ownedMemory.Add(new KeyValuePair<Assembly,string>(result,hash));
        CheckAssembly(result); return result;
    }
    private void CheckAssembly(Assembly a,bool allowPending=false) {
        if (a==typeof(FixedCollector).Assembly) return;
        string name=a.GetName().Name;
        if (Framework(a)) { loaded[name]=Program.HashFile(a.Location); return; }
        string expected;
        if (KnownMemory(a,out expected)) {
            Program.Need(String.IsNullOrEmpty(a.Location),"UNEXPECTED_DISK_BINDING"); loaded[name]=expected; return;
        }
        // AssemblyLoad fires before Load(bytes) returns its exact object. Pending
        // events are provisional and receive no hash receipt. Final enumeration
        // accepts only the exact object returned by our verified loader.
        if (allowPending && pendingMemory.ContainsKey(name)) {
            Program.Need(String.IsNullOrEmpty(a.Location),"UNEXPECTED_PENDING_DISK_BINDING"); return;
        }
        foreach (DependencyPin pin in FixedProfile.Dependencies) if (pin.Name==name && pin.Resource==null) {
            Program.Need(!String.IsNullOrEmpty(a.Location) && Program.HashFile(a.Location)==pin.Sha256,"DEPENDENCY_BINDING_HASH_MISMATCH");
            loaded[name]=pin.Sha256; return;
        }
        throw new InvalidDataException("UNAPPROVED_MANAGED_ASSEMBLY");
    }
    private void OnLoad(object sender,AssemblyLoadEventArgs args) { CheckAssembly(args.LoadedAssembly,true); }
    private Assembly Resolve(object sender,ResolveEventArgs args) {
        AssemblyName requested=new AssemblyName(args.Name);
        foreach (DependencyPin pin in FixedProfile.Dependencies) if (pin.Name==requested.Name) {
            Assembly result;
            if (pin.Resource==null) result=Assembly.LoadFrom(Path.Combine(input,pin.File));
            else {
                Program.Need(game!=null,"EMBEDDED_SOURCE_UNAVAILABLE");
                using (Stream stream=game.GetManifestResourceStream(pin.Resource)) {
                    Program.Need(stream!=null && stream.Length>0 && stream.Length<=16*1024*1024,"EMBEDDED_DEPENDENCY_BOUND");
                    byte[] bytes=new byte[(int)stream.Length]; int offset=0;
                    while (offset<bytes.Length) { int count=stream.Read(bytes,offset,bytes.Length-offset); Program.Need(count>0,"SHORT_EMBEDDED_READ"); offset+=count; }
                    result=LoadVerifiedMemory(bytes,pin.Name,pin.Sha256);
                }
            }
            Program.Need(result.FullName==requested.FullName,"DEPENDENCY_IDENTITY_MISMATCH");
            CheckAssembly(result); return result;
        }
        throw new InvalidDataException("UNAPPROVED_DEPENDENCY_REQUEST");
    }
    private void LoadPinned() {
        // AssemblyLoad checking is a binding-integrity diagnostic. It does not
        // make mixed-mode module startup safe; external OS isolation is required.
        Program.Need(SetDllDirectory(input),"DLL_SEARCH_PATH_FAILED");
        AppDomain.CurrentDomain.AssemblyResolve+=Resolve;
        AppDomain.CurrentDomain.AssemblyLoad+=OnLoad;
        foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies()) CheckAssembly(assembly);
        byte[] bytes=File.ReadAllBytes(Path.Combine(input,"Terraria.exe"));
        game=LoadVerifiedMemory(bytes,"Terraria",FixedProfile.GameSha256);
        Program.Need(game.GetName().Name=="Terraria" && game.GetName().Version.ToString()=="1.4.5.8","GAME_IDENTITY_MISMATCH");
        foreach (MethodPin pin in FixedProfile.Methods) {
            MethodBase method=game.ManifestModule.ResolveMethod(pin.Token);
            Program.Need(method.DeclaringType.FullName==pin.Owner && method.Name==pin.Name &&
                Program.Hash(game.ManifestModule.ResolveSignature(pin.Token))==pin.SignatureSha256 &&
                method.GetMethodBody()!=null && Program.Hash(method.GetMethodBody().GetILAsByteArray())==pin.IlSha256,"METHOD_PIN_MISMATCH");
            methods.Add(pin.Key,method);
        }
        foreach (FieldPin pin in FixedProfile.Fields) {
            FieldInfo field=game.ManifestModule.ResolveField(pin.Token);
            Program.Need(field.DeclaringType.FullName==pin.Owner && field.Name==pin.Name &&
                Program.Hex(game.ManifestModule.ResolveSignature(pin.Token))==pin.SignatureHex,"FIELD_PIN_MISMATCH");
            fields.Add(pin.Key,field);
        }
    }
    // Keys below are compile-time literals or arrays frozen in FixedProfile.cs.
    // No input file or command-line string chooses a type, member, token or argument.
    private object Call(string key,object target,params object[] args) {
        MethodBase method=methods[key]; ConstructorInfo constructor=method as ConstructorInfo;
        if (constructor!=null) return constructor.Invoke(args);
        return method.Invoke(target,args);
    }
    private void Stage(string key,object target,params object[] args) { Console.Error.WriteLine("STAGE: "+key); Call(key,target,args); stages.Add(key); }
    private object Read(string key,object target=null) { return fields[key].GetValue(target); }
    private void Set(string key,object value) { fields[key].SetValue(null,value); }
    private int Integer(string key,object target=null) { return (int)PrimitiveJson.Scalar(Read(key,target)); }
    private void Cctor(string key) { Console.Error.WriteLine("STAGE: "+key); RuntimeHelpers.RunClassConstructor(methods[key].DeclaringType.TypeHandle); stages.Add(key); }
    private Array ArrayField(string key,Type element,int count) {
        Array value=Read(key) as Array;
        Program.Need(value!=null && value.Rank==1 && value.GetLowerBound(0)==0 && value.Length==count && value.GetType().GetElementType()==element,"REGISTRY_DOMAIN_MISMATCH");
        return value;
    }
    private void Initialize() {
        Cctor("programCctor"); Set("program.SavePath",output);
        // Setting dedServ itself may trigger Main.cctor. Run it explicitly and
        // record the boundary; dedServ cannot suppress that earlier initializer.
        Cctor("mainCctor"); Set("main.dedServ",true); Set("main.netMode",2);
        Set("main.rand",Call("randomCtor",null,0));
        object language=Read("language.Instance"); Stage("language",language,"zh-Hans"); Stage("legacyLanguage",null);
        Array players=Read("main.player") as Array; int localPlayer=Integer("main.myPlayer");
        Program.Need(players!=null && players.Length<=256 && localPlayer>=0 && localPlayer<players.Length,"PLAYER_BOOTSTRAP_DOMAIN");
        players.SetValue(Call("playerCtor",null),localPlayer); stages.Add("bootstrapPlayer");
        Stage("samples",null);
        Stage("tiles",null); Stage("researchInit",Read("research.Instance"));
        Stage("tileData1",null); Stage("tileData2",null); Stage("itemsInit",null);
        int count=Integer("projectileId.Count"); Program.Need(count>1 && count<=65536,"PROJECTILE_COUNT_BOUND");
        bool[] hostile=(bool[])ArrayField("main.projHostile",typeof(bool),count);
        bool[] hook=(bool[])ArrayField("main.projHook",typeof(bool),count);
        for (int id=1;id<count;id++) {
            object projectile=Call("projectileCtor",null); Call("projectileDefaults",projectile,id);
            if ((bool)Read("projectile.hostile",projectile)) hostile[id]=true;
            if (Integer("projectile.aiStyle",projectile)==7) hook[id]=true;
        }
        stages.Add("projectileFlagsSourceLoop");
        Stage("recipeGroups",null); Stage("armorSets",null); Stage("armorLookup",null);
        Stage("itemPost",null); Stage("tilePost",null);
        // Only the two item-relevant subcalls of DyeInitializer.Load: LoadMisc
        // does not feed the observed Item dye/hairDye registries.
        Stage("armorDyes",null); Stage("hairDyes",null); Stage("dyeIds",null);
        int recipes=Integer("recipe.maxRecipes"); Array array=Read("main.recipe") as Array;
        Program.Need(recipes>0 && recipes<=65536 && array!=null && array.Length==recipes,"RECIPE_ALLOCATION_BOUND");
        for (int i=0;i<recipes;i++) array.SetValue(Call("recipeCtor",null),i);
        stages.Add("recipeAllocationSourceLoop"); Stage("recipes",null); Stage("fixItems",null);
        Cctor("groupsCctor"); Cctor("poolsCctor");
    }
    private object NativeReceipt() {
        SortedDictionary<string,object> modules=PrimitiveJson.Object();
        string windows=Path.GetFullPath(Environment.GetFolderPath(Environment.SpecialFolder.Windows)).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
        string executable=Path.GetFullPath(typeof(FixedCollector).Assembly.Location);
        using (Process process=Process.GetCurrentProcess()) foreach (ProcessModule module in process.Modules) {
            string path=Path.GetFullPath(module.FileName); string name=Path.GetFileName(path);
            string actual=Program.HashFile(path); bool vendor=false;
            foreach (DependencyPin pin in FixedProfile.NativeDependencies) if (String.Equals(name,pin.File,StringComparison.OrdinalIgnoreCase)) {
                Program.Need(actual==pin.Sha256,"NATIVE_BINDING_HASH_MISMATCH"); vendor=true;
            }
            foreach (DependencyPin pin in FixedProfile.Dependencies) if (pin.Resource==null && String.Equals(name,pin.File,StringComparison.OrdinalIgnoreCase)) {
                Program.Need(actual==pin.Sha256,"MIXED_MODE_BINDING_HASH_MISMATCH"); vendor=true;
            }
            Program.Need(vendor || String.Equals(path,executable,StringComparison.OrdinalIgnoreCase) || path.StartsWith(windows,StringComparison.OrdinalIgnoreCase),"UNAPPROVED_NATIVE_MODULE_PATH");
            // System/CLR hashes document the observed baseline, not an approved
            // platform pin. Independent OS-image acceptance is still required.
            if (!modules.ContainsKey(name)) modules.Add(name,actual);
            else Program.Need((string)modules[name]==actual,"AMBIGUOUS_NATIVE_MODULE_NAME");
            Program.Need(modules.Count<=256,"NATIVE_MODULE_COUNT_BOUND");
        }
        return modules;
    }
    internal object Observe() {
        LoadPinned(); Initialize();
        int count=Integer("itemId.Count"); Program.Need(count==6196,"PINNED_ITEM_COUNT_MISMATCH");
        int prefixCount=Integer("prefixId.Count"); Program.Need(prefixCount>1 && prefixCount<=256,"PREFIX_COUNT_BOUND");
        IDictionary samples=Read("samples.ItemsByType") as IDictionary;
        IDictionary identities=Read("samples.ItemPersistentIdsByNetIds") as IDictionary;
        Program.Need(samples!=null && identities!=null && samples.Count<=65536 && identities.Count<=65536,"SAMPLE_DICTIONARY_BOUND");
        object[] rows=new object[count-1]; object catalog=Read("research.Instance");
        for (int id=1;id<count;id++) {
            Program.Need(samples.Contains(id),"MISSING_CONTENT_SAMPLE"); object item=samples[id];
            Program.Need(item!=null && item.GetType().FullName=="Terraria.Item","INVALID_SAMPLE_TYPE");
            SortedDictionary<string,object> gameplay=PrimitiveJson.Object();
            foreach (string name in FixedProfile.ItemFields) gameplay.Add(name,PrimitiveJson.Scalar(Read("item."+name,item)));
            object[] capArgs={id,0}; bool researchPresent=(bool)Call("researchCap",catalog,capArgs);
            bool variantIsNull=Call("itemVariant",item)==null;
            Program.Need(variantIsNull,"UNEXPECTED_ITEM_VARIANT");
            object nameValue=Call("itemName",item);
            Program.Need(nameValue==null || nameValue.GetType()==typeof(string),"INVALID_NAME_TYPE");
            bool persistentPresent=identities.Contains(id); object persistent=persistentPresent?identities[id]:null;
            Program.Need(persistent==null || persistent.GetType()==typeof(string),"INVALID_PERSISTENT_ID_TYPE");
            rows[id-1]=PrimitiveJson.Object("requestedId",id,"variantIsNull",variantIsNull,"resolvedType",Integer("identity.type",item),"name",nameValue,
                "persistentIdPresent",persistentPresent,"persistentId",persistent,
                "research",PrimitiveJson.Object("present",researchPresent,"count",(int)capArgs[1]),"gameplay",gameplay);
        }
        SortedDictionary<string,object> groups=PrimitiveJson.Object(),sets=PrimitiveJson.Object(),priorities=PrimitiveJson.Object(),pools=PrimitiveJson.Object();
        foreach (FieldPin pin in FixedProfile.Fields) {
            if (pin.Key.StartsWith("groups.",StringComparison.Ordinal)) groups.Add(pin.Name,(bool[])ArrayField(pin.Key,typeof(bool),count).Clone());
            if (pin.Key.StartsWith("sets.",StringComparison.Ordinal)) sets.Add(pin.Name,(bool[])ArrayField(pin.Key,typeof(bool),count).Clone());
            if (pin.Key.StartsWith("priorities.",StringComparison.Ordinal)) priorities.Add(pin.Name,(int[])ArrayField(pin.Key,typeof(int),count).Clone());
            if (pin.Key.StartsWith("pools.",StringComparison.Ordinal)) {
                Array source=Read(pin.Key) as Array;
                Program.Need(source!=null && source.Rank==1 && source.GetLowerBound(0)==0 && source.Length>0 && source.Length<=256,"PREFIX_POOL_BOUND");
                int[] values=new int[source.Length];
                for (int i=0;i<values.Length;i++) { values[i]=(int)PrimitiveJson.Scalar(source.GetValue(i)); Program.Need(values[i]>0 && values[i]<prefixCount,"PREFIX_POOL_ID_BOUND"); }
                pools.Add(pin.Name,values);
            }
        }
        SortedDictionary<string,object> world=PrimitiveJson.Object();
        foreach (string field in new string[]{"drunkWorld","getGoodWorld","tenthAnniversaryWorld","dontStarveWorld","notTheBeesWorld","remixWorld","noTrapsWorld","zenithWorld","skyblockWorld","infectedSeed"}) { object flag=Read("main."+field); Program.Need(flag is bool && !(bool)flag,"UNEXPECTED_WORLD_VARIANT"); world.Add(field,flag); }
        int gameMode=(int)Call("gameMode",null);
        bool expert=(bool)Call("expertMode",null),master=(bool)Call("masterMode",null),mechdusa=(bool)Call("mechdusa",null);
        Program.Need(gameMode==0 && !expert && !master && !mechdusa,"UNEXPECTED_GAME_MODE");
        object difficultyOverride=Read("main._gameModeDifficultyOverride");
        Program.Need(difficultyOverride==null,"UNEXPECTED_DIFFICULTY_OVERRIDE");
        object actualCulture=Call("activeCulture",Read("language.Instance"));
        string culture=(string)Call("cultureName",actualCulture);
        Program.Need(culture=="zh-Hans","UNEXPECTED_CULTURE");
        object context=PrimitiveJson.Object("gameMode",gameMode,"difficulty",PrimitiveJson.Scalar(Call("difficulty",null)),
            "expertMode",expert,"masterMode",master,"mechdusa",mechdusa,
            "activeWorldFileDataPresent",Read("main.ActiveWorldFileData")!=null,"difficultyOverride",difficultyOverride,
            "dedServ",Read("main.dedServ"),"netMode",Integer("main.netMode"),"localPlayerIndex",Integer("main.myPlayer"));
        object playerObservation=CapturePlayerObservation(rows);
        object[] prefixNames=CapturePrefixNames(prefixCount);
        object materialObservation=CaptureMaterialObservation(samples,count,culture,context);
        object mapObservation=CaptureMapObservation(culture,context);
        foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies()) CheckAssembly(assembly);
        return PrimitiveJson.Object("schemaVersion",2,"kind","pinned-consumer-data-observation-fragment","status","PARTIAL",
            "gameVersion","1.4.5.8","culture",culture,"context",context,"sourceSha256",FixedProfile.GameSha256,"itemCount",count,"prefixCount",prefixCount,
            "playerObservation",playerObservation,"materialObservation",materialObservation,"mapObservation",mapObservation,"prefixNames",prefixNames,"records",rows,"groups",groups,"itemSets",sets,"priorities",priorities,"pools",pools,
            "initializersReturned",stages.ToArray(),"worldFlags",world,"managedAssemblyHashes",loaded,
            "nativeModuleHashes",NativeReceipt(),"randomSeed",0,"collectorExecutableSha256",Program.HashFile(typeof(FixedCollector).Assembly.Location),
            "osVersion",Environment.OSVersion.VersionString,"clrVersion",Environment.Version.ToString(),
            "requestedExecutionMode","isolated-windows-x86-clr4-dedServ","isolationVerified",false,"initializationVerified",false,
            "sourceSemanticsVerified",false,"complete",false,"publishable",false,
            "missingJoins",new object[]{"selected-item-domain","priority-override-provenance","prefix-facts","initialization-acceptance"});
    }
}}
