import unittest
import os
from unittest.mock import patch
from pathlib import Path
from streamlit.testing.v1 import AppTest
from fixtures import app_rows, sample_row

APP = str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')

class AppTests(unittest.TestCase):
    def setUp(self):
        self.snapshot_mode = self.enterContext(patch('projectcs.data.using_snapshot', return_value=False))
        self.rows = self.enterContext(patch('projectcs.data.read_rows', return_value=app_rows()))
        self.enterContext(patch('projectcs.db.read_records', return_value=[]))
        self.enterContext(patch('projectcs.db.backup', return_value=None))

    def test_navigation_uses_only_faceit_data(self):
        app = AppTest.from_file(APP).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.sidebar.radio), 1)
        self.assertEqual(app.metric[0].value, '1')
        for page in ['Giocatori', 'Partite', 'Revisione', 'Allenamento', 'Panoramica']:
            app.sidebar.radio[0].set_value(page).run(timeout=30)
            self.assertEqual(len(app.exception), 0, page)

    def test_empty_archive_has_no_fallback(self):
        self.rows.return_value = []
        app = AppTest.from_file(APP).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 0)
        self.assertTrue(any('vuoto' in item.value for item in app.info))

    def test_owner_gate_and_review(self):
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'test-only-owner-password'}), patch('projectcs.db.add_review') as save:
            app = AppTest.from_file(APP).run(timeout=30)
            app.sidebar.radio[0].set_value('Revisione').run()
            self.assertFalse(any(x.label == 'Cosa avete osservato?' for x in app.text_area))
            app.sidebar.text_input[0].set_value('wrong')
            app.sidebar.button[0].click().run()
            self.assertNotIn('owner_digest', app.session_state)
            app.session_state['login_after'] = 0
            app.sidebar.text_input[0].set_value('test-only-owner-password')
            app.sidebar.button[0].click().run()
            self.assertIn('owner_digest', app.session_state)
            self.assertTrue(any(x.label == 'Cosa avete osservato?' for x in app.text_area))
            app.text_area[0].set_value('Osservazione di test')
            next(button for button in app.button if button.label == 'Salva osservazione').click().run()
            self.assertEqual(len(app.exception), 0)
            save.assert_called_once()
            self.assertEqual(save.call_args.args[4], 'Osservazione di test')

    def test_published_snapshot_disables_owner_access(self):
        self.snapshot_mode.return_value = True
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'test-only-owner-password'}):
            app = AppTest.from_file(APP).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.sidebar.text_input), 0)
            app.sidebar.radio[0].set_value('Revisione').run()
            self.assertEqual(len(app.text_area), 0)

    def test_analysis_views_and_history_window(self):
        self.rows.return_value = [
            sample_row(match_id=f'ui-{i:02}', nickname='Grvpe', roster_count=3)
            for i in range(25)]
        app = AppTest.from_file(APP).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.slider[0].value, (6, 25))
        app.slider[0].set_value((1, 10)).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        for view in ['Aperture', 'AWP e trade', 'Utility', 'Clutch', 'Sintesi']:
            next(control for control in app.radio if control.key == 'overview_stats').set_value(view).run(timeout=30)
            self.assertEqual(len(app.exception), 0, view)

    def test_long_reviews_render_as_wrapping_cards(self):
        with patch('projectcs.db.read_records', return_value=[{
            'kind': 'Da migliorare', 'category': 'Call', 'status': 'Da verificare',
            'observation': 'Nota molto lunga ' * 200, 'action': 'Azione ' * 100}]):
            app = AppTest.from_file(APP).run(timeout=30)
            app.sidebar.radio[0].set_value('Revisione').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('Nota molto lunga' in item.value for item in app.markdown))

    def test_filters_and_player(self):
        app = AppTest.from_file(APP).run(timeout=30)
        app.selectbox[0].set_value('Quintetto · 5').run()
        self.assertEqual(len(app.exception), 0)
        app.sidebar.radio[0].set_value('Giocatori').run()
        app.selectbox[1].set_value('Senshe').run()
        self.assertEqual(len(app.exception), 0)

if __name__ == '__main__':
    unittest.main()
