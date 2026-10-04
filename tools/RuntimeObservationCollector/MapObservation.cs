using System;
using System.Reflection;

namespace RuntimeObservationCollector {
internal sealed partial class FixedCollector {
    internal static object[] MapColorBytes(uint packed) {
        return new object[]{(int)(packed&255U),(int)((packed>>8)&255U),(int)((packed>>16)&255U),(int)((packed>>24)&255U)};
    }
    private object[] MapOptionRows(string countsKey,string lookupKey,int count,int colors) {
        int[] counts=(int[])ArrayField(countsKey,typeof(int),count).Clone();
        ushort[] lookup=(ushort[])ArrayField(lookupKey,typeof(ushort),count).Clone();
        return MapOptionRowsFromArrays(counts,lookup,count,colors);
    }
    internal static object[] MapOptionRowsFromArrays(int[] counts,ushort[] lookup,int count,int colors) {
        Program.Need(count>0 && count<=65536 && colors>0 && colors<=65536 && counts!=null && lookup!=null && counts.Length==count && lookup.Length==count,"MAP_OPTION_ARRAY_BOUND");
        object[] rows=new object[count]; int total=0;
        for (int id=0;id<count;id++) {
            int length=counts[id],start=(int)lookup[id];
            Program.Need(length>=0 && length<=256,"MAP_OPTION_COUNT_BOUND");
            Program.Need(length==0 || (start<colors && start+length<=colors),"MAP_LOOKUP_BOUND");
            total+=length; Program.Need(total<=65536,"MAP_OPTION_AGGREGATE_BOUND");
            object[] options=new object[length];
            for (int option=0;option<length;option++) options[option]=PrimitiveJson.Object("option",option,"lookupIndex",start+option);
            rows[id]=PrimitiveJson.Object("id",id,"optionCount",length,"lookupStart",start,"options",options);
        }
        return rows;
    }
    private object CaptureMapObservation(string culture,object context) {
        Program.Need(culture=="zh-Hans" && (bool)Read("main.dedServ"),"MAP_OBSERVATION_MODE_MISMATCH");
        Stage("mapInitialize",null);
        int tiles=Integer("tileId.Count"),walls=Integer("wallId.Count");
        Program.Need(tiles==754 && walls==367,"MAP_ID_COUNT_BOUND");
        Array colors=Read("map.colors") as Array,legend=Read("lang.mapLegend") as Array;
        Type textType=methods["localizedTextValue"].DeclaringType;
        Program.Need(colors!=null && colors.Rank==1 && colors.GetLowerBound(0)==0 && colors.Length>0 && colors.Length<=65536,"MAP_COLOR_ARRAY_BOUND");
        Program.Need(legend!=null && legend.Rank==1 && legend.GetLowerBound(0)==0 && legend.Length==colors.Length && legend.GetType().GetElementType()==textType,"MAP_LEGEND_ARRAY_BOUND");
        colors=(Array)colors.Clone(); legend=(Array)legend.Clone();
        Type colorType=colors.GetType().GetElementType(); Assembly assembly=colorType.Assembly;
        Program.Need(colorType.FullName=="Microsoft.Xna.Framework.Color" && assembly.GetName().Name=="Microsoft.Xna.Framework","MAP_COLOR_TYPE_MISMATCH");
        CheckAssembly(assembly);
        FieldPin pin=FixedProfile.XnaColorField;
        FieldInfo field=assembly.ManifestModule.ResolveField(pin.Token);
        Program.Need(field.DeclaringType==colorType && field.Name==pin.Name &&
            Program.Hex(assembly.ManifestModule.ResolveSignature(pin.Token))==pin.SignatureHex,"MAP_COLOR_FIELD_PIN_MISMATCH");
        object[] entries=new object[colors.Length];
        for (int index=0;index<colors.Length;index++) {
            object color=colors.GetValue(index),text=legend.GetValue(index);
            Program.Need(color!=null && color.GetType()==colorType && (text==null || text.GetType()==textType),"MAP_ENTRY_TYPE_MISMATCH");
            object raw=field.GetValue(color); Program.Need(raw!=null && raw.GetType()==typeof(uint),"MAP_COLOR_PACKED_TYPE");
            uint packed=(uint)raw;
            entries[index]=PrimitiveJson.Object("lookupIndex",index,
                "rgba",MapColorBytes(packed),
                "legendPresent",text!=null,"name",text==null?null:ObservedText(Call("localizedTextValue",text)),
                "localizationKey",text==null?null:ObservedText(Read("localizedText.Key",text)));
        }
        object[] tileRows=MapOptionRows("map.tileOptions","map.tileLookup",tiles,entries.Length);
        object[] wallRows=MapOptionRows("map.wallOptions","map.wallLookup",walls,entries.Length);
        stages.Add("mapObservation");
        return PrimitiveJson.Object("schemaVersion",1,"kind","pinned-map-observation-fragment","status","PARTIAL",
            "sourceSha256",FixedProfile.GameSha256,"gameVersion","1.4.5.8","culture",culture,"context",context,
            "tileCount",tiles,"wallCount",walls,"lookupCount",entries.Length,"lookupEntries",entries,
            "tiles",tileRows,
            "walls",wallRows,
            "appOptionSelectionApplied",false,"isolationVerified",false,"initializationVerified",false,
            "sourceSemanticsVerified",false,"complete",false,"publishable",false);
    }
}}
