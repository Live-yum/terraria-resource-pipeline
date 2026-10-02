import unittest
from resource_pipeline.preflight import _raw_checkpoint
from resource_pipeline.security import PipelineError

class RawCheckpointTests(unittest.TestCase):
    def test_first_poll_and_persistent_cancellation_within_five_milliseconds(self):
        now=[0.0];cancel=[False];polls=[]
        def canceled():
            polls.append(now[0]);return cancel[0]
        check=_raw_checkpoint(canceled,120,clock=lambda:now[0])
        check();cancel[0]=True
        for tick in range(1,5000):
            now[0]=tick/1_000_000;check()
        self.assertEqual(polls,[0.0])
        now[0]=0.005
        with self.assertRaisesRegex(PipelineError,'RAW_JOB_CANCELED'):check()
        self.assertEqual(polls,[0.0,0.005])

    def test_deadline_is_checked_inside_cancellation_poll_interval(self):
        now=[0.0];polls=[]
        check=_raw_checkpoint(lambda:polls.append(now[0]) or False,0.002,clock=lambda:now[0])
        check();now[0]=0.002
        with self.assertRaisesRegex(PipelineError,'RAW_JOB_TIMEOUT'):check()
        self.assertEqual(polls,[0.0])

    def test_existing_cancel_and_cancellation_read_errors_fail_closed(self):
        with self.assertRaisesRegex(PipelineError,'RAW_JOB_CANCELED'):
            _raw_checkpoint(lambda:True,120,clock=lambda:0)()
        def failed():raise OSError('unreadable cancellation state')
        with self.assertRaises(OSError):_raw_checkpoint(failed,120,clock=lambda:0)()

    def test_distant_next_call_polls_and_catches_cancellation(self):
        now=[1.0];cancel=[False]
        check=_raw_checkpoint(lambda:cancel[0],120,clock=lambda:now[0]);check()
        cancel[0]=True;now[0]=10.0
        with self.assertRaisesRegex(PipelineError,'RAW_JOB_CANCELED'):check()

    def test_slow_cancel_stat_cannot_return_past_deadline(self):
        now=[0.0]
        def slow_stat():
            now[0]=2.0;return False
        with self.assertRaisesRegex(PipelineError,'RAW_JOB_TIMEOUT'):
            _raw_checkpoint(slow_stat,1.0,clock=lambda:now[0])()
