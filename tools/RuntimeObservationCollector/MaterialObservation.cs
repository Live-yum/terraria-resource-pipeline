using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.CompilerServices;

namespace RuntimeObservationCollector {
// Only original primitive records pass this seam. No generic object serializer,
// externally selected members, equality overrides, delegates or recursive walk.
internal sealed class MaterialNodeSnapshot {
    internal object[] SubTiles, Alternates;
    internal bool LinkedAlternates, StyleHorizontal, HasGetStyleOverride;
    internal int Width, Height, CoordinateWidth, CoordinatePadding, PaddingFixX, PaddingFixY;
    internal int Style, StyleMultiplier, StyleWrapLimit, StyleLineSkip, RandomStyleRange;
    internal int[] CoordinateHeights, SpecificRandomStyles;
}
internal abstract class MaterialNodeReader {
    internal abstract void Validate(object node);
    internal abstract MaterialNodeSnapshot Capture(object node);
}
internal sealed class MaterialGraphLimits {
    internal readonly int Nodes, ArrayEntries, Edges, CoordinateValues, RandomValues;
    internal MaterialGraphLimits() : this(65536,65536,1000000,1000000,1000000) {}
    internal MaterialGraphLimits(int nodes,int arrayEntries,int edges,int coordinateValues,int randomValues) {
        Program.Need(nodes>0 && nodes<=65536 && arrayEntries>0 && arrayEntries<=65536 &&
            edges>0 && edges<=1000000 && coordinateValues>0 && coordinateValues<=1000000 &&
            randomValues>0 && randomValues<=1000000,"MATERIAL_LIMIT_CONFIGURATION");
        Nodes=nodes; ArrayEntries=arrayEntries; Edges=edges;
        CoordinateValues=coordinateValues; RandomValues=randomValues;
    }
}
internal sealed class MaterialGraph {
    private sealed class IdentityComparer : IEqualityComparer<object> {
        public new bool Equals(object left,object right) { return Object.ReferenceEquals(left,right); }
        public int GetHashCode(object value) { return RuntimeHelpers.GetHashCode(value); }
    }
    private readonly MaterialNodeReader reader;
    private readonly MaterialGraphLimits limits;
    private readonly Dictionary<object,int> ids=new Dictionary<object,int>(new IdentityComparer());
    private readonly List<object> queue=new List<object>();
    private int edges,coordinateValues,randomValues;
    private MaterialGraph(MaterialNodeReader reader,MaterialGraphLimits limits) {
        Program.Need(reader!=null && limits!=null,"MATERIAL_GRAPH_ARGUMENT"); this.reader=reader; this.limits=limits;
    }
    private static void Consume(ref int used,int count,int maximum,string reason) {
        Program.Need(count>=0 && count<=maximum-used,reason); used+=count;
    }
    private object Intern(object node) {
        if (node==null) return null;
        int id;
        if (ids.TryGetValue(node,out id)) return id;
        Program.Need(ids.Count<limits.Nodes,"MATERIAL_NODE_LIMIT");
        // Runtime reader rejects unsupported exact types and getter recursion
        // before any pinned getter runs. Original fixtures use this same seam.
        reader.Validate(node);
        id=queue.Count; ids.Add(node,id); queue.Add(node); return id;
    }
    private object[] References(object[] children) {
        if (children==null) return null;
        Program.Need(children.Length<=limits.ArrayEntries,"MATERIAL_CHILD_ARRAY_LIMIT");
        Consume(ref edges,children.Length,limits.Edges,"MATERIAL_GRAPH_EDGE_LIMIT");
        object[] result=new object[children.Length];
        for (int i=0;i<children.Length;i++) result[i]=Intern(children[i]);
        return result;
    }
    private int[] Integers(int[] values,bool coordinates) {
        if (values==null) return null;
        Program.Need(values.Length<=limits.ArrayEntries,"MATERIAL_INTEGER_ARRAY_LIMIT");
        if (coordinates) Consume(ref coordinateValues,values.Length,limits.CoordinateValues,"MATERIAL_COORDINATE_VALUE_LIMIT");
        else Consume(ref randomValues,values.Length,limits.RandomValues,"MATERIAL_RANDOM_VALUE_LIMIT");
        return (int[])values.Clone(); // Preserve signs, duplicates, order and empty versus null.
    }
    internal static SortedDictionary<string,object> Capture(object[] registry,int tileCount,MaterialNodeReader reader,MaterialGraphLimits limits) {
        Program.Need(registry!=null && tileCount>0 && tileCount<=65536 && registry.Length<=tileCount,"MATERIAL_REGISTRY_DOMAIN");
        MaterialGraph graph=new MaterialGraph(reader,limits);
        Program.Need(registry.Length<=limits.ArrayEntries,"MATERIAL_REGISTRY_LIMIT");
        Consume(ref graph.edges,registry.Length,limits.Edges,"MATERIAL_GRAPH_EDGE_LIMIT");
        object[] roots=new object[tileCount];
        for (int id=0;id<tileCount;id++) roots[id]=PrimitiveJson.Object("tileId",id,
            "registrationPresent",id<registry.Length,"nodeId",id<registry.Length?graph.Intern(registry[id]):null);
        List<object> records=new List<object>();
        for (int index=0;index<graph.queue.Count;index++) {
            MaterialNodeSnapshot s=reader.Capture(graph.queue[index]);
            Program.Need(s!=null,"MISSING_MATERIAL_NODE_SNAPSHOT");
            object[] subTiles=graph.References(s.SubTiles),alternates=graph.References(s.Alternates);
            int[] heights=graph.Integers(s.CoordinateHeights,true),random=graph.Integers(s.SpecificRandomStyles,false);
            records.Add(PrimitiveJson.Object("nodeId",index,"subTiles",subTiles,"alternates",alternates,
                "linkedAlternates",s.LinkedAlternates,"width",s.Width,"height",s.Height,
                "coordinateWidth",s.CoordinateWidth,"coordinateHeights",heights,"coordinatePadding",s.CoordinatePadding,
                "paddingFixX",s.PaddingFixX,"paddingFixY",s.PaddingFixY,"style",s.Style,"styleMultiplier",s.StyleMultiplier,
                "styleHorizontal",s.StyleHorizontal,"styleWrapLimit",s.StyleWrapLimit,"styleLineSkip",s.StyleLineSkip,
                "randomStyleRange",s.RandomStyleRange,"specificRandomStyles",random,"hasGetStyleOverride",s.HasGetStyleOverride));
        }
        return PrimitiveJson.Object("tileRoots",roots,"nodes",records.ToArray());
    }
}

internal sealed partial class FixedCollector {
    private sealed class PinnedMaterialReader : MaterialNodeReader {
        private readonly FixedCollector owner;
        private readonly Type nodeType,listType;
        private readonly object baseObject;
        // These fields guard the only recursive paths of the 17 fixed getters.
        // They are compile-time profile keys, never observation-input selectors.
        private static readonly string[] Modules={"material._alternates","material._placementHooks","material._subTiles",
            "material._tileObjectStyle","material._tileObjectBase","material._tileObjectCoords"};
        internal PinnedMaterialReader(FixedCollector owner) {
            this.owner=owner;
            nodeType=owner.fields["material.baseObject"].FieldType;
            listType=owner.fields["material.registry"].FieldType;
            Program.Need(nodeType.FullName=="Terraria.ObjectData.TileObjectData" && Object.ReferenceEquals(nodeType.Assembly,owner.game),"MATERIAL_NODE_ASSEMBLY_MISMATCH");
            Program.Need(listType.IsGenericType && listType.GetGenericTypeDefinition()==typeof(List<>) &&
                listType.GetGenericArguments().Length==1 && listType.GetGenericArguments()[0]==nodeType,"MATERIAL_LIST_TYPE_MISMATCH");
            baseObject=owner.Read("material.baseObject"); ExactNode(baseObject);
            foreach (string key in Modules) Module(key,baseObject,true);
        }
        private void ExactNode(object node) {
            Program.Need(node!=null && node.GetType()==nodeType,"MATERIAL_NODE_TYPE_MISMATCH");
        }
        private void Module(string key,object node,bool required) {
            object value=owner.Read(key,node);
            Program.Need((value!=null || !required) && (value==null || value.GetType()==owner.fields[key].FieldType),"MATERIAL_GETTER_FALLBACK_UNSUPPORTED");
        }
        internal override void Validate(object node) {
            ExactNode(node);
            Program.Need(Object.ReferenceEquals(baseObject,owner.Read("material.baseObject")),"MATERIAL_BASE_OBJECT_CHANGED");
            foreach (string key in Modules) Module(key,node,key=="material._tileObjectStyle");
            // Pinned get_StyleHorizontal calls itself on the same node when its
            // style module is null. Never invoke that branch or invent a default.
        }
        internal object[] CopyList(object value,bool nullable) {
            if (value==null) { Program.Need(nullable,"MISSING_MATERIAL_REGISTRY"); return null; }
            Program.Need(value.GetType()==listType,"MATERIAL_LIST_TYPE_MISMATCH");
            IList source=(IList)value;
            Program.Need(source.Count>=0 && source.Count<=65536,"MATERIAL_LIST_LIMIT");
            object[] copy=new object[source.Count];
            for (int i=0;i<copy.Length;i++) { object node=source[i]; if (node!=null) ExactNode(node); copy[i]=node; }
            Program.Need(source.Count==copy.Length,"MATERIAL_LIST_CHANGED"); return copy;
        }
        private static int Int(object value) {
            Program.Need(value!=null && value.GetType()==typeof(int),"MATERIAL_INTEGER_TYPE_MISMATCH"); return (int)value;
        }
        private static bool Bool(object value) {
            Program.Need(value!=null && value.GetType()==typeof(bool),"MATERIAL_BOOLEAN_TYPE_MISMATCH"); return (bool)value;
        }
        private static int[] IntArray(object value) {
            Program.Need(value==null || value.GetType()==typeof(int[]),"MATERIAL_INTEGER_ARRAY_TYPE_MISMATCH");
            int[] source=(int[])value;
            Program.Need(source==null || source.Length<=65536,"MATERIAL_INTEGER_ARRAY_LIMIT");
            return source; // Graph immediately bounds and copies; never serializes the source object.
        }
        internal override MaterialNodeSnapshot Capture(object node) {
            Validate(node);
            MaterialNodeSnapshot s=new MaterialNodeSnapshot();
            s.SubTiles=CopyList(owner.Call("materialSubTiles",node),true);
            s.Alternates=CopyList(owner.Call("materialAlternates",node),true);
            s.LinkedAlternates=Bool(owner.Call("materialLinkedAlternates",node));
            s.Width=Int(owner.Call("materialWidth",node)); s.Height=Int(owner.Call("materialHeight",node));
            s.CoordinateWidth=Int(owner.Call("materialCoordinateWidth",node));
            s.CoordinateHeights=IntArray(owner.Call("materialCoordinateHeights",node));
            s.CoordinatePadding=Int(owner.Call("materialCoordinatePadding",node));
            object fix=owner.Call("materialCoordinatePaddingFix",node);
            Program.Need(fix!=null && fix.GetType()==owner.fields["material.point16X"].DeclaringType &&
                fix.GetType()==owner.fields["material.point16Y"].DeclaringType && Object.ReferenceEquals(fix.GetType().Assembly,owner.game),"MATERIAL_PADDING_FIX_TYPE_MISMATCH");
            s.PaddingFixX=(int)PrimitiveJson.Scalar(owner.Read("material.point16X",fix));
            s.PaddingFixY=(int)PrimitiveJson.Scalar(owner.Read("material.point16Y",fix));
            s.Style=Int(owner.Call("materialStyle",node));
            s.StyleMultiplier=Int(owner.Call("materialStyleMultiplier",node));
            s.StyleHorizontal=Bool(owner.Call("materialStyleHorizontal",node));
            s.StyleWrapLimit=Int(owner.Call("materialStyleWrapLimit",node));
            s.StyleLineSkip=Int(owner.Call("materialStyleLineSkip",node));
            s.RandomStyleRange=Int(owner.Call("materialRandomStyleRange",node));
            s.SpecificRandomStyles=IntArray(owner.Call("materialSpecificRandomStyles",node));
            s.HasGetStyleOverride=owner.Call("materialGetStyleOverride",node)!=null;
            return s;
        }
    }
    private object CaptureMaterialObservation(IDictionary samples,int itemCount,string culture,object context) {
        Console.Error.WriteLine("STAGE: materialObservation");
        Program.Need(samples!=null && samples.Count<=65536 && itemCount>1 && itemCount<=65536,"MATERIAL_ITEM_DOMAIN");
        Program.Need(culture=="zh-Hans" && (bool)Read("main.dedServ"),"MATERIAL_CONTEXT_CHANGED");
        Program.Need((string)Call("cultureName",Call("activeCulture",Read("language.Instance")))==culture,"MATERIAL_CULTURE_CHANGED");
        int tileCount=Integer("tileId.Count");
        // Independent data-only Count proof pins 754; this is not a flag-count heuristic.
        Program.Need(tileCount==754,"PINNED_MATERIAL_TILE_COUNT_MISMATCH");
        bool[] frameImportant=(bool[])ArrayField("material.frameImportant",typeof(bool),tileCount).Clone();
        // Fresh source registries consumed by the existing pixel selection policy.
        // Preserve source names and clone the entire exact tile domain; no new classifications.
        object pixelFlags=PrimitiveJson.Object(
            "tileSolid",(bool[])ArrayField("material.tileSolid",typeof(bool),tileCount).Clone(),
            "tileSolidTop",(bool[])ArrayField("material.tileSolidTop",typeof(bool),tileCount).Clone(),
            "tileSand",(bool[])ArrayField("material.tileSand",typeof(bool),tileCount).Clone());
        PinnedMaterialReader reader=new PinnedMaterialReader(this);
        object[] registry=reader.CopyList(Read("material.registry"),false);
        SortedDictionary<string,object> graph=MaterialGraph.Capture(registry,tileCount,reader,new MaterialGraphLimits());
        object[] placements=new object[itemCount-1];
        Type itemType=fields["identity.type"].DeclaringType;
        Program.Need(itemType.FullName=="Terraria.Item" && Object.ReferenceEquals(itemType.Assembly,game),"MATERIAL_ITEM_ASSEMBLY_MISMATCH");
        Type textType=methods["localizedTextValue"].DeclaringType;
        Program.Need(textType.FullName=="Terraria.Localization.LocalizedText" && Object.ReferenceEquals(textType.Assembly,game) &&
            fields["localizedText.EnglishValue"].DeclaringType==textType && fields["localizedText.Key"].DeclaringType==textType,
            "MATERIAL_LOCALIZED_TEXT_BINDING_MISMATCH");
        for (int id=1;id<itemCount;id++) {
            bool present=samples.Contains(id); object item=present?samples[id]:null;
            Program.Need(!present || item!=null && item.GetType()==itemType,"MATERIAL_SAMPLE_TYPE_MISMATCH");
            // Observe the requested-ID language cache directly. EnglishValue is
            // retained by the source localization layer while zh-Hans stays active.
            object text=Call("materialItemName",null,id);
            Program.Need(text!=null && text.GetType()==textType && Object.ReferenceEquals(text.GetType().Assembly,game),
                "MATERIAL_LOCALIZED_TEXT_TYPE_MISMATCH");
            placements[id-1]=PrimitiveJson.Object("requestedId",id,"samplePresent",present,
                "resolvedType",present?(object)Integer("identity.type",item):null,
                "createTile",present?(object)Integer("item.createTile",item):null,
                "placeStyle",present?(object)Integer("material.placeStyle",item):null,
                "englishValue",ObservedText(Read("localizedText.EnglishValue",text)),
                "localizationKey",ObservedText(Read("localizedText.Key",text)));
        }
        object result=PrimitiveJson.Object("schemaVersion",1,"kind","pinned-material-observation-fragment","status","PARTIAL",
            "evidenceLevel","DERIVED_ONLY","sourceSha256",FixedProfile.GameSha256,"gameVersion","1.4.5.8","culture",culture,
            "context",context,"tileCount",tileCount,"tileObjectDataCount",registry.Length,"itemCount",itemCount,
            "frameImportant",frameImportant,"pixelFlags",pixelFlags,"itemPlacements",placements,"tileRoots",graph["tileRoots"],"nodes",graph["nodes"],
            "isolationVerified",false,"initializationVerified",false,"sourceSemanticsVerified",false,"complete",false,"publishable",false,
            "missingJoins",new object[]{"final-map-options-and-localized-names","material-option-selection-policy",
                "ordered-shape-and-alias-policy","marker-selector-and-crop-policy","initialization-acceptance"});
        Program.Need(PrimitiveJson.Encode(result).Length<=32*1024*1024,"MATERIAL_OUTPUT_BYTE_LIMIT");
        stages.Add("materialObservation"); return result;
    }
}}
