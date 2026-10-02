import random
import unittest
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.static_il import bounded_evidence_json, json_evidence_size

class EvidenceSizeTests(unittest.TestCase):
    def assert_exact(self,value):
        expected=canonical_json(value)
        self.assertEqual(len(expected),json_evidence_size(value,len(expected)))
        self.assertEqual(expected,bounded_evidence_json(value,len(expected)))
        with self.assertRaises(PipelineError):json_evidence_size(value,len(expected)-1)
    def test_scalar_and_chunk_boundaries(self):
        for value in [None,True,False,0,-1,10**100,-0.0,1e-30,'','\x00\b\f\n\r\t\\"','中文😀é','x'*127,'x'*128,'x'*129,'x'*4095+'😀\n'+'x'*4096]:self.assert_exact(value)
    def test_cache_budget_and_repeated_keys_count_every_occurrence(self):
        values=[{f'key-{i}':'value-'+str(i)} for i in range(5000)]
        values += [{'shared':'中文😀','bool':True,'none':None,'number':-9}]*1000
        self.assert_exact(values)
    def test_random_nested_exact_canonical_bytes(self):
        rng=random.Random(731)
        def make(depth=0):
            scalar=[None,True,False,rng.randrange(-100000,100000),rng.random(),''.join(rng.choices('ab中文😀\n\x00\\"',k=rng.randrange(35)))]
            if depth>=4 or rng.randrange(3)==0:return rng.choice(scalar)
            if rng.randrange(2):return [make(depth+1) for _ in range(rng.randrange(5))]
            return {'key'+str(i):make(depth+1) for i in range(rng.randrange(5))}
        for _ in range(300):self.assert_exact(make())
    def test_invalid_values_and_cycles_rejected(self):
        cycle=[];cycle.append(cycle)
        for value in [cycle,{'bad':float('nan')},float('inf'),'\ud800',{1:'value'},object()]:
            with self.assertRaises(PipelineError):json_evidence_size(value,1000000)
        depth=[]
        for _ in range(130):depth=[depth]
        with self.assertRaises(PipelineError):json_evidence_size(depth,1000000)
    def test_each_call_revalidates_and_checkpoints_cancel(self):
        value={'shared':'first'};self.assert_exact(value);value['shared']='changed';self.assert_exact(value)
        count=0
        def checkpoint():
            nonlocal count
            count+=1
            if count==50:raise PipelineError('canceled')
        with self.assertRaisesRegex(PipelineError,'canceled'):json_evidence_size(['shared']*1000,100000,checkpoint)
        self.assertEqual(count,50)
