using System;
using System.Collections.Generic;
using System.IO;

namespace RuntimeObservationCollector {
// Original fake objects only. The runner does not resolve or load a game type.
internal static class MaterialObservationSelfTest {
    private sealed class Node {
        internal MaterialNodeSnapshot Snapshot=new MaterialNodeSnapshot();
        internal bool Readable=true;
        public override bool Equals(object other) { throw new Exception("Vendor equality must not run"); }
        public override int GetHashCode() { throw new Exception("Vendor hashing must not run"); }
        public override string ToString() { throw new Exception("Vendor formatting must not run"); }
    }
    private sealed class Reader : MaterialNodeReader {
        internal int Captures;
        internal override void Validate(object value) {
            Program.Need(value!=null && value.GetType()==typeof(Node) && ((Node)value).Readable,"TEST_UNSAFE_NODE");
        }
        internal override MaterialNodeSnapshot Capture(object value) { Captures++; return ((Node)value).Snapshot; }
    }
    private static void Check(bool ok) { if (!ok) throw new Exception("MATERIAL_SELF_TEST_FAILED"); }
    private static void Reject(Action action,string reason) {
        bool rejected=false;
        try { action(); } catch (InvalidDataException error) { Check(error.Message==reason); rejected=true; }
        Check(rejected);
    }
    private static SortedDictionary<string,object> Row(object value) { return (SortedDictionary<string,object>)value; }
    private static object[] Array(object value) { return (object[])value; }
    private static SortedDictionary<string,object> Capture(object[] roots,int count,MaterialGraphLimits limits) {
        return MaterialGraph.Capture(roots,count,new Reader(),limits);
    }
    internal static void Run() {
        Node a=new Node(),b=new Node();
        a.Snapshot.SubTiles=new object[]{null,b,a,b}; a.Snapshot.Alternates=new object[]{b,null};
        a.Snapshot.CoordinateHeights=new int[]{16,-1,16}; a.Snapshot.SpecificRandomStyles=new int[]{-7,4,4};
        a.Snapshot.Style=-2; a.Snapshot.StyleMultiplier=-3; a.Snapshot.StyleWrapLimit=-4;
        a.Snapshot.StyleLineSkip=-5; a.Snapshot.RandomStyleRange=-6;
        a.Snapshot.PaddingFixX=-18; a.Snapshot.PaddingFixY=-20; a.Snapshot.HasGetStyleOverride=true;
        b.Snapshot.SubTiles=new object[0]; b.Snapshot.Alternates=null;
        Reader reader=new Reader();
        SortedDictionary<string,object> graph=MaterialGraph.Capture(new object[]{a,a,null},4,reader,new MaterialGraphLimits());
        object[] roots=Array(graph["tileRoots"]),nodes=Array(graph["nodes"]);
        Check(roots.Length==4 && nodes.Length==2 && reader.Captures==2);
        Check((int)Row(roots[0])["nodeId"]==0 && (int)Row(roots[1])["nodeId"]==0);
        Check(Row(roots[2])["nodeId"]==null && (bool)Row(roots[2])["registrationPresent"]);
        Check(Row(roots[3])["nodeId"]==null && !(bool)Row(roots[3])["registrationPresent"]);
        SortedDictionary<string,object> first=Row(nodes[0]),second=Row(nodes[1]);
        object[] children=Array(first["subTiles"]);
        Check(children.Length==4 && children[0]==null && (int)children[1]==1 && (int)children[2]==0 && (int)children[3]==1);
        Check(Array(second["subTiles"]).Length==0 && second["alternates"]==null);
        Check((int)first["style"]==-2 && (int)first["styleMultiplier"]==-3 && (int)first["styleWrapLimit"]==-4);
        Check((int)first["styleLineSkip"]==-5 && (int)first["randomStyleRange"]==-6);
        Check((int)first["paddingFixX"]==-18 && (int)first["paddingFixY"]==-20 && (bool)first["hasGetStyleOverride"]);
        Check(((int[])first["specificRandomStyles"])[0]==-7 && ((int[])first["specificRandomStyles"])[1]==4 && ((int[])first["specificRandomStyles"])[2]==4);
        a.Snapshot.CoordinateHeights[0]=999; a.Snapshot.SpecificRandomStyles[0]=999; a.Snapshot.SubTiles[1]=null;
        Check(((int[])first["coordinateHeights"])[0]==16 && ((int[])first["specificRandomStyles"])[0]==-7 && (int)children[1]==1);
        Check(PrimitiveJson.Encode(graph).Length>0);
        Node blocked=new Node(); blocked.Readable=false; Reader blockedReader=new Reader();
        Reject(delegate { MaterialGraph.Capture(new object[]{blocked},1,blockedReader,new MaterialGraphLimits()); },"TEST_UNSAFE_NODE");
        Check(blockedReader.Captures==0);
        Reject(delegate { Capture(new object[]{new object()},1,new MaterialGraphLimits()); },"TEST_UNSAFE_NODE");
        Reject(delegate { Capture(new object[]{new Node(),new Node()},1,new MaterialGraphLimits()); },"MATERIAL_REGISTRY_DOMAIN");
        Reject(delegate { Capture(new object[]{new Node(),new Node()},2,new MaterialGraphLimits(1,2,10,10,10)); },"MATERIAL_NODE_LIMIT");
        Node wide=new Node(); wide.Snapshot.SubTiles=new object[]{null,null};
        Reject(delegate { Capture(new object[]{wide},1,new MaterialGraphLimits(5,1,10,10,10)); },"MATERIAL_CHILD_ARRAY_LIMIT");
        Reject(delegate { Capture(new object[]{wide},1,new MaterialGraphLimits(5,5,2,10,10)); },"MATERIAL_GRAPH_EDGE_LIMIT");
        Node coordinates=new Node(); coordinates.Snapshot.CoordinateHeights=new int[]{1,2};
        Reject(delegate { Capture(new object[]{coordinates},1,new MaterialGraphLimits(5,1,10,10,10)); },"MATERIAL_INTEGER_ARRAY_LIMIT");
        Reject(delegate { Capture(new object[]{coordinates},1,new MaterialGraphLimits(5,5,10,1,10)); },"MATERIAL_COORDINATE_VALUE_LIMIT");
        Node random=new Node(); random.Snapshot.SpecificRandomStyles=new int[]{1,1};
        Reject(delegate { Capture(new object[]{random},1,new MaterialGraphLimits(5,5,10,10,1)); },"MATERIAL_RANDOM_VALUE_LIMIT");
        Node empty=new Node(); empty.Snapshot=null;
        Reject(delegate { Capture(new object[]{empty},1,new MaterialGraphLimits()); },"MISSING_MATERIAL_NODE_SNAPSHOT");
        Node head=new Node(),tail=head;
        for (int i=1;i<2048;i++) { Node next=new Node(); tail.Snapshot.SubTiles=new object[]{next}; tail=next; }
        Check(Array(Capture(new object[]{head},1,new MaterialGraphLimits())["nodes"]).Length==2048);
        // Multiple node arrays are charged cumulatively, not per-node alone.
        Node c=new Node(),d=new Node(); c.Snapshot.CoordinateHeights=new int[]{1}; d.Snapshot.CoordinateHeights=new int[]{2};
        Reject(delegate { Capture(new object[]{c,d},2,new MaterialGraphLimits(5,5,10,1,10)); },"MATERIAL_COORDINATE_VALUE_LIMIT");
        c.Snapshot.SpecificRandomStyles=new int[]{-1}; d.Snapshot.SpecificRandomStyles=new int[]{-1};
        Reject(delegate { Capture(new object[]{c,d},2,new MaterialGraphLimits(5,5,10,10,1)); },"MATERIAL_RANDOM_VALUE_LIMIT");
        Check(Array(Capture(new object[0],2,new MaterialGraphLimits())["nodes"]).Length==0);
        Reject(delegate { new MaterialGraphLimits(65537,1,1,1,1); },"MATERIAL_LIMIT_CONFIGURATION");
    }
}}
