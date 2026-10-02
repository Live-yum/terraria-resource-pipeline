import io
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient
from resource_pipeline.api import create_app
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError

class RealProducerTests(unittest.TestCase):
    def test_static_and_texture_sources_join_but_never_claim_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);server=root/'server';client=root/'client';server.mkdir();client.mkdir()
            (server/'TerrariaServer.exe').write_bytes(b'original synthetic inert server')
            (client/'Item_1.xnb').write_bytes(b'original synthetic source')
            inspect=lambda path, **options: {'input':{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},'idFamilies':{'items':{'records':[{'id':1,'name':'Example'},{'id':2,'name':'Other'}]}},'gameVersionEvidence':{'status':'DECLARED_IN_ASSEMBLY','trusted':False}}
            receipt={'client':{'status':'TEXTURES_DECODED','images':[{'input':'Item_1.xnb','output':'Item_1.png','sourceSha256':'1'*64,'sha256':'2'*64,'width':1,'height':1,'surfaceFormat':0}]}}
            value=RawEvidenceProducer(inspect).produce({'server':server,'client':client},receipt,root/'output')
            self.assertEqual(value['familyCoverage']['items']['idsWithoutDirectImage'],[2])
            self.assertEqual(value['familyCoverage']['items']['decodedImages'],1)
            self.assertFalse(value['extractionComplete']);self.assertFalse(value['publishable'])
            self.assertTrue((root/'output/version-adapter-manifest.json').is_file())
            self.assertTrue((root/'output/server-0.json').is_file())
            with self.assertRaises(PipelineError): RawEvidenceProducer(inspect).produce({}, {},root/'output')

    def test_http_installs_real_stage_and_exposes_only_blocked_review(self):
        with tempfile.TemporaryDirectory() as temp:
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as archive: archive.writestr('TerrariaServer.exe',b'not a CLI assembly')
            with TestClient(create_app(Path(temp),synchronous=True)) as client:
                result=client.post('/api/raw-jobs',files={'server_file':('server.zip',package.getvalue(),'application/zip')})
                self.assertEqual(result.status_code,202);job=result.json();identity=job['id']
                self.assertEqual(job['producerEvidence']['status'],'PARTIAL')
                self.assertEqual(len(job['producerEvidence']['rejectedServerMetadata']),1)
                self.assertFalse(job['extractionComplete'])
                compact=client.get(f'/api/jobs/{identity}?compact=true').json()
                self.assertNotIn('files',compact['sources']['server']['inventory'])
                self.assertNotIn('idsWithoutDirectImage',compact['producerEvidence']['familyCoverage']['items'])
                review=client.get(f'/api/raw-jobs/{identity}/review').json()
                self.assertFalse(review['reviewable']);self.assertIsNone(review['reviewDigest'])
                self.assertEqual(client.post(f'/api/raw-jobs/{identity}/publish',json={'reviewDigest':'0'*64,'confirmed':True}).status_code,409)
                self.assertIsNone(client.get('/api/current').json())


    def test_cancel_before_processing_and_retry_use_fresh_verified_job(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(Path(temp),synchronous=True);service=app.state.raw_preflight
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as archive: archive.writestr('TerrariaServer.exe',b'original inert bytes')
            old=service.submit(server_file=(io.BytesIO(package.getvalue()),'server.zip'))
            with TestClient(app) as client:
                result=client.post(f"/api/raw-jobs/{old['id']}/cancel")
                self.assertTrue(result.json()['cancelRequested'])
                canceled=service.process(old['id']);self.assertEqual(canceled['state'],'CANCELED')
                result=client.post(f"/api/raw-jobs/{old['id']}/retry")
                self.assertEqual(result.status_code,202);new=result.json()
                self.assertNotEqual(new['id'],old['id']);self.assertEqual(new['state'],'BLOCKED')
                self.assertEqual(new['sources']['server']['archiveSha256'],old['sources']['server']['archiveSha256'])
                self.assertEqual(app.state.pipeline.get(old['id'])['state'],'CANCELED')
                path=app.state.pipeline.root/'jobs'/old['id']/'server.zip';path.write_bytes(b'changed')
                self.assertEqual(client.post(f"/api/raw-jobs/{old['id']}/retry").status_code,409)


    def test_unexpected_extractor_error_is_terminal_and_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(Path(temp),synchronous=True);service=app.state.raw_preflight
            class Broken:
                def extract(self,*args,**kwargs):raise OSError('private path must not leak')
            service.texture_extractor=Broken()
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as archive:archive.writestr('Content/Images/Test.xnb',b'inert bytes')
            submitted=service.submit(client_file=(io.BytesIO(package.getvalue()),'client.zip'))
            result=service.process(submitted['id'])
            self.assertEqual(result['state'],'BLOCKED');self.assertEqual(result['error'],'RAW_PROCESSING_FAILED')
            self.assertNotIn('private path',str(result));self.assertFalse(result['extractionComplete'])
            retried=service.retry(submitted['id']);self.assertNotEqual(retried['id'],submitted['id'])
