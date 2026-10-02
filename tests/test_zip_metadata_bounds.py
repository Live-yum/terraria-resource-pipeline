from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import struct
import zipfile
from resource_pipeline.security import ArchiveLimits, PipelineError, extract_zip

class ZipMetadataBounds(TestCase):
    def fixture(self, root, count=2, comment=b''):
        path=root/'input.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.comment=comment
            for i in range(count):archive.writestr(f'entry{i}',b'')
        return path

    def test_large_entry_count_rejected_before_zipfile_construction(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);source=self.fixture(root,2000)
            with patch('resource_pipeline.security.zipfile.ZipFile') as constructor:
                with self.assertRaises(PipelineError):extract_zip(source,root/'out',ArchiveLimits(files=1))
                constructor.assert_not_called()

    def test_spoofed_low_count_is_stream_checked_before_constructor(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);source=self.fixture(root)
            value=bytearray(source.read_bytes());struct.pack_into('<HH',value,len(value)-22+8,1,1);source.write_bytes(value)
            with patch('resource_pipeline.security.zipfile.ZipFile') as constructor:
                with self.assertRaises(PipelineError):extract_zip(source,root/'out',ArchiveLimits(files=1))
                constructor.assert_not_called()

    def test_comment_signature_and_trailing_bytes_are_not_ambiguous(self):
        for suffix in (b'PK\x05\x06'+b'\0'*18,b'ordinary'):
            with TemporaryDirectory() as temp:
                root=Path(temp);source=self.fixture(root,comment=suffix)
                if suffix==b'ordinary':source.write_bytes(source.read_bytes()+b'extra')
                with self.assertRaises(PipelineError):extract_zip(source,root/'out')

    def test_checkpoint_interrupts_before_all_records_or_constructor(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);source=self.fixture(root,2000);calls=0
            def stop():
                nonlocal calls
                calls+=1
                if calls==4:raise PipelineError('cancel')
            with patch('resource_pipeline.security.zipfile.ZipFile') as constructor:
                with self.assertRaisesRegex(PipelineError,'cancel'):extract_zip(source,root/'out',checkpoint=stop)
                constructor.assert_not_called()
            self.assertEqual(calls,4)

    def test_normal_comments_and_file_count_remain_supported(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);source=self.fixture(root,comment=b'normal ZIP comment')
            result=extract_zip(source,root/'out')
            self.assertEqual(result['entryCount'],2)
            self.assertEqual(len(result['files']),2)
