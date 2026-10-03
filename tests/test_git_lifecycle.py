import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resource_pipeline.pipeline import Pipeline
from resource_pipeline.consumer_control import ConsumerReleaseControl


class GitLifecycleTests(unittest.TestCase):
    def test_local_publication_does_not_spawn_receive_maintenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = root / 'trace.jsonl'
            with patch.dict(os.environ, {'GIT_TRACE2_EVENT': str(trace)}):
                pipeline = Pipeline(root / 'job')
                control = ConsumerReleaseControl(pipeline, publisher_id='synthetic-local-demo', synthetic_demo=True)
                review = control.preview_demo(1)
                result = control.publish(review['id'], review['reviewDigest'], True)
                self.assertEqual(result['pointer'], control.current())
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            starts = [row.get('argv', []) for row in events if row.get('event') in ('start', 'child_start')]
            self.assertTrue(any(any('receive-pack' in argument for argument in args) for args in starts))
            self.assertFalse(any('maintenance' in args or 'gc' in args for args in starts),
                             'A Git child must not outlive the owned publication operation')

    def test_reopen_restores_owned_receiver_policy_before_next_push(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = Pipeline(root)
            first.git(['--git-dir', str(first.remote), 'config', 'receive.autogc', 'true'])
            head = first.git(['--git-dir', str(first.remote), 'rev-parse', 'main'])
            second = Pipeline(root)
            self.assertEqual('false', second.git(['--git-dir', str(second.remote), 'config', '--get', 'receive.autogc']))
            self.assertEqual(head, second.git(['--git-dir', str(second.remote), 'rev-parse', 'main']))
