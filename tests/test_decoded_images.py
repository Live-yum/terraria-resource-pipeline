from io import BytesIO
from pathlib import Path
import json
import tempfile
import unittest
import zipfile

from PIL import Image, PngImagePlugin

from resource_pipeline.decoded_images import inspect_export, inspect_png
from resource_pipeline.security import ArchiveLimits, PipelineError


def png(color=(1, 2, 3, 255), metadata=False):
    result = BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text('Comment', 'Synthetic fixture')
    Image.new('RGBA', (2, 3), color).save(result, format='PNG', pnginfo=info)
    return result.getvalue()


class DecodedImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, entries):
        path = self.root / 'images.zip'
        with zipfile.ZipFile(path, 'w') as package:
            for name, data in entries:
                package.writestr(name, data)
        return path

    def test_real_decode_and_duplicate_pixels(self):
        archive = self.archive([('Images/Item_1.png', png()), ('Images/Alias.png', png(metadata=True))])
        before = archive.read_bytes()
        result = inspect_export(archive, declared_version='1.4.5.8')
        self.assertEqual(result['imageCount'], 2)
        self.assertEqual(result['uniquePixelImages'], 1)
        self.assertEqual(result['duplicatePixelImages'], 1)
        self.assertNotEqual(result['images'][0]['sha256'], result['images'][1]['sha256'])
        self.assertEqual(archive.read_bytes(), before)
        for key in ('publishable', 'extractionComplete', 'versionVerified', 'executedInput'):
            self.assertFalse(result[key])
        self.assertEqual(result['images'][0]['height'], 3)

    def test_pixel_change_is_not_duplicate(self):
        result = inspect_export(self.archive([('a.png', png()), ('b.png', png((3, 2, 1, 255)))]))
        self.assertEqual(result['uniquePixelImages'], 2)

    def test_truncated_image(self):
        with self.assertRaises(PipelineError):
            inspect_png(png()[:40])

    def test_wrong_format(self):
        data = BytesIO()
        Image.new('RGB', (1, 1)).save(data, format='JPEG')
        with self.assertRaises(PipelineError):
            inspect_png(data.getvalue())

    def test_path_traversal(self):
        with self.assertRaises(PipelineError):
            inspect_export(self.archive([('../a.png', png())]))

    def test_non_png(self):
        with self.assertRaises(PipelineError):
            inspect_export(self.archive([('run.py', b'raise RuntimeError()')]))

    def test_empty(self):
        with self.assertRaises(PipelineError):
            inspect_export(self.archive([]))

    def test_byte_limit(self):
        with self.assertRaises(PipelineError):
            inspect_export(self.archive([('a.png', png())]), limits=ArchiveLimits(archive_bytes=1))

    def test_version_not_source_proof(self):
        with self.assertRaises(PipelineError):
            inspect_export(self.archive([('a.png', png())]), declared_version='verified')

    def test_symlink(self):
        path = self.archive([('a.png', png())])
        link = self.root / 'linked.zip'
        link.symlink_to(path)
        with self.assertRaises(PipelineError):
            inspect_export(link)

    def test_apng_rejected(self):
        data = BytesIO()
        a, b = Image.new('RGBA', (2, 2), 'red'), Image.new('RGBA', (2, 2), 'blue')
        a.save(data, format='PNG', save_all=True, append_images=[b])
        with self.assertRaises(PipelineError):
            inspect_png(data.getvalue())

    def test_inventory_cannot_be_a_publication_envelope(self):
        from resource_pipeline.pipeline import Pipeline
        inventory = inspect_export(self.archive([('a.png', png())]))
        forged = self.archive([('resource-bundle.json', json.dumps(inventory).encode())])
        pipeline = Pipeline(self.root / 'service')
        job = pipeline.submit(forged.read_bytes(), 'inventory.zip')
        result = pipeline.process(job['id'])
        self.assertNotEqual(result['state'], 'READY_FOR_REVIEW')
        self.assertIsNone(pipeline.current())
        with self.assertRaises(PipelineError):
            pipeline.publish(job['id'], '', True)

    def test_per_image_pixel_limit(self):
        from unittest.mock import patch
        with patch('resource_pipeline.decoded_images.MAX_PIXELS', 5):
            with self.assertRaises(PipelineError):
                inspect_png(png())

    def test_total_pixel_limit(self):
        from unittest.mock import patch
        # A mocked decoder tests aggregation without allocating a huge image.
        row = {'width': 16000, 'height': 16000, 'rgbaSha256': '0' * 64}
        with patch('resource_pipeline.decoded_images.inspect_png', return_value=row):
            with self.assertRaisesRegex(PipelineError, 'pixel budget'):
                inspect_export(self.archive([('a.png', png()), ('b.png', png())]))

    def test_large_synthetic_export_with_default_entry_limits(self):
        image = png()
        archive = self.archive([(f'Images/Synthetic_{i}.png', image) for i in range(16000)])
        result = inspect_export(archive)
        self.assertEqual(result['imageCount'], 16000)
        self.assertEqual(result['uniquePixelImages'], 1)
        self.assertFalse(result['publishable'])
