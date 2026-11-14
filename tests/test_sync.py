import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import sample_row
from projectcs import sync
from projectcs.db import connect, read_rows, save_match


class SyncTests(unittest.TestCase):
    def test_finished_history_imports_deduplicates_and_skips_existing(self):
        class Client:
            calls = 0

            def __init__(self, key):
                pass

            def player(self, nickname):
                return {"player_id": nickname}

            def history(self, *args):
                return iter([{"match_id": "shared", "status": "finished"}])

            def get(self, path):
                Client.calls += 1
                if path.endswith("/stats"):
                    return {
                        "rounds": [{
                            "round_stats": {"Map": "de_ancient", "Rounds": "20"},
                            "teams": [{
                                "team_id": "team",
                                "team_stats": {"Team Win": "1"},
                                "players": [{
                                    "player_id": "Grvpe",
                                    "player_stats": {"Kills": "18", "Deaths": "12", "ADR": "86.5"},
                                }],
                            }],
                        }]
                    }
                return {"match_id": "shared", "finished_at": 1794490000}

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "test.sqlite"

            def local_connect(path=db):
                return connect(db)

            def local_save(match, rows):
                save_match(match, rows, db)

            with patch.object(sys, "argv", ["sync", "--since", "2026-08-20"]), \
                 patch.object(sync, "FaceitClient", Client), \
                 patch.object(sync, "connect", local_connect), \
                 patch.object(sync, "save_match", side_effect=local_save) as save:
                self.assertEqual(sync.main(), 0)
                save.assert_called_once()
                self.assertEqual(len(read_rows(db)), 1)
                first_calls = Client.calls

                # Secondo sync senza --force: salta la partita completa gia' in archivio
                self.assertEqual(sync.main(), 0)
                self.assertEqual(Client.calls, first_calls)

            # Con --force riscarica la partita
            with patch.object(sys, "argv", ["sync", "--since", "2026-08-20", "--force"]), \
                 patch.object(sync, "FaceitClient", Client), \
                 patch.object(sync, "connect", local_connect), \
                 patch.object(sync, "save_match", side_effect=local_save) as save_forced:
                self.assertEqual(sync.main(), 0)
                save_forced.assert_called_once()

    def test_incremental_sync_retries_incomplete_matches(self):
        class Client:
            calls = 0

            def __init__(self, key):
                pass

            def player(self, nickname):
                return {"player_id": nickname}

            def history(self, *args):
                return iter([{"match_id": "partial", "status": "finished"}])

            def get(self, path):
                Client.calls += 1
                if path.endswith("/stats"):
                    return {
                        "rounds": [{
                            "round_stats": {"Map": "de_mirage", "Rounds": "22"},
                            "teams": [{
                                "team_id": "team",
                                "team_stats": {"Team Win": "1"},
                                "players": [{
                                    "player_id": "Grvpe",
                                    "player_stats": {"Kills": "20", "Deaths": "14", "ADR": "90.0"},
                                }],
                            }],
                        }]
                    }
                return {"match_id": "partial", "finished_at": 1794490000}

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "partial_sync.sqlite"
            # Salviamo un match incompleto (senza player_maps e senza _projectcs_stats)
            save_match({"match_id": "partial"}, [], db)

            def local_connect(path=db):
                return connect(db)

            def local_save(match, rows):
                save_match(match, rows, db)

            with patch.object(sys, "argv", ["sync", "--since", "2026-08-20"]), \
                 patch.object(sync, "FaceitClient", Client), \
                 patch.object(sync, "connect", local_connect), \
                 patch.object(sync, "save_match", side_effect=local_save):
                self.assertEqual(sync.main(), 0)
                self.assertGreater(Client.calls, 0)
                self.assertEqual(len(read_rows(db)), 1)

    def test_reprocess_local_failure_returns_nonzero_and_skips_export(self):
        # 1) Eccezione diretta da reprocess_stored_matches -> exit code != 0 e nessun export
        with patch.object(sys, "argv", ["sync", "--reprocess-local", "--export-snapshot"]), \
             patch.object(sync, "reprocess_stored_matches", side_effect=RuntimeError("db locked")), \
             patch.object(sync, "export_snapshot") as export_mock:
            rc = sync.main()
            self.assertNotEqual(rc, 0)
            export_mock.assert_not_called()

        # 2) Fallimenti parziali riportati in info["failed"] -> exit code != 0 e nessun export
        with patch.object(sys, "argv", ["sync", "--reprocess-local", "--export-snapshot"]), \
             patch.object(
                 sync,
                 "reprocess_stored_matches",
                 return_value={"matches": 0, "player_maps": 0, "failed": [{"match_id": "m1", "error": "corrupt"}]},
             ), \
             patch.object(sync, "export_snapshot") as export_mock:
            rc2 = sync.main()
            self.assertNotEqual(rc2, 0)
            export_mock.assert_not_called()

    def test_atomic_export_preserves_previous_snapshot_on_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "export.sqlite"
            snap_dir = root / "published"
            snap_csv = snap_dir / "player_maps.csv"
            snap_meta = snap_dir / "metadata.json"

            save_match({"match_id": "m1"}, [sample_row(match_id="m1", kills=19)], db)

            meta = sync.export_snapshot(db_path=db, published_dir=snap_dir)
            self.assertEqual(meta["player_maps"], 1)
            self.assertTrue(snap_csv.exists())
            self.assertTrue(snap_meta.exists())
            original_csv = snap_csv.read_text(encoding="utf-8")
            original_meta = json.loads(snap_meta.read_text(encoding="utf-8"))
            self.assertEqual(original_meta["player_maps"], 1)

            # Simuliamo un errore durante la generazione del metadata temporaneo: lo snapshot precedente deve restare valido
            with patch("projectcs.sync.json.dumps", side_effect=ValueError("serialization error")):
                with self.assertRaises(ValueError):
                    sync.export_snapshot(db_path=db, published_dir=snap_dir)

            self.assertEqual(snap_csv.read_text(encoding="utf-8"), original_csv)
            self.assertEqual(json.loads(snap_meta.read_text(encoding="utf-8"))["player_maps"], 1)
            # Nessun file temporaneo residuo
            self.assertEqual(list(snap_dir.glob("*.tmp")), [])

            # Simuliamo un fallimento della seconda os.replace (metadata.json): il CSV viene ripristinato alla versione 1
            save_match({"match_id": "m2"}, [sample_row(match_id="m2", kills=21)], db)
            orig_replace = sync.os.replace
            calls = []

            def fail_second_replace(src, dst):
                calls.append(str(dst))
                if len(calls) == 2:
                    raise OSError("metadata replacement failure")
                return orig_replace(src, dst)

            with patch.object(sync.os, "replace", side_effect=fail_second_replace):
                with self.assertRaises(OSError):
                    sync.export_snapshot(db_path=db, published_dir=snap_dir)

            self.assertEqual(snap_csv.read_text(encoding="utf-8"), original_csv)
            self.assertEqual(json.loads(snap_meta.read_text(encoding="utf-8"))["player_maps"], 1)
            self.assertEqual(list(snap_dir.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
