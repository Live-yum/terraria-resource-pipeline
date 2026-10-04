using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;

namespace RuntimeObservationCollector {
internal sealed class MethodPin {
    internal readonly string Key, Owner, Name, SignatureSha256, IlSha256;
    internal readonly int Token;
    internal MethodPin(string key, string owner, string name, int token, string signature, string il) {
        Key=key; Owner=owner; Name=name; Token=token; SignatureSha256=signature; IlSha256=il;
    }
}
internal sealed class FieldPin {
    internal readonly string Key, Owner, Name, SignatureHex;
    internal readonly int Token;
    internal FieldPin(string key, string owner, string name, int token, string signature) {
        Key=key; Owner=owner; Name=name; Token=token; SignatureHex=signature;
    }
}
internal sealed class DependencyPin {
    internal readonly string Name, File, Sha256, Resource;
    internal DependencyPin(string name, string file, string hash, string resource) {
        Name=name; File=file; Sha256=hash; Resource=resource;
    }
}
internal static class Program {
    internal const string OptIn = "--i-understand-this-executes-game-code";
    private static readonly List<FileStream> LockedInputs = new List<FileStream>();
    internal static string Hex(byte[] data) { return BitConverter.ToString(data).Replace("-", "").ToLowerInvariant(); }
    internal static string Hash(byte[] data) { using (SHA256 sha=SHA256.Create()) return Hex(sha.ComputeHash(data)); }
    internal static string HashFile(string path) { using (FileStream f=File.OpenRead(path)) using (SHA256 sha=SHA256.Create()) return Hex(sha.ComputeHash(f)); }
    internal static void Need(bool okay, string message) { if (!okay) throw new InvalidDataException(message); }
    internal static bool IsLocalDriveAbsolute(string path) {
        return path!=null && path.Length>=3 && ((path[0]>='A' && path[0]<='Z') || (path[0]>='a' && path[0]<='z')) &&
            path[1]==':' && (path[2]=='\\' || path[2]=='/');
    }
    internal static string LocalPath(string path) {
        Need(IsLocalDriveAbsolute(path), "LOCAL_ABSOLUTE_PATH_REQUIRED");
        string full=Path.GetFullPath(path);
        string part=full;
        while (!String.IsNullOrEmpty(part)) {
            if (File.Exists(part) || Directory.Exists(part))
                Need((File.GetAttributes(part) & FileAttributes.ReparsePoint)==0, "REPARSE_POINT_REFUSED");
            part=Path.GetDirectoryName(part);
        }
        return full;
    }
    internal static void VerifyHash(byte[] bytes, string hash) { Need(Hash(bytes)==hash,"INPUT_HASH_MISMATCH"); }
    private static void LockAndHash(string path, string hash, long maximum) {
        LocalPath(path);
        FileStream f=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
        LockedInputs.Add(f);
        Need(f.Length>0 && f.Length<=maximum,"INPUT_BYTE_LIMIT");
        using (SHA256 sha=SHA256.Create()) Need(Hex(sha.ComputeHash(f))==hash,"INPUT_HASH_MISMATCH: "+Path.GetFileName(path));
        f.Position=0;
    }
    internal static bool IsObservationArguments(string[] args) {
        return args.Length==6 && args[0]=="--observe-pinned-client" && args[1]==OptIn &&
            args[2]=="--isolated-windows-x86" && args[3]=="--input-and-new-output";
    }
    private static int Main(string[] args) {
        try {
            // No filesystem or vendor loading on this original-code-only path.
            if (args.Length==1 && args[0]=="--self-test") return SelfTest.Run();
            if (!IsObservationArguments(args)) {
                Console.Error.WriteLine("Use --self-test, or the documented explicit isolated observation command. No arbitrary invocation is supported.");
                return 64;
            }
            Need(Environment.OSVersion.Platform==PlatformID.Win32NT && IntPtr.Size==4, "WINDOWS_X86_CLR4_REQUIRED");
            Need(Environment.Version.Major==4 && Type.GetType("Mono.Runtime")==null,"MICROSOFT_CLR4_REQUIRED");
            WindowsJob.Install();
            string input=LocalPath(args[4]); string output=LocalPath(args[5]);
            string temp=Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
            Need(output.StartsWith(temp,StringComparison.OrdinalIgnoreCase),"OUTPUT_MUST_BE_NEW_TEMP_DIRECTORY");
            Need(!Directory.Exists(output) && !File.Exists(output),"OUTPUT_MUST_NOT_EXIST");
            Need(Directory.Exists(input),"INPUT_DIRECTORY_MISSING");
            Need(!output.StartsWith(input.TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase),"OUTPUT_CANNOT_BE_INSIDE_INPUT");
            string gamePath=Path.Combine(input,"Terraria.exe");
            LockAndHash(gamePath,FixedProfile.GameSha256,64*1024*1024);
            HashSet<string> names=new HashSet<string>(StringComparer.OrdinalIgnoreCase); names.Add("Terraria.exe");
            foreach (DependencyPin pin in FixedProfile.Dependencies) if (pin.Resource==null) {
                names.Add(pin.File); LockAndHash(Path.Combine(input,pin.File),pin.Sha256,16*1024*1024);
            }
            foreach (DependencyPin pin in FixedProfile.NativeDependencies) {
                names.Add(pin.File); LockAndHash(Path.Combine(input,pin.File),pin.Sha256,16*1024*1024);
            }
            HashSet<string> observedNames=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string file in Directory.GetFileSystemEntries(input)) {
                Need(File.Exists(file) && names.Contains(Path.GetFileName(file)) && observedNames.Add(Path.GetFileName(file)),"UNEXPECTED_OR_DUPLICATE_INPUT_ENTRY");
                LocalPath(file);
            }
            Need(observedNames.SetEquals(names),"INCOMPLETE_INPUT_INVENTORY");
            Directory.CreateDirectory(output);
            Directory.SetCurrentDirectory(output);
            // No automatic installer, permissions change or firewall action.
            using (FixedCollector collector=new FixedCollector(input,output)) {
                object observation=collector.Observe();
                byte[] bytes=PrimitiveJson.Encode(observation);
                string path=Path.Combine(output,"observation.partial.json");
                string pending=path+".writing";
                using (FileStream file=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                    file.Write(bytes,0,bytes.Length); file.Flush(true);
                }
                // The final observation name appears only after complete durable writing.
                File.Move(pending,path);
                Console.WriteLine("PARTIAL "+Hash(bytes));
            }
            return 0;
        } catch (Exception error) {
            // Never serialize arbitrary Exception.Data, paths, vendor objects, or
            // continue after a failed initializer. No success artifact on failure.
            Console.Error.WriteLine("COLLECTOR_FAILED: "+error.GetType().Name+": "+(error is InvalidDataException ? error.Message : "see last STAGE marker"));
            return 1;
        } finally { foreach (FileStream input in LockedInputs) input.Dispose(); }
    }
}}
