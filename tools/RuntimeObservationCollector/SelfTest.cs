using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;

namespace RuntimeObservationCollector {
internal static class SelfTest {
    private sealed class DoNotTraverse {
        public object Secret { get { throw new Exception("Getter must never run"); } }
        public override string ToString() { throw new Exception("ToString must never run"); }
    }
    private sealed class OriginalMetadataFixture {
        public int Number;
        public OriginalMetadataFixture() { Number=7; }
        public static int OriginalMethod() { return new OriginalMetadataFixture().Number; }
    }
    private static void Check(bool condition) { if (!condition) throw new Exception("SELF_TEST_FAILED"); }
    private static void Reject(Action action) {
        bool rejected=false;
        try { action(); } catch (InvalidDataException) { rejected=true; } catch (EncoderFallbackException) { rejected=true; }
        Check(rejected);
    }
    internal static int Run() {
        Type fixture=typeof(OriginalMetadataFixture);
        FieldInfo field=fixture.GetField("Number",BindingFlags.Public|BindingFlags.Instance);
        MethodInfo method=fixture.GetMethod("OriginalMethod",BindingFlags.Public|BindingFlags.Static);
        Check(field!=null && method!=null);
        Check(Program.Hex(fixture.Module.ResolveSignature(field.MetadataToken))=="0608");
        Check(Program.Hex(fixture.Module.ResolveSignature(method.MetadataToken))=="000008");
        Check(fixture.Module.ResolveField(field.MetadataToken).DeclaringType==fixture);
        Check(fixture.Module.ResolveMethod(method.MetadataToken).GetMethodBody().GetILAsByteArray().Length>0);
        byte[] json=PrimitiveJson.Encode(PrimitiveJson.Object("z",new object[]{true,null,-1,0,1.5f},"a","line\n\"\\中"));
        Check(Encoding.UTF8.GetString(json)=="{\"a\":\"line\\u000a\\\"\\\\\\u4e2d\",\"z\":[true,null,-1,0,1.5]}");
        Check(Encoding.UTF8.GetString(PrimitiveJson.Encode(1.2f))=="1.2000000476837158");
        Check((int)PrimitiveJson.Scalar((sbyte)-1)==-1);
        Check((int)PrimitiveJson.Scalar((ushort)65535)==65535);
        Reject(delegate { PrimitiveJson.Encode(new DoNotTraverse()); });
        Reject(delegate { PrimitiveJson.Encode(double.NaN); });
        Reject(delegate { PrimitiveJson.Encode(float.PositiveInfinity); });
        Reject(delegate { PrimitiveJson.Encode(new string('a',4097)); });
        Reject(delegate { PrimitiveJson.Encode("\ud800"); });
        Reject(delegate { PrimitiveJson.Encode(new int[65537]); });
        Reject(delegate { PrimitiveJson.Scalar(null); });
        Reject(delegate { PrimitiveJson.Scalar("0"); });
        Reject(delegate { PrimitiveJson.Object("odd"); });
        object nesting=1;
        for (int i=0;i<14;i++) nesting=new object[]{nesting};
        object tooDeep=nesting; Reject(delegate { PrimitiveJson.Encode(tooDeep); });
        object[] tooLarge=new object[20000];
        for (int i=0;i<tooLarge.Length;i++) tooLarge[i]=new string('a',4096);
        Reject(delegate { PrimitiveJson.Encode(tooLarge); });
        Check(Program.Hash(new byte[0])=="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        Reject(delegate { Program.VerifyHash(new byte[]{1},FixedProfile.GameSha256); });
        Check(Program.IsLocalDriveAbsolute("C:\\original") && Program.IsLocalDriveAbsolute("d:/original"));
        Check(!Program.IsLocalDriveAbsolute("C:relative") && !Program.IsLocalDriveAbsolute("\\root-relative"));
        Check(!Program.IsLocalDriveAbsolute("\\\\server\\share") && !Program.IsLocalDriveAbsolute(null));
        Check(!Program.IsObservationArguments(new string[]{"--observe-pinned-client"}));
        Check(!Program.IsObservationArguments(new string[]{"--observe-pinned-client","yes","--isolated-windows-x86","--input-and-new-output","x","y"}));
        Check(Program.IsObservationArguments(new string[]{"--observe-pinned-client",Program.OptIn,"--isolated-windows-x86","--input-and-new-output","x","y"}));
        Check(FixedProfile.ItemFields.Length==47 && FixedProfile.PriorityFields.Length==20);
        Check(FixedProfile.XnaVectorFields.Length==3 && FixedProfile.FaceSets.Length==6 && FixedProfile.ArmorShaderClasses.Length==4);
        HashSet<string> keys=new HashSet<string>(StringComparer.Ordinal);
        foreach (MethodPin pin in FixedProfile.Methods) {
            Check(keys.Add(pin.Key)); Check(pin.Token>0x06000000 && pin.Token<0x07000000);
            Check(pin.Name!="Initialize_AlmostEverything" && pin.Name!="BoringSetup");
        }
        keys.Clear(); foreach (FieldPin pin in FixedProfile.Fields) Check(keys.Add(pin.Key));
        Console.WriteLine("PASS original-code-only self-test; game execution NOT_RUN; initializationVerified=false; publishable=false");
        return 0;
    }
}}
