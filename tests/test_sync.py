import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectcs import sync
from projectcs.db import connect, read_rows

class SyncTests(unittest.TestCase):
    def test_finished_history_imports_and_deduplicates(self):
        class Client:
            def __init__(self, key):
                pass
            def player(self, nickname):
                return {'player_id': nickname}
            def history(self, *args):
                return iter([{'match_id': 'shared', 'status': 'finished'}])
            def get(self, path):
                if path.endswith('/stats'):
                    return {'rounds': [{'round_stats': {'Map': 'de_ancient', 'Rounds': '20'},
                                        'teams': [{'team_id': 'team', 'team_stats': {'Team Win': '1'},
                                                   'players': [{'player_id': 'Grvpe', 'player_stats': {'Kills': '18', 'Deaths': '12'}}]}]}]}
                return {'match_id': 'shared', 'finished_at': 1794490000}
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'test.sqlite'
            def local_connect():
                return connect(db)
            def local_save(match, rows):
                from projectcs.db import save_match
                save_match(match, rows, db)
            with patch.object(sys, 'argv', ['sync', '--since', '2026-08-20']), patch.object(sync, 'FaceitClient', Client), patch.object(sync, 'connect', local_connect), patch.object(sync, 'save_match', side_effect=local_save) as save:
                self.assertEqual(sync.main(), 0)
                save.assert_called_once()
                self.assertEqual(len(read_rows(db)), 1)

if __name__ == '__main__':
    unittest.main()
