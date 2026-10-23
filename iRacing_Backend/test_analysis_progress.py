"""The picker's progress estimate: ordered stages, never backwards, never full before the end."""
import unittest
import asyncio

import analysis_progress
from analysis_progress import Job


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now


class JobTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(analysis_progress._rates)
        self.addCleanup(lambda: analysis_progress._rates.update(self.saved))

    def test_the_bar_moves_inside_a_stage_but_waits_at_its_end(self):
        clock = Clock()
        job = Job('a', clock)
        job.laps = 40
        job.enter('laps')
        job.enter('handling')
        start = job.snapshot()['percent']
        clock.now += 5
        middle = job.snapshot()
        self.assertGreater(middle['percent'], start)
        self.assertEqual(middle['label'], '차 거동 비교 중')
        clock.now += 10_000
        stuck = job.snapshot()['percent']
        job.enter('repetition')
        self.assertGreater(job.snapshot()['percent'], stuck)
        self.assertLess(job.snapshot()['percent'], 100)

    def test_earlier_or_repeated_stages_never_move_it_back(self):
        clock = Clock()
        job = Job('b', clock)
        job.laps = 10
        job.enter('repetition')
        before = job.snapshot()['percent']
        job.enter('handling')
        job.enter('repetition')
        job.enter('nonsense')
        self.assertEqual(job.snapshot()['percent'], before)
        self.assertEqual(job.snapshot()['stage'], 'repetition')

    def test_measured_stage_time_teaches_the_next_estimate(self):
        clock = Clock()
        job = Job('c', clock)
        job.laps = 10
        job.enter('handling')
        clock.now += 30           # 3 s per lap, slower than the default
        job.enter('repetition')
        self.assertGreater(analysis_progress._rates['handling'], analysis_progress.DEFAULT_RATES['handling'])

    def test_finished_job_is_full_and_unknown_job_is_none(self):
        token = analysis_progress.start('d')
        analysis_progress.stage('laps', laps=5)
        self.assertLess(analysis_progress.status('d')['percent'], 100)
        analysis_progress.finish(token)
        self.assertEqual(analysis_progress.status('d')['percent'], 100)
        self.assertIsNone(analysis_progress.status('missing'))
        analysis_progress.stage('handling')          # outside a job: nothing happens
        self.assertIsNone(analysis_progress.start(None))

    def test_learning_the_lap_count_does_not_move_a_visible_bar_backwards(self):
        clock = Clock()
        job = Job('long-recording', clock)
        clock.now = 1.
        before = job.snapshot()['percent']
        job.laps = 150
        job.file_gb = 3.
        self.assertGreaterEqual(job.snapshot()['percent'], before)
        job.enter('handling')
        before = job.snapshot()['percent']
        analysis_progress._rates['handling'] = 1000.
        self.assertGreaterEqual(job.snapshot()['percent'], before)

    def test_failure_keeps_the_last_estimate_below_complete_and_stops_the_clock(self):
        clock = Clock()
        job = Job('failed', clock)
        job.enter('handling')
        clock.now = 2.
        before = job.snapshot()['percent']
        job.complete(False)
        clock.now = 100.
        status = job.snapshot()
        self.assertEqual(status['stage'], 'error')
        self.assertEqual(status['percent'], before)
        self.assertLess(status['percent'], 100)
        self.assertEqual(status['elapsed_s'], 2.)

    def test_worker_thread_receives_the_request_job_context(self):
        async def run():
            token = analysis_progress.start('worker')
            try:
                await asyncio.to_thread(analysis_progress.stage, 'handling', 40)
                self.assertEqual(analysis_progress.status('worker')['stage'], 'handling')
            finally:
                analysis_progress.finish(token)
        asyncio.run(run())
        self.assertEqual(analysis_progress.status('worker')['percent'], 100)


if __name__ == '__main__':
    unittest.main()
