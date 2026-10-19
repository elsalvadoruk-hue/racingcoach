"""Changing measurements must reach an already visible card without restarting it."""
import unittest
from time import monotonic
from unittest.mock import Mock

from scripts.overlay_sample import OverlaySample


class CardRenderingTests(unittest.TestCase):
    def canvas(self):
        app = Mock(card_ticket=None, scale_x=1., scale_y=1.)
        app.font.return_value.measure.return_value = 0
        app.x.side_effect = lambda value: value
        app.fit_text.side_effect = lambda value, *args: value
        return app

    def card(self):
        return {'id': 'same-event', 'session': '레이스', 'name': '급유 설정', 'caption': '피트 진입 전 확인',
                'value': '급유 설정 부족', 'color': 'warning', 'left': '설정 10L', 'right': '필요 18L'}

    def test_new_measurements_replace_existing_text_with_same_event_id(self):
        app, card = self.canvas(), self.card()
        OverlaySample.draw_session_card(app, card)
        app.text.reset_mock()
        app.canvas.reset_mock()
        app.background_canvas.reset_mock()
        card.update(left='설정 12L', right='필요 17L')
        OverlaySample.draw_session_card(app, card)
        texts = [call.args[2] for call in app.text.call_args_list]
        self.assertIn('설정 12L', texts)
        self.assertIn('필요 17L', texts)
        self.assertNotIn('설정 10L', texts)
        app.canvas.delete.assert_called_once_with('session-card')
        app.background_canvas.delete.assert_called_once_with('session-card')

    def test_unchanged_measurements_reuse_rendered_card(self):
        app, card = self.canvas(), self.card()
        OverlaySample.draw_session_card(app, card)
        app.text.reset_mock()
        OverlaySample.draw_session_card(app, {**card, 'at': 20.})
        app.text.assert_not_called()


class LiveCadenceTests(unittest.TestCase):
    def test_notice_change_keeps_existing_graph_items_and_fast_paint_path(self):
        app = Mock(
            display_mode='live', hud_visible=True, live_generation=4,
            live_view={'run_id': 'race'}, celebration_started=None,
            live_structure=(False, None, False, None, (), None, 'race', None),
        )
        OverlaySample.display(app, {'mode': 'live', 'visible': True, 'run_id': 'race',
            'card': {'id': 'lap-1', 'text': '랩 1:21.350'}, 'points': []})
        app.draw.assert_not_called()
        app.draw_trace.assert_called_once_with()

    def test_live_packet_updates_graph_before_acknowledgement(self):
        app = Mock(
            display_mode='live', hud_visible=True, live_generation=4,
            live_view={'run_id':'race'}, celebration_started=None,
            live_structure=(False, None, False, None, (), None, 'race', None),
        )
        view = {'mode':'live', 'visible':True, 'run_id':'race',
                'points':[{'session_time':23., 'throttle':.8, 'brake':0.}]}
        OverlaySample.display(app, view)
        app.draw_trace.assert_called_once_with()
        app.draw.assert_not_called()
        self.assertIs(app.live_view, view)
        self.assertEqual(app.live_generation, 5)

    def test_live_packet_does_not_paint_over_active_celebration(self):
        practice = {'five_successes':True, 'scope':'practice', 'celebration_id':'five'}
        app = Mock(
            display_mode='live', hud_visible=True, live_generation=4,
            live_view={'practice':practice}, celebration_started=12.,
            live_celebrations={'five'},
            live_structure=(True, 'practice', False, None, (), None, None, None),
        )
        OverlaySample.display(app, {'mode':'live', 'visible':True, 'practice':practice})
        app.draw_trace.assert_not_called()
        app.draw.assert_not_called()

    def test_live_graph_keeps_fast_paint_timer_after_preview_result(self):
        # A preview can leave the scene selector on its result page.  Live
        # practice still needs the animation timer; otherwise it falls back to
        # the 100 ms idle timer and visibly steps between telemetry snapshots.
        app = Mock(
            closed=False, display_mode='live', live_received=monotonic(),
            hud_visible=True, celebration_started=None,
            live_view={'practice': {'phase': 'DURING'}},
        )
        app.scene.get.return_value = 'result'
        OverlaySample.tick(app)
        delay = app.root.after.call_args.args[0]
        self.assertLessEqual(delay, 20)
        app.draw_trace.assert_called_once()


class FooterRenderingTests(unittest.TestCase):
    def app(self, notice=None):
        app = Mock(notice_ticket=None, scale_x=1., scale_y=1.,
                   live_view={'comparison': True, 'feedback': {'delta_s': .12, 'brake_delta_m': 3},
                              'card': notice}, live_labels={'reference': 'left', 'detail': 'right'})
        app.fit_text.side_effect = lambda text, *args: text
        app.font.return_value.measure.return_value = 100
        app.x.side_effect = lambda x: x
        return app

    def test_only_notices_center_and_tint_existing_footer_then_restore_original_labels(self):
        notice = {'text': '이번 랩 피트 · 잔여 연료 0.9랩', 'kind': 'fuel', 'color': 'warning'}
        app = self.app(notice)
        OverlaySample.update_live_labels(app)
        self.assertEqual([c.kwargs['text'] for c in app.canvas.itemconfigure.call_args_list
                          if 'text' in c.kwargs], ['', ''])
        OverlaySample.draw_footer_notice(app, notice)
        self.assertEqual(app.rect.call_args.args[:5], (6, 131, 378, 18, 'notice_warning'))
        self.assertEqual(app.text.call_args.args[:3], (195, 146, notice['text']))
        self.assertEqual(app.text.call_args.kwargs['anchor'], 's')
        app.live_view['card'] = None
        app.canvas.reset_mock()
        OverlaySample.update_live_labels(app)
        self.assertEqual([c.kwargs['text'] for c in app.canvas.itemconfigure.call_args_list
                          if 'text' in c.kwargs], ['직전 코너 +0.12초', '제동 시작 3m 늦음'])
        OverlaySample.draw_footer_notice(app, None)
        app.canvas.delete.assert_called_with('footer-notice')
        self.assertIsNone(app.notice_ticket)

    def test_same_notice_refreshes_text_without_rebuilding_unchanged_tint(self):
        app = self.app()
        notice = {'text': '랩 1:21.350', 'kind': 'lap', 'color': 'text'}
        OverlaySample.draw_footer_notice(app, notice)
        app.text.reset_mock()
        OverlaySample.draw_footer_notice(app, notice)
        app.text.assert_not_called()
        OverlaySample.draw_footer_notice(app, {**notice, 'text': '앞차 접근 · 1.8 → 0.7초', 'kind': 'closing'})
        self.assertEqual(app.rect.call_args.args[4], 'notice_traffic')
        self.assertEqual(app.text.call_args.args[2], '앞차 접근 · 1.8 → 0.7초')


if __name__ == '__main__':
    unittest.main()
