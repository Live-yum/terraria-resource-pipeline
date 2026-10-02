from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile

from resource_pipeline.pipeline import Pipeline
from resource_pipeline.preflight import RawInputPreflight
from resource_pipeline.security import PipelineError


def archive(path):
    result=BytesIO()
    with zipfile.ZipFile(result,'w') as z:z.writestr(path,b'original synthetic bytes')
    return BytesIO(result.getvalue()),'game.zip'


class TextureIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pipeline=Pipeline(Path(self.temp.name)/'service')

    def test_server_without_texture_payload_is_explicit(self):
        service=RawInputPreflight(self.pipeline)
        job=service.submit(archive('TerrariaServer.exe'))
        result=service.process(job['id'])
        self.assertEqual(result['textures']['server']['status'],'NO_XNB_PAYLOAD')
        self.assertFalse(result['extractionComplete'])

    def test_combined_input_does_not_require_manual_png_or_second_zip(self):
        class Probe:
            def extract(inner,source,job):
                self.assertTrue((source/'Content/Images/Test.xnb').is_file())
                return {'status':'TEXTURES_DECODED','imageCount':1,'publishable':False}
        service=RawInputPreflight(self.pipeline,texture_extractor=Probe())
        job=service.submit(archive('Content/Images/Test.xnb'))
        result=service.process(job['id'])
        self.assertEqual(result['textures']['server']['status'],'TEXTURES_DECODED')
        self.assertNotIn('MISSING_CLIENT_INPUT',{b['code'] for b in result['blockers']})
        self.assertEqual(result['state'],'BLOCKED')
        with self.assertRaises(PipelineError):self.pipeline.publish(job['id'],'',True)

    def test_missing_converter_and_sandbox_failure_are_not_success(self):
        service=RawInputPreflight(self.pipeline)
        job=service.submit(archive('Content/Images/Test.xnb'))
        result=service.process(job['id'])
        self.assertEqual(result['textures']['server']['status'],'CONVERTER_NOT_INSTALLED')
        class Blocked:
            def extract(self,*args):raise PipelineError('Sandbox unavailable')
        service=RawInputPreflight(self.pipeline,texture_extractor=Blocked())
        job=service.submit(archive('Content/Images/Test.xnb'))
        result=service.process(job['id'])
        self.assertEqual(result['textures']['server']['status'],'BLOCKED')
        self.assertFalse(result['extractionComplete'])
        self.assertIsNone(self.pipeline.current())

    def test_job_polling_compacts_texture_receipts_but_detail_retains_them(self):
        from fastapi.testclient import TestClient
        from resource_pipeline.api import create_app
        app=create_app(Path(self.temp.name)/'api',synchronous=True)
        service=app.state.raw_preflight
        job=service.submit(archive('TerrariaServer.exe'))
        result=service.process(job['id'])
        result['textures']['server'].update(images=[{'input':'synthetic.xnb'}],skipped=[{'input':'font.xnb'}])
        app.state.pipeline.save(result)
        with TestClient(app) as client:
            listing=client.get('/api/jobs').json()[0]['textures']['server']
            self.assertNotIn('images',listing)
            self.assertEqual(listing['imagesCount'],1)
            self.assertEqual(listing['skippedCount'],1)
            detail=client.get('/api/jobs/'+job['id']).json()['textures']['server']
            self.assertEqual(len(detail['images']),1)
