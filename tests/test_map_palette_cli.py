from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import importlib.util
import json
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('original_map_cli',ROOT/'scripts/extract_map_palette.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


class MapPaletteCliTests(unittest.TestCase):
    def invoke(self,path):
        output=StringIO()
        with redirect_stdout(output):code=cli.main(['--source','original.dll','--xna','original-xna.dll','--output',str(path)])
        return code,json.loads(output.getvalue())

    def test_private_output_and_summary_redaction(self):
        proof={'status':'ORIGINAL_TEST_MODEL','factScope':'PRE_LEGEND_BOUNDARY','private':'ORIGINAL_PRIVATE_ROW',
               'rows':{'tiles':[{'optionCount':1,'packedRgba':[123]}],'walls':[{'optionCount':0,'packedRgba':[]}]}}
        with tempfile.TemporaryDirectory() as tmp,patch.object(cli,'extract_map_palette_semantics',return_value=proof):
            path=Path(tmp)/'new';code,summary=self.invoke(path)
            self.assertEqual(0,code);self.assertNotIn('ORIGINAL_PRIVATE_ROW',json.dumps(summary))
            self.assertIn('ORIGINAL_PRIVATE_ROW',(path/'map-palette-proof.json').read_text())
            for key in ('executedInput','sourceProductionComplete','consumerReleaseReady','publicationApproved','wholeInitializerProven'):
                self.assertFalse(summary[key])
            self.assertEqual(1,summary['tileOptions'])

    def test_traversal_checkout_existing_and_symlink_outputs_reject_before_read(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cli,'extract_map_palette_semantics') as extract:
            parent=Path(tmp);(parent/'link').symlink_to(parent,target_is_directory=True)
            for target in (parent,parent/'link'/'new',ROOT/'private-proof-test',Path('/tmp/..')/ROOT.relative_to('/')/'private-proof-test'):
                with self.subTest(target=target):self.assertEqual(1,self.invoke(target)[0])
            extract.assert_not_called()

    def test_unsupported_model_is_a_bounded_json_rejection(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cli,'extract_map_palette_semantics',side_effect=cli.ILUnsupported('MAP_UNSUPPORTED_SOURCE_PROFILE')):
            path=Path(tmp)/'new';code,summary=self.invoke(path)
            self.assertEqual(1,code);self.assertEqual('REJECTED',summary['status'])
            self.assertEqual('MAP_UNSUPPORTED_SOURCE_PROFILE',summary['error']);self.assertFalse(path.exists())

    def test_failed_model_has_no_output(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cli,'extract_map_palette_semantics',side_effect=cli.PipelineError('rejected')):
            path=Path(tmp)/'new';self.assertEqual(1,self.invoke(path)[0]);self.assertFalse(path.exists())


if __name__=='__main__':unittest.main()
