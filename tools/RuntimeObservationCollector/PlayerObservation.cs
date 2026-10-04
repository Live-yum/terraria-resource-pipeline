using System;
using System.Collections.Generic;
using System.Reflection;

namespace RuntimeObservationCollector {
internal sealed partial class FixedCollector {
    private object[] Vector3Value(object vector) {
        Program.Need(vector!=null,"MISSING_SHADER_VECTOR");
        Type type=vector.GetType(); Assembly assembly=type.Assembly;
        Program.Need(type.FullName=="Microsoft.Xna.Framework.Vector3" && assembly.GetName().Name=="Microsoft.Xna.Framework","UNEXPECTED_SHADER_VECTOR_TYPE");
        CheckAssembly(assembly);
        object[] values=new object[3]; int index=0;
        foreach (FieldPin pin in FixedProfile.XnaVectorFields) {
            FieldInfo field=assembly.ManifestModule.ResolveField(pin.Token);
            Program.Need(field.DeclaringType==type && field.Name==pin.Name &&
                Program.Hex(assembly.ManifestModule.ResolveSignature(pin.Token))==pin.SignatureHex,"XNA_VECTOR_FIELD_PIN_MISMATCH");
            object value=field.GetValue(vector);
            Program.Need(value!=null && value.GetType()==typeof(float),"UNEXPECTED_SHADER_VECTOR_SCALAR");
            values[index++]=value;
        }
        return values;
    }
    private static object ObservedText(object value) {
        Program.Need(value==null || value.GetType()==typeof(string),"UNEXPECTED_OBSERVED_TEXT_TYPE");
        return value; // No replacement string for missing/empty source facts.
    }
    private object[] CapturePrefixNames(int count) {
        Type textType=methods["localizedTextValue"].DeclaringType;
        Program.Need(Object.ReferenceEquals(textType.Assembly,game),"PREFIX_TEXT_ASSEMBLY_MISMATCH");
        Array source=ArrayField("lang.prefix",textType,count);
        object[] names=new object[count];
        for (int id=0;id<count;id++) {
            object text=source.GetValue(id);
            Program.Need(text!=null && text.GetType()==textType,"MISSING_PREFIX_LOCALIZED_TEXT");
            names[id]=PrimitiveJson.Object("id",id,"name",ObservedText(Call("localizedTextValue",text)));
        }
        return names;
    }
    private object CapturePlayerObservation(object[] itemRows) {
        Console.Error.WriteLine("STAGE: playerObservation");
        Program.Need((bool)Read("main.dedServ"),"DYE_OBSERVATION_MODE_CHANGED");
        object language=Read("language.Instance");
        Program.Need((string)Call("cultureName",Call("activeCulture",language))=="zh-Hans","PLAYER_OBSERVATION_CULTURE_CHANGED");
        int buffCount=Integer("buffId.Count"); Program.Need(buffCount>1 && buffCount<=65536,"BUFF_COUNT_BOUND");
        object[] buffs=new object[buffCount-1];
        for (int id=1;id<buffCount;id++) buffs[id-1]=PrimitiveJson.Object("id",id,
            "name",ObservedText(Call("buffName",null,id)),"description",ObservedText(Call("buffDescription",null,id)));
        bool[] debuffs=(bool[])ArrayField("main.debuff",typeof(bool),buffCount).Clone();
        Cctor("faceSetsCctor"); int faceCount=Integer("faceId.Count");
        Program.Need(faceCount>0 && faceCount<=512,"FACE_COUNT_BOUND");
        SortedDictionary<string,object> face=PrimitiveJson.Object();
        foreach (string name in FixedProfile.FaceSets)
            face.Add(name,(bool[])ArrayField("faceSets."+name,typeof(bool),faceCount).Clone());
        object armor=Read("shader.armor"),hair=Read("shader.hair");
        int hairCount=Integer("hair.shaderCount",hair); Program.Need(hairCount>0 && hairCount<=255,"HAIR_SHADER_COUNT_BOUND");
        List<object> dyes=new List<object>(),hairBindings=new List<object>();
        foreach (object value in itemRows) {
            SortedDictionary<string,object> row=(SortedDictionary<string,object>)value;
            SortedDictionary<string,object> gameplay=(SortedDictionary<string,object>)row["gameplay"];
            int id=(int)row["requestedId"],dye=(int)gameplay["dye"],hairDye=(int)gameplay["hairDye"];
            if (dye>0) {
                int shaderId=(int)Call("armorShaderId",armor,id);
                Program.Need(shaderId==dye,"ITEM_ARMOR_SHADER_ID_MISMATCH");
                object shader=Call("armorShaderForItem",armor,id);
                Program.Need(shader!=null && Object.ReferenceEquals(shader.GetType().Assembly,game) &&
                    Array.IndexOf(FixedProfile.ArmorShaderClasses,shader.GetType().FullName)>=0,"UNEXPECTED_ARMOR_SHADER_CLASS");
                object saturation=Read("shader.saturation",shader);
                Program.Need(saturation!=null && saturation.GetType()==typeof(float),"UNEXPECTED_SHADER_SATURATION_TYPE");
                dyes.Add(PrimitiveJson.Object("itemId",id,"shaderId",shaderId,"class",shader.GetType().FullName,
                    "pass",ObservedText(Read("shader.pass",shader)),"color",Vector3Value(Read("shader.color",shader)),
                    "secondaryColor",Vector3Value(Read("shader.secondaryColor",shader)),"saturation",saturation));
                Program.Need(dyes.Count<=512,"DYE_RECORD_COUNT_BOUND");
            }
            if (hairDye>0) {
                int shaderId=(int)PrimitiveJson.Scalar(Call("hairShaderId",hair,id));
                Program.Need(shaderId==hairDye && shaderId<=hairCount,"ITEM_HAIR_SHADER_ID_MISMATCH");
                hairBindings.Add(PrimitiveJson.Object("itemId",id,"shaderId",shaderId));
                Program.Need(hairBindings.Count<=255,"HAIR_DYE_BINDING_COUNT_BOUND");
            }
        }
        stages.Add("playerObservation");
        return PrimitiveJson.Object("schemaVersion",1,"gameVersion","1.4.5.8","sourceSha256",FixedProfile.GameSha256,
            "culture","zh-Hans","dedServ",true,"buffCount",buffCount,"buffs",buffs,"dyes",dyes.ToArray(),
            "faceCount",faceCount,"faceSets",face,"hairShaderCount",hairCount,"hairDyeBindings",hairBindings.ToArray(),
            "mainDebuff",debuffs,"omittedFields",new object[]{"dyes.image"},
            "initializationVerified",false,"complete",false,"publishable",false);
    }
}}
