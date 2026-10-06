"""Execution-protocol tests use fake adapters, never actual game expectations."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('differential',Path(__file__).resolve().parents[1]/'run-game-differential.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        assembly=b'synthetic-protocol-only';(self.root/'game.exe').write_bytes(assembly)
        (self.root/'dependency.bin').write_bytes(b'dependency')
        (self.root/'adapter.py').write_text('''import argparse,hashlib,json,sys
p=argparse.ArgumentParser();p.add_argument('side');p.add_argument('--mode',default='same');p.add_argument('--category');p.add_argument('--input');p.add_argument('--assembly');p.add_argument('--source-commit');p.add_argument('--scratch');a=p.parse_args()
if a.mode=='fail': print('controlled failure',file=sys.stderr);sys.exit(7)
b=open(a.input,'rb').read();result={'value': 2 if a.mode=='different' else 1}
print(json.dumps(dict(side=a.side,inputSha256=hashlib.sha256(b).hexdigest(),gameAssemblySha256=hashlib.sha256(open(a.assembly,'rb').read()).hexdigest(),engineSourceCommit=a.source_commit,entryPoint='fake-protocol-adapter',result=result)))
''')
        self.config=dict(appCommit='a'*40,engineSourceCommit='b'*40,extractorCommit='c'*40,
            gameAssembly='game.exe',gameAssemblySha256=m.sha(assembly),resourceManifestSha256='d'*64,
            builtinDescriptorSha256='e'*64,authorityId='fake',sequence='1',timeoutSeconds=5,cases=[],
            gameAdapter=[sys.executable,str(self.root/'adapter.py'),'game'],nativeAdapter=[sys.executable,str(self.root/'adapter.py'),'native'])
        for category in sorted(m.CATEGORIES):
            raw=json.dumps({'resourceManifestSha256':'d'*64}).encode();(self.root/(category+'.json')).write_bytes(raw)
            self.config['cases'].append(dict(id=category,category=category,input=category+'.json',inputSha256=m.sha(raw),
                gameMethod='fake-protocol-adapter',nativeEntryPoint='fake-protocol-adapter',dependencies={'dependency.bin':m.sha(b'dependency')}))
    def test_executes_independent_adapters_and_retains_evidence(self):
        report=m.run(self.config,self.root,self.root/'result')
        self.assertEqual(len(report['cases']),5)
        self.assertTrue(all(c['status']=='passed' for c in report['cases']))
        self.assertEqual(len(list((self.root/'result').glob('*.stderr'))),10)
    def test_different_output_is_not_a_pass(self):
        self.config['nativeAdapter']+=['--mode','different']
        with self.assertRaisesRegex(ValueError,'outputs differ'):m.run(self.config,self.root,self.root/'result')
        report=json.loads((self.root/'result/differential-report.json').read_bytes())
        self.assertTrue(all(c['status']=='failed' for c in report['cases']))
    def test_adapter_failure_retains_logs(self):
        self.config['nativeAdapter']+=['--mode','fail']
        with self.assertRaisesRegex(ValueError,'adapter failed'):m.run(self.config,self.root,self.root/'result')
        report=json.loads((self.root/'result/differential-report.json').read_bytes())
        self.assertEqual(report['cases'][0]['status'],'execution-failed')
        self.assertEqual(report['cases'][0]['returnCode'],7)
        self.assertIn('controlled failure',(self.root/'result/background-rle.native.stderr').read_text())
    def test_missing_category_or_changed_dependency_blocks_execution(self):
        self.config['cases'].pop()
        with self.assertRaisesRegex(ValueError,'five categories'):m.run(self.config,self.root,self.root/'result')
        self.setUp()
        (self.root/'dependency.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'dependency mismatch'):m.run(self.config,self.root,self.root/'result')
    def test_existing_evidence_directory_is_not_overwritten(self):
        (self.root/'result').mkdir()
        with self.assertRaisesRegex(ValueError,'new private directory'):m.run(self.config,self.root,self.root/'result')
if __name__=='__main__':unittest.main()
