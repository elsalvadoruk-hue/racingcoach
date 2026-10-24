"""The shipped trophy asset, race-only result surface and animation boundaries."""
import unittest
from unittest.mock import Mock
from PIL import ImageChops
from scripts.overlay_race_result import RaceResultRenderer, lap_text
from scripts.overlay_sample import OverlaySample


RESULT=dict(id='race:finish',status='FINISHED',start_position=11,finish_position=1,
            provisional=False,best_lap_s=107.236,fastest_lap_s=106.842,best_lap_rank=5,
            recorded_drivers=18,class_drivers=18,best_gap_s=.394)


class ResultRenderingTests(unittest.TestCase):
    def test_shipped_podium_shapes_differ_and_transition_to_persistent_results(self):
        renderer=RaceResultRenderer()
        cups=[]
        for rank in (1,2,3):
            r={**RESULT,'finish_position':rank}
            cups.append(renderer.frame(r,1.,celebrate=True))
            self.assertEqual(cups[-1].size,(390,150))
            summary=renderer.frame(r,6.01,celebrate=True)
            self.assertEqual(summary.tobytes(),renderer.frame(r,50.,celebrate=True).tobytes())
            self.assertNotEqual(cups[-1].tobytes(),summary.tobytes())
            self.assertEqual(summary.getpixel((0,149))[3],0)  # backdrop has independent opacity
        self.assertNotEqual(cups[0].tobytes(),cups[1].tobytes())
        self.assertNotEqual(cups[1].tobytes(),cups[2].tobytes())

    def test_reduced_motion_is_static_and_large_dpi_frames_keep_their_bounds(self):
        renderer=RaceResultRenderer()
        a=renderer.frame(RESULT,.2,2.,1.5,True,True)
        b=renderer.frame(RESULT,2.,2.,1.5,True,True)
        self.assertEqual(a.size,(780,225));self.assertEqual(a.tobytes(),b.tobytes())
        self.assertEqual(lap_text(59.9998),'1:00.000')
        self.assertEqual(lap_text(None),'—')

    def test_result_packets_and_timer_never_draw_pedals_and_do_not_replay_the_podium(self):
        app=Mock(race_celebrations={},scale_x=1.,scale_y=1.,reduced_motion=False,
                 live_view={'race_result':RESULT},race_ticket=None)
        OverlaySample.draw_race_result(app,10.)
        OverlaySample.draw_race_result(app,11.)
        self.assertEqual(app.race_celebrations,{'race:finish':10.})
        self.assertEqual(app.race_result_renderer.frame.call_args.args[1],1.)
        app.draw_trace.assert_not_called();app.draw_gear.assert_not_called()
        app.canvas.itemconfigure.assert_called_with('all',state='hidden')


if __name__=='__main__':unittest.main()
