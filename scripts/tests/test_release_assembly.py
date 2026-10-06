"""Synthetic protocol tests. These are deliberately NOT real-game fixtures."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),SCRIPTS/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
verify=module('verify-release-assembly')
experiment=module('wasm-experiment-plan')

class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.identity=self.fixture()
    def put(self,name,value):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);return verify.sha(raw)
    def fixture(self):
        asm=self.put('game.exe',b'synthetic-unit-test-not-a-game-assembly')
        resource={'gameVersion':'test','sources':{'serverSha256':asm}}
        resource_hash=self.put('manifest.json',resource)
        builtin_hash=self.put('builtin.json',{'gameVersion':'test'})
        identity=dict(schema=1,appCommit='a'*40,engineSourceCommit='b'*40,extractorCommit='c'*40,
            gameAssemblySha256=asm,resourceManifestSha256=resource_hash,builtinDescriptorSha256=builtin_hash,
            authorityId='unit-test',sequence='9007199254740993',engines=[],evidence={})
        evidence={'resourceManifest':'manifest.json','builtinDescriptor':'builtin.json','gameAssembly':'game.exe',
            'extraction':'extraction.json','appBuild':'app.json','authority':'authority.json','differentialReport':'differential.json'}
        identity['evidence']=evidence
        self.put('extraction.json',dict(extractorCommit='c'*40,cleanBuild=True,gameAssemblySha256=asm,resourceManifestSha256=resource_hash))
        approval=dict(active=dict(gameVersion='test',manifestSha256=resource_hash),authorityId='unit-test',channel='stable',
            revokedManifestSha256=[],schema=1,sequence=identity['sequence'])
        approval['stateSha256']=verify.sha(json.dumps(approval,separators=(',',':')).encode())
        self.put('authority.json',approval)
        app={'sourceCommit':'a'*40,'dirty':False,'builtinDescriptorSha256':builtin_hash,'engineManifestSha256':{}}
        for feature in ('wld','plr'):
            exports=['_terra_build_info_json'];abi={'version':1,'requiredExports':exports,'exportHash':verify.sha(b'_terra_build_info_json\n')}
            manifest=dict(sourceCommit='b'*40,dirty=False,build={'featureSet':feature},abi=abi,artifacts=[])
            for role,extension in [('wrapper','js'),('wasm','wasm')]:
                path=feature+'.'+extension;raw=('synthetic-'+feature+role).encode()
                digest=self.put('artifacts/'+path,raw)
                manifest['artifacts'].append({'role':role,'path':path,'bytes':len(raw),'sha256':digest})
            digest=self.put(feature+'.json',manifest)
            app['engineManifestSha256'][feature]=digest
            self.put(feature+'-runtime.json',{'sourceCommit':'b'*40,'dirty':False,'featureSet':feature,'abiVersion':1})
            identity['engines'].append(dict(featureSet=feature,manifest=feature+'.json',artifactRoot='artifacts',abi=abi,runtimeIdentity=feature+'-runtime.json'))
        self.put('app.json',app)
        report={k:identity[k] for k in ('appCommit','engineSourceCommit','extractorCommit','gameAssemblySha256','resourceManifestSha256','builtinDescriptorSha256','authorityId','sequence')}
        report.update(producer='executed-game-assembly-vs-native',cases=[])
        for category in sorted(verify.CATEGORIES):
            row={'id':category,'category':category,'status':'passed','gameMethod':'synthetic-protocol-test','nativeEntryPoint':'synthetic-protocol-test'}
            for kind in ('input','game','native'):
                path=category+'.'+kind;row[kind+'Path']=path;row[kind+'Sha256']=self.put(path,b'unit-test-only')
            report['cases'].append(row)
        self.put('differential.json',report)
        return identity
    def test_valid_protocol_fixture(self):
        self.assertEqual(verify.verify(self.identity,self.root)['differentialCases'],5)
    def test_identity_tuple_mutations_rejected(self):
        for key in ('appCommit','engineSourceCommit','extractorCommit','gameAssemblySha256','resourceManifestSha256','builtinDescriptorSha256','authorityId','sequence'):
            bad=copy.deepcopy(self.identity);bad[key]=('d'*len(bad[key])) if key!='sequence' else '9007199254740994'
            with self.subTest(key=key),self.assertRaises((ValueError,KeyError)):verify.verify(bad,self.root)
    def test_noncanonical_sequence_rejected(self):
        for sequence in (1,'01','-1',True,'1.0'):
            bad=copy.deepcopy(self.identity);bad['sequence']=sequence
            with self.subTest(sequence=sequence),self.assertRaises(ValueError):verify.verify(bad,self.root)
    def test_wrong_artifact_or_escaping_path_rejected(self):
        self.put('artifacts/wld.wasm',b'tampered')
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)
        with self.assertRaises(ValueError):verify.read(self.root,'../missing')
    def test_incomplete_real_evidence_rejected(self):
        report=json.loads((self.root/'differential.json').read_bytes());report['cases'].pop();self.put('differential.json',report)
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)
    def test_handwritten_oracle_label_rejected(self):
        report=json.loads((self.root/'differential.json').read_bytes());report['producer']='handwritten-reference';self.put('differential.json',report)
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)
    def test_workspace_claim_requires_loaded_abi(self):
        self.identity['engines'][0]['abi']['pixelWorkspace']={'version':1}
        # Manifest mismatch already blocks a source-only ABI declaration.
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)
    def test_report_evidence_tampering_rejected(self):
        self.put('background-rle.game',b'changed')
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)
    def test_revoked_release_rejected(self):
        state=json.loads((self.root/'authority.json').read_bytes());state['revokedManifestSha256']=[self.identity['resourceManifestSha256']];self.put('authority.json',state)
        with self.assertRaises(ValueError):verify.verify(self.identity,self.root)

class ExperimentTests(unittest.TestCase):
    def test_requires_all_variant_proofs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError):experiment.plans({'schema':1,'engineSourceCommit':'a'*40,'variants':[]},root,root)
    def test_memory_gate_preserves_defaults_and_rejects_forged_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);proof={'schema':1,'engineSourceCommit':'a'*40,'variants':[]}
            for opt in ('-O3','-Oz'):
                for simd in (False,True):
                    item=dict(optimization=opt,simd=simd,method='linked-symbols-and-bounded-stack-analysis',staticDataEnd=8<<20,stackBytes=5<<20,
                        heapBase=13<<20,minimumWorkspaceBytes=8<<20,guardBytes=1<<20)
                    for kind in ('wasm','linkMap','stackAnalysis'):
                        if kind=='wasm':
                            def leb(value):
                                out=bytearray()
                                while True:
                                    b=value&127;value>>=7
                                    if value or b&64:out.append(b|128)
                                    else:out.append(b);return bytes(out)
                            def section(ident,value):return bytes([ident])+leb(len(value))+value
                            names=('__data_end','__stack_low','__stack_high','__heap_base')
                            values=(8<<20,8<<20,13<<20,13<<20)
                            raw=b'\x00asm\x01\x00\x00\x00'+section(6,b'\x04'+b''.join(b'\x7f\x00\x41'+leb(v)+b'\x0b' for v in values))
                            raw+=section(7,b'\x04'+b''.join(leb(len(n))+n.encode()+b'\x03'+leb(i) for i,n in enumerate(names)))
                        elif kind=='stackAnalysis':
                            raw=json.dumps({'schema':1,'unresolvedIndirectCalls':0,'entries':['step'],'functions':{'step':{'frameBytes':4096,'dynamicStack':False,'calls':[]}}}).encode()
                        else:raw=b'unit-test-only'
                        (root/kind).write_bytes(raw);item[kind+'Path']=kind;item[kind+'Sha256']=verify.sha(raw)
                    proof['variants'].append(item)
            plan=experiment.plans(proof,root,root)
            self.assertFalse(plan['defaultChanged']);self.assertEqual(len(plan['experiments']),12)
            self.assertEqual(sum(v['status']=='eligible' for v in plan['experiments']),8)
            (root/'wasm').write_bytes(b'changed')
            with self.assertRaises(ValueError):experiment.plans(proof,root,root)
    def test_rejects_recursive_or_dynamic_stack(self):
        base={'schema':1,'unresolvedIndirectCalls':0,'entries':['f'],'functions':{'f':{'frameBytes':10,'dynamicStack':False,'calls':['f']}}}
        with self.assertRaises(ValueError):experiment.bounded_stack(base)
        base['functions']['f']['calls']=[];base['functions']['f']['dynamicStack']=True
        with self.assertRaises(ValueError):experiment.bounded_stack(base)
        base['unresolvedIndirectCalls']=1
        with self.assertRaises(ValueError):experiment.bounded_stack(base)
if __name__=='__main__':unittest.main()
