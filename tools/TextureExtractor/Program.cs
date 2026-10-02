using System.IO.Compression;
using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TConvert.Extract;
// Static data parsing only; never activate an XNB reader or load game code.
if (args.Length!=4 || args[0]!="--request" || args[2]!="--output") return 2;
try {
 var request=JsonDocument.Parse(File.ReadAllBytes(args[1]));
 string input=request.RootElement.GetProperty("input").GetString(),output=args[3];
 Directory.CreateDirectory(output);
 var files=Directory.EnumerateFiles(input,"*",SearchOption.AllDirectories).Where(p=>p.EndsWith(".xnb",StringComparison.OrdinalIgnoreCase)).Order().ToArray();
 if(files.Length>50000)throw new InvalidDataException("File count");
 var rows=new List<object>();var skipped=new List<object>();long pixels=0;
 foreach(string path in files){
  if(new FileInfo(path).Length>128*1024*1024)throw new InvalidDataException("Input size");
  var source=File.ReadAllBytes(path);var texture=Texture.Read(source);
  if(texture is null){skipped.Add(new{input=Path.GetRelativePath(input,path).Replace('\\','/'),sourceSha256=Hash(source),reason="UNSUPPORTED_XNB_TYPE"});continue;}
  pixels=checked(pixels+(long)texture.Width*texture.Height);if(pixels>256000000)throw new InvalidDataException("Total pixels");
  string relative=Path.GetRelativePath(input,path),name=Path.ChangeExtension(relative,".png"),destination=Path.Combine(output,name);
  Directory.CreateDirectory(Path.GetDirectoryName(destination));byte[] png=Texture.Png(texture);
  using(var target=new FileStream(destination,FileMode.CreateNew))target.Write(png);
  rows.Add(new{input=relative.Replace('\\','/'),output=name.Replace('\\','/'),sourceSha256=Hash(source),sha256=Hash(png),width=texture.Width,height=texture.Height,surfaceFormat=texture.SurfaceFormat});
 }
 File.WriteAllText(Path.Combine(output,"texture-report.json"),JsonSerializer.Serialize(new{schemaVersion=1,converter="tconvert-lzx-portable-texture-v1",imageCount=rows.Count,images=rows,skipped,executedInput=false,extractionComplete=false,publishable=false}));return 0;
}catch(Exception ex){Console.Error.WriteLine("Texture extraction rejected: "+ex.GetType().Name);return 1;}
static string Hash(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
record Pixels(int Width,int Height,byte[] Rgba,int SurfaceFormat);
static class Texture {
 const int Max=128*1024*1024;
 static byte[] Bytes(BinaryReader r,int n){if(n<0||n>Max||n>r.BaseStream.Length-r.BaseStream.Position)throw new InvalidDataException();return r.ReadBytes(n);}
 static int VarInt(BinaryReader r){uint v=0;for(int i=0;i<5;i++){byte b=r.ReadByte();if(i==4&&b>7)throw new InvalidDataException();v|=(uint)(b&127)<<(7*i);if(b<128)return checked((int)v);}throw new InvalidDataException();}
 static string Str(BinaryReader r){int n=VarInt(r);if(n>4096)throw new InvalidDataException();return new UTF8Encoding(false,true).GetString(Bytes(r,n));}
 public static Pixels Read(byte[] source){
  using var initial=new BinaryReader(new MemoryStream(source));
  if(Encoding.ASCII.GetString(Bytes(initial,3))!="XNB")throw new InvalidDataException();
  if(!new[]{(byte)'w',(byte)'m',(byte)'x'}.Contains(initial.ReadByte())||initial.ReadByte()!=5)throw new InvalidDataException();
  byte flags=initial.ReadByte();if((flags&~0x81)!=0||initial.ReadInt32()!=source.Length)throw new InvalidDataException();
  byte[] payload;
  if((flags&0x80)!=0){int size=initial.ReadInt32();if(size<1||size>Max)throw new InvalidDataException();
   int pos=14,total=0;
   while(pos<source.Length){if(pos+2>source.Length)throw new InvalidDataException();int a=source[pos++],b=source[pos++],frame=32768,block=(a<<8)|b;
    if(a==255){if(pos+3>source.Length)throw new InvalidDataException();frame=(b<<8)|source[pos++];block=(source[pos++]<<8)|source[pos++];}
    if(frame<1||frame>32768||block<1||pos+block>source.Length)throw new InvalidDataException();total=checked(total+frame);if(total>size)throw new InvalidDataException();pos+=block;}
   if(total!=size)throw new InvalidDataException();
   using var expanded=new MemoryStream(size);new LzxDecoder().Decompress(initial,source.Length-14,expanded,size);
   if(expanded.Length!=size)throw new InvalidDataException();payload=expanded.ToArray();
  }else payload=Bytes(initial,source.Length-10);
  using var r=new BinaryReader(new MemoryStream(payload));int count=VarInt(r);if(count<1||count>64)throw new InvalidDataException();
  var readers=new string[count];for(int i=0;i<count;i++){readers[i]=Str(r).Split(',')[0];r.ReadInt32();}
  if(VarInt(r)!=0)throw new InvalidDataException();int primary=VarInt(r);if(primary<1||primary>count)throw new InvalidDataException();
  if(readers[primary-1]!="Microsoft.Xna.Framework.Content.Texture2DReader")return null;
  int format=r.ReadInt32(),w=r.ReadInt32(),h=r.ReadInt32(),mips=r.ReadInt32();
  if(w<1||h<1||(long)w*h>16000000||mips<1||mips>1+(int)Math.Log2(Math.Max(w,h))||!new[]{0,4,5,6}.Contains(format))throw new InvalidDataException();
  byte[] rgba=null;
  for(int mip=0;mip<mips;mip++){int mw=Math.Max(1,w>>mip),mh=Math.Max(1,h>>mip);long expected=format==0?(long)mw*mh*4:((long)(mw+3)/4)*((mh+3)/4)*(format==4?8:16);int n=r.ReadInt32();if(n!=expected)throw new InvalidDataException();byte[] data=Bytes(r,n);
   if(mip==0)rgba=format switch{0=>data,4=>DxtUtil.DecompressDxt1(data,w,h),5=>DxtUtil.DecompressDxt3(data,w,h),6=>DxtUtil.DecompressDxt5(data,w,h),_=>throw new InvalidDataException()};}
  if(r.BaseStream.Position!=r.BaseStream.Length)throw new InvalidDataException();return new Pixels(w,h,rgba,format);
 }
 static uint Crc(byte[] bytes){uint c=0xffffffff;foreach(byte b in bytes){c^=b;for(int i=0;i<8;i++)c=(c>>1)^((c&1)!=0?0xedb88320u:0);}return ~c;}
 public static byte[] Png(Pixels p){using var file=new MemoryStream();file.Write(new byte[]{137,80,78,71,13,10,26,10});
  void Chunk(string name,byte[] data){byte[] type=Encoding.ASCII.GetBytes(name),n=new byte[4];BinaryPrimitives.WriteInt32BigEndian(n,data.Length);file.Write(n);file.Write(type);file.Write(data);BinaryPrimitives.WriteUInt32BigEndian(n,Crc(type.Concat(data).ToArray()));file.Write(n);}
  byte[] header=new byte[13];BinaryPrimitives.WriteInt32BigEndian(header,p.Width);BinaryPrimitives.WriteInt32BigEndian(header.AsSpan(4),p.Height);header[8]=8;header[9]=6;Chunk("IHDR",header);
  using var compressed=new MemoryStream();using(var z=new ZLibStream(compressed,CompressionLevel.Optimal,true)){for(int y=0;y<p.Height;y++){z.WriteByte(0);z.Write(p.Rgba,y*p.Width*4,p.Width*4);}}
  Chunk("IDAT",compressed.ToArray());Chunk("IEND",Array.Empty<byte>());return file.ToArray();}
}
