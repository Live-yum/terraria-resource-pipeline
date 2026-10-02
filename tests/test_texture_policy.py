import struct
import tempfile
import unittest
from pathlib import Path
from resource_pipeline.texture_batches import TextureBatchPolicy, plan_texture_batches
from resource_pipeline.security import PipelineError

class TextureSchedulingPolicyTests(unittest.TestCase):
    def plan(self,count,expanded=64,**options):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);rows=[]
            for index in range(count):
                name=f'fixture-{index:04d}.xnb'
                # Planner-only synthetic header. No proprietary texture payload.
                body=b'XNBw'+bytes((5,128))+struct.pack('<ii',14,expanded)
                (root/name).write_bytes(body);rows.append({'path':name,'bytes':len(body)})
            return plan_texture_batches(root,rows,TextureBatchPolicy(**options))
    def test_opt_in_window_preserves_order_and_default(self):
        self.assertEqual(256,TextureBatchPolicy().files_per_child)
        old=self.plan(1025);new=self.plan(1025,files_per_child=1024)
        self.assertEqual([256,256,256,256,1],[len(x) for x in old.batches])
        self.assertEqual([1024,1],[len(x) for x in new.batches])
        self.assertEqual([r for b in old.batches for r in b],[r for b in new.batches for r in b])
        self.assertEqual(old.declared_expanded_bytes,new.declared_expanded_bytes)
        self.assertEqual(old.pixel_risk,new.pixel_risk)
    def test_pixel_risk_hard_limit_still_splits_large_window(self):
        plan=self.plan(17,expanded=16_000_000,files_per_child=1024)
        self.assertEqual([16,1],[len(x) for x in plan.batches])
        self.assertEqual(17*16_000_000,plan.pixel_risk)
    def test_other_resource_caps_and_type_bounds_are_unchanged(self):
        for options in [{'files_per_child':1025},{'files_per_child':True},{'files_per_child':0},
                        {'child_pixels':256_000_001},{'image_pixels':16_000_001},
                        {'max_children':257},{'total_pixels':1_024_000_001},
                        {'expanded_bytes':4*1024**3+1}]:
            with self.assertRaises(ValueError):TextureBatchPolicy(**options)
        with self.assertRaises(PipelineError):self.plan(2,expanded=100,files_per_child=1024,expanded_bytes=199)
        with self.assertRaises(PipelineError):self.plan(1025,files_per_child=1024,max_children=1)
    def test_planner_cancellation_remains_per_file(self):
        with tempfile.TemporaryDirectory() as directory:
            def stop():raise PipelineError('cancel-requested')
            with self.assertRaisesRegex(PipelineError,'cancel-requested'):
                plan_texture_batches(Path(directory),[{'path':'missing.xnb','bytes':14}],TextureBatchPolicy(files_per_child=1024),stop)
