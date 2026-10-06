// Opt-in fixture adapter. All expected colors/tiles and player loading are
// evaluated by the supplied, hashed official assembly, not copied formulas.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

internal static class GameOracleProbe
{
    const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    static Assembly game;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    static Type T(string name) { return game.GetType(name, true); }
    static FieldInfo F(Type type, string name) { return type.GetField(name, Any) ?? throw new MissingFieldException(type.FullName, name); }
    static object Get(object target, string name) {
        Type type = target as Type ?? target.GetType(); object instance = target is Type ? null : target;
        var field = type.GetField(name, Any); return field != null ? field.GetValue(instance) : type.GetProperty(name, Any).GetValue(instance, null);
    }
    static void Set(Type type, string name, object value) {
        var field = type.GetField(name, Any);
        if (field != null) field.SetValue(null, value); else type.GetProperty(name, Any).SetValue(null, value, null);
    }
    static object Call(object target, string name, params object[] args) {
        Type type = target as Type ?? target.GetType(); object instance = target is Type ? null : target;
        var methods = type.GetMethods(Any).Where(m => m.Name == name && m.GetParameters().Length == args.Length).ToArray();
        var method = methods.Single(m => m.GetParameters().Select((p,i) => args[i] == null || p.ParameterType.IsInstanceOfType(args[i])).All(v => v));
        return method.Invoke(instance, args);
    }
    static string Hash(byte[] bytes) { using (var h = SHA256.Create()) return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static int Number(Dictionary<string, object> row, string key, int fallback = 0) { return row.ContainsKey(key) ? Convert.ToInt32(row[key]) : fallback; }
    static double Real(Dictionary<string, object> row, string key, double fallback) { return row.ContainsKey(key) ? Convert.ToDouble(row[key]) : fallback; }
    static bool Flag(Dictionary<string, object> row, string key) { return row.ContainsKey(key) && Convert.ToBoolean(row[key]); }
    static object[] Rows(Dictionary<string, object> input) { return ((IEnumerable)input["rows"]).Cast<object>().ToArray(); }
    static string Fixture(string inputPath, string relative) {
        string root = Path.GetFullPath(Path.GetDirectoryName(inputPath)) + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(root, relative));
        if (!path.StartsWith(root, StringComparison.Ordinal) || !File.Exists(path)) throw new InvalidDataException("fixture escapes input directory or is missing");
        return path;
    }
    static void World(Dictionary<string, object> input) {
        Type main = T("Terraria.Main"); int width = Number(input,"width",128), height=Number(input,"height",512);
        if(width<1 || width>512 || height<256 || height>2048) throw new InvalidDataException("bounded oracle world dimensions required");
        Set(main,"maxTilesX",width); Set(main,"maxTilesY",height);
        Set(main,"worldSurface",Real(input,"ground",120)); Set(main,"rockLayer",Real(input,"rock",240));
        Set(main,"worldName", input.ContainsKey("worldName") ? (string)input["worldName"] : "Oracle");
        Set(main,"worldID",Number(input,"worldId",123)); Set(main,"remixWorld",false);
        Set(T("Terraria.WorldGen"),"generatingWorld",true);
        Set(main,"tile",Array.CreateInstance(T("Terraria.Tile"),width,height));
        Set(main,"Map",Activator.CreateInstance(T("Terraria.Map.WorldMap"),width,height));
    }
    static object Tile(Dictionary<string,object> row) {
        object tile=Activator.CreateInstance(T("Terraria.Tile"));
        foreach (var pair in new[]{new[]{"type","type"},new[]{"wall","wall"},new[]{"frameX","frameX"},new[]{"frameY","frameY"},new[]{"liquidAmount","liquid"}}) {
            var f=F(tile.GetType(),pair[1]);f.SetValue(tile,Convert.ChangeType(Number(row,pair[0]),f.FieldType));
        }
        Call(tile,"active",Flag(row,"active"));Call(tile,"color",(byte)Number(row,"tileColor"));Call(tile,"wallColor",(byte)Number(row,"wallColor"));
        if(Number(row,"liquidAmount")>0) Call(tile,"liquidType",Number(row,"liquidType",1)-1);
        foreach(string key in new[]{"invisibleBlock","invisibleWall","fullbrightBlock","fullbrightWall"}) Call(tile,key,Flag(row,key));
        return tile;
    }
    static object ColorRows(Dictionary<string,object> input, bool rle) {
        World(input);var main=T("Terraria.Main");var tiles=(Array)Get(main,"tile");var result=new List<int[]>();
        var rows=Rows(input);int count=rle?Number(input,"height",512):rows.Length;
        if(count>65536)throw new InvalidDataException("too many oracle rows");
        for(int n=0;n<count;n++) {
            var row=(Dictionary<string,object>)rows[rle?0:n];int x=Number(row,"x",0),y=rle?n:Number(row,"y",0);
            object tile=Flag(row,"nullTile")?null:Tile(row);tiles.SetValue(tile,x,y);
            object mapTile=Call(T("Terraria.Map.MapHelper"),"CreateMapTile",x,y,(byte)255,0);
            object color=Call(T("Terraria.Map.MapHelper"),"GetMapTileXnaColor",mapTile,x,y);
            result.Add(new[]{Convert.ToInt32(Get(color,"R")),Convert.ToInt32(Get(color,"G")),Convert.ToInt32(Get(color,"B")),Convert.ToInt32(Get(color,"A"))});
        }
        return result;
    }
    static object MapHeader(Dictionary<string,object> input, string scratch) {
        World(input);Type main=T("Terraria.Main"),helper=T("Terraria.Map.MapHelper");
        string player=Path.Combine(scratch,"oracle.plr");
        Set(main,"playerPathName",player);Set(main,"gameMenu",true);
        Set(main,"ActivePlayerFileData",Activator.CreateInstance(T("Terraria.IO.PlayerFileData"),player,false));
        object world=Activator.CreateInstance(T("Terraria.IO.WorldFileData"),Path.Combine(scratch,"oracle.wld"),false);
        F(world.GetType(),"WorldId").SetValue(world,Number(input,"worldId",123));Set(main,"ActiveWorldFileData",world);
        object metadata=Call(T("Terraria.IO.FileMetadata"),"FromCurrentSettings",Enum.Parse(T("Terraria.IO.FileType"),"Map"));
        Set(main,"MapFileMetadata",metadata);
        // Invoke the real writer directly, so it cannot swallow errors in SaveMap.
        Call(helper,"InternalSaveMap");
        string file=Path.Combine(scratch,"oracle",Get(world,"MapFileName")+".map");
        using(var reader=new BinaryReader(File.OpenRead(file))) {
            int format=reader.ReadInt32();Call(T("Terraria.IO.FileMetadata"),"Read",reader,Enum.Parse(T("Terraria.IO.FileType"),"Map"));
            string name=reader.ReadString();int id=reader.ReadInt32(),height=reader.ReadInt32(),width=reader.ReadInt32();
            int tiles=reader.ReadInt16(),walls=reader.ReadInt16();var gradients=new int[4];for(int i=0;i<4;i++)gradients[i]=reader.ReadInt16();
            byte[] tileBits=reader.ReadBytes((tiles+7)/8),wallBits=reader.ReadBytes((walls+7)/8);
            var counts=new List<int[]>();
            foreach(var shape in new[]{new object[]{tiles,tileBits},new object[]{walls,wallBits}}) {
                int length=(int)shape[0];byte[] bits=(byte[])shape[1];var values=new int[length];
                for(int i=0;i<length;i++)values[i]=(bits[i/8]&(1<<(i%8)))!=0?reader.ReadByte():1;counts.Add(values);
            }
            Console.Error.WriteLine("Actual game MAP wire format="+format+"; revision="+Get(Get(main,"MapFileMetadata"),"Revision")+"; file="+file);
            return new{worldName=name,worldId=id,width=width,height=height,gradients=gradients,tileOptions=counts[0],wallOptions=counts[1]};
        }
    }
    static object PlayerFields(string filename) {
        object file=Call(T("Terraria.Player"),"LoadPlayer",filename,false),player=Get(file,"Player");
        if(Convert.ToInt32(Get(player,"loadStatus")) != Convert.ToInt32(Get(T("Terraria.ID.StatusID"),"Ok")))throw new InvalidDataException("game rejected player fixture: "+Get(player,"loadStatus"));
        var output=new Dictionary<string,object>();
        foreach(string name in new[]{"name","difficulty","hair","hairDye","skinVariant","statLife","statLifeMax","statMana","statManaMax","extraAccessory","taxMoney"})output[name]=Get(player,name);
        foreach(var pair in new[]{new[]{"inventory","inventory"},new[]{"armor","armor"},new[]{"dyes","dye"},new[]{"miscEquips","miscEquips"},new[]{"miscDyes","miscDyes"},new[]{"piggyBank","bank"},new[]{"safe","bank2"},new[]{"defendersForge","bank3"},new[]{"voidVault","bank4"}}) {
            object value=Get(player,pair[1]);var items=value as Array ?? (Array)Get(value,"item");
            var rows=new List<object>();
            // Slot 58 is the transient mouse slot; the real Serialize method saves 0..57.
            var savedItems=items.Cast<object>().Take(pair[0]=="inventory"?58:items.Length);
            foreach(object item in savedItems)rows.Add(new{itemType=Convert.ToInt32(Get(item,"type")),stack=Convert.ToInt32(Get(item,"stack")),prefix=Convert.ToInt32(Get(item,"prefix")),favorited=Convert.ToBoolean(Get(item,"favorited"))});
            output[pair[0]]=rows;
        }
        return output;
    }
    public static int Run(string[] args) {
        try {
            if(args.Length%2!=0)throw new ArgumentException("oracle expects explicit option/value pairs");
            var options=new Dictionary<string,string>();for(int i=0;i<args.Length;i+=2)options.Add(args[i],args[i+1]);
            string assembly=Path.GetFullPath(options["--assembly"]),inputPath=Path.GetFullPath(options["--input"]),scratch=Path.GetFullPath(options["--scratch"]);
            if(Directory.Exists(scratch))throw new IOException("oracle scratch must be new");Directory.CreateDirectory(scratch);
            AppDomain.CurrentDomain.AssemblyResolve+=(sender,ev)=>{
                string name=new AssemblyName(ev.Name).Name;if(name.IndexOfAny(new[]{'/','\\'})>=0)return null;
                string sibling=Path.Combine(Path.GetDirectoryName(assembly),name+".dll");if(File.Exists(sibling))return Assembly.LoadFrom(sibling);
                if(game==null)return null;string resource=game.GetManifestResourceNames().FirstOrDefault(n=>n.EndsWith(name+".dll",StringComparison.OrdinalIgnoreCase));
                if(resource==null)return null;using(var stream=game.GetManifestResourceStream(resource))using(var bytes=new MemoryStream()){stream.CopyTo(bytes);return Assembly.Load(bytes.ToArray());}
            };
            byte[] inputBytes=File.ReadAllBytes(inputPath);var input=Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(inputBytes));
            game=Assembly.LoadFrom(assembly);Program.BootstrapOracle(game,scratch);
            object result;string category=options["--category"];
            if(category=="map-header")result=MapHeader(input,scratch);
            else if(category=="map-color-paint"||category=="missing-tile"||category=="background-rle")result=ColorRows(input,category=="background-rle");
            else if(category=="player-conversion")result=new {original=PlayerFields(Fixture(inputPath,(string)input["original"])),converted=PlayerFields(Fixture(inputPath,(string)input["converted"]))};
            else throw new InvalidDataException("unknown oracle category");
            Console.WriteLine(Json.Serialize(new{side="game",inputSha256=Hash(inputBytes),gameAssemblySha256=Hash(File.ReadAllBytes(assembly)),entryPoint=category=="map-header"?"MapHelper.InternalSaveMap":category=="player-conversion"?"Player.LoadPlayer":"MapHelper.CreateMapTile+GetMapTileXnaColor",result=result}));return 0;
        }catch(Exception error){Console.Error.WriteLine(error);return 1;}
    }
}
