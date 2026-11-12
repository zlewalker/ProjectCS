import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectcs.data import load_data, frame, player_summary, team_maps
from fixtures import sample_row
from projectcs.db import add_review, backup, read_records, read_rows, save_match
from projectcs.faceit import normalize_match

class AnalyticsTests(unittest.TestCase):
    def test_weighted_adr_and_nulls(self):
        rows = [sample_row(match_id=f"test-{i}") for i in range(3)]
        for r in rows:
            r["nickname"] = "X"
        for r, adr, rounds in zip(rows, [100, 50, None], [10, 30, 20]):
            r["adr"], r["rounds"] = adr, rounds
        result = player_summary(frame(rows)).iloc[0]
        self.assertEqual(result["ADR"], 62.5)

    def test_deaths_per_round_uses_paired_available_counts(self):
        rows = [sample_row(deaths=10, rounds=10),
                sample_row(deaths=6, rounds=30),
                sample_row(deaths=None, rounds=20)]
        result = player_summary(frame(rows)).iloc[0]
        self.assertEqual(result['DPR'], .4)
        result = player_summary(frame([sample_row(deaths=None)])).iloc[0]
        self.assertIsNone(result['DPR'])

    def test_published_snapshot_loads_without_local_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / 'player_maps.csv'
            frame([sample_row()]).to_csv(snapshot, index=False)
            with patch('projectcs.data.DB_PATH', root / 'absent.sqlite'), patch('projectcs.data.SNAPSHOT_PATH', snapshot):
                loaded = load_data()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.iloc[0]['kills'], 16)
            self.assertEqual(loaded.iloc[0]['source'], 'faceit')
            self.assertFalse((root / 'absent.sqlite').exists())

    def test_team_counts_are_not_player_counts(self):
        data = frame([sample_row(player_id="a"), sample_row(player_id="b")])
        self.assertEqual(len(team_maps(data)), 1)
        self.assertEqual(len(data), 2)

    def test_opposite_teams_are_separate(self):
        match = {"match_id": "m", "finished_at": 1700000000}
        stats = {"rounds": [{"round_stats": {"Map": "de_ancient", "Rounds": "20"},
                            "teams": [
                                {"team_id": "a", "team_stats": {"Team Win": "1"}, "players": [
                                    {"player_id": "p1", "player_stats": {"Kills": "20"}}]},
                                {"team_id": "b", "team_stats": {"Team Win": "0"}, "players": [
                                    {"player_id": "p2", "player_stats": {}}]}]}]}
        rows = normalize_match(match, stats, {"p1": "A", "p2": "B"})
        self.assertEqual([r["roster_count"] for r in rows], [1, 1])
        self.assertEqual([r["won"] for r in rows], [1, 0])
        self.assertIsNone(rows[1]["kills"])
        self.assertEqual(len(team_maps(frame(rows))), 2)

    def test_idempotent_storage_and_reviews(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite"
            rows = [sample_row()]
            match = {"match_id": rows[0]["match_id"]}
            save_match(match, rows, path)
            save_match(match, rows, path)
            self.assertEqual(len(read_rows(path)), 1)
            add_review(None, None, "Positivo", "Call", "Test", "Da verificare", "Azione", path)
            self.assertEqual(read_records("reviews", path)[0]["observation"], "Test")
            self.assertTrue(backup(path).startswith(b"SQLite format 3"))

if __name__ == "__main__":
    unittest.main()
