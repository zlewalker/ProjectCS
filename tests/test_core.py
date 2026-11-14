import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import sample_row
from projectcs import db as db_mod
from projectcs.data import frame, lineup_summary, load_data, player_summary, summary, team_maps
from projectcs.db import (
    add_review,
    apply_demo_trades,
    backup,
    compute_round_trades,
    connect,
    existing_match_ids,
    read_records,
    read_rows,
    reprocess_stored_matches,
    save_match,
)
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
        rows = [
            sample_row(deaths=10, rounds=10),
            sample_row(deaths=6, rounds=30),
            sample_row(deaths=None, rounds=20),
        ]
        result = player_summary(frame(rows)).iloc[0]
        self.assertEqual(result["DPR"], 0.4)
        result = player_summary(frame([sample_row(deaths=None)])).iloc[0]
        self.assertIsNone(result["DPR"])

    def test_untraded_deaths_percentage_paired_and_null_when_absent(self):
        only_api = player_summary(frame([sample_row(deaths=15, untraded_deaths=None)])).iloc[0]
        self.assertIsNone(only_api["Morti senza trade %"])

        rows = [
            sample_row(match_id="m1", deaths=10, untraded_deaths=7, traded_deaths=3),
            sample_row(match_id="m2", deaths=10, untraded_deaths=5, traded_deaths=5),
            sample_row(match_id="m3", deaths=15, untraded_deaths=None, traded_deaths=None),
        ]
        result = player_summary(frame(rows)).iloc[0]
        self.assertEqual(result["Morti senza trade %"], 60.0)

    def test_demo_trade_computation_5s_window_and_persistence(self):
        events = [
            {"round_no": 1, "time_sec": 10.0, "victim_id": "p1", "victim_team": "T1", "attacker_id": "e1", "attacker_team": "T2"},
            {"round_no": 1, "time_sec": 14.0, "victim_id": "e1", "victim_team": "T2", "attacker_id": "p2", "attacker_team": "T1"},
            {"round_no": 2, "time_sec": 20.0, "victim_id": "p1", "victim_team": "T1", "attacker_id": "e2", "attacker_team": "T2"},
            {"round_no": 2, "time_sec": 26.0, "victim_id": "e2", "victim_team": "T2", "attacker_id": "p2", "attacker_team": "T1"},
        ]
        trades = compute_round_trades(events, window_seconds=5.0)
        self.assertEqual(trades["p1"], {"traded_deaths": 1, "untraded_deaths": 1})

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "trade.sqlite"
            row = sample_row(match_id="m-demo", map_no=0, player_id="p1", nickname="Senshe", deaths=2)
            save_match({"match_id": "m-demo"}, [row], db)
            res = apply_demo_trades("m-demo", 0, events, path=db, window_seconds=5.0)
            self.assertEqual(res["status"], "complete")
            self.assertEqual(res["updated"], 1)

            loaded = frame(read_rows(db))
            self.assertEqual(loaded.iloc[0]["traded_deaths"], 1.0)
            self.assertEqual(loaded.iloc[0]["untraded_deaths"], 1.0)
            self.assertEqual(player_summary(loaded).iloc[0]["Morti senza trade %"], 50.0)
            self.assertEqual(summary(loaded)["demo_coverage"], 100.0)

            save_match({"match_id": "m-demo"}, [row], db)
            reloaded = frame(read_rows(db))
            self.assertEqual(reloaded.iloc[0]["untraded_deaths"], 1.0)

    def test_empty_and_incomplete_demo_events_keep_metrics_null(self):
        # Eventi senza timestamp / round / team devono restare None (niente trade falsi)
        missing_ts = [
            {"round_no": 1, "victim_id": "p1", "victim_team": "T1", "attacker_id": "e1", "attacker_team": "T2"},
            {"round_no": 1, "victim_id": "e1", "victim_team": "T2", "attacker_id": "p2", "attacker_team": "T1"},
        ]
        self.assertEqual(
            compute_round_trades(missing_ts)["p1"],
            {"traded_deaths": None, "untraded_deaths": None},
        )

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "demo_guard.sqlite"
            row = sample_row(match_id="m-guard", map_no=0, player_id="p1", nickname="Senshe", deaths=2)
            save_match({"match_id": "m-guard"}, [row], db)

            # 1) Lista eventi vuota -> non tocca i record, lascia None e coverage invariato
            empty_res = apply_demo_trades("m-guard", 0, [], path=db)
            self.assertEqual(empty_res["status"], "empty")
            loaded = frame(read_rows(db))
            self.assertTrue(loaded["untraded_deaths"].isna().all())
            self.assertEqual(loaded.iloc[0]["coverage"], "Statistiche match")
            self.assertEqual(summary(loaded)["demo_coverage"], 0.0)

            # 2) Eventi parziali (solo le morti del giocatore tracciato senza morti avversarie) -> mantiene l'analisi precedente
            full_events = [
                {"round_no": 1, "time_sec": 10.0, "victim_id": "p1", "victim_team": "T1", "attacker_id": "e1", "attacker_team": "T2"},
                {"round_no": 1, "time_sec": 12.0, "victim_id": "e1", "victim_team": "T2", "attacker_id": "p2", "attacker_team": "T1"},
                {"round_no": 2, "time_sec": 20.0, "victim_id": "p1", "victim_team": "T1", "attacker_id": "e2", "attacker_team": "T2"},
                {"round_no": 2, "time_sec": 22.0, "victim_id": "e2", "victim_team": "T2", "attacker_id": "p2", "attacker_team": "T1"},
            ]
            one_death = [full_events[0]]
            partial_res = apply_demo_trades("m-guard", 0, one_death, path=db)
            self.assertEqual(partial_res["status"], "incomplete")
            loaded_after = frame(read_rows(db))
            self.assertTrue(loaded_after["untraded_deaths"].isna().all())
            self.assertEqual(loaded_after.iloc[0]["coverage"], "Statistiche match")

            # 3) Salviamo un feed completo valido -> Morti senza trade % = 0.0
            ok_res = apply_demo_trades("m-guard", 0, full_events, path=db)
            self.assertEqual(ok_res["status"], "complete")
            self.assertEqual(player_summary(frame(read_rows(db))).iloc[0]["Morti senza trade %"], 0.0)

            # 4) Reimport parziale (solo morti di p1, senza morti nemiche) -> status incomplete, preserva lo 0.0 precedente
            partial_reimport = apply_demo_trades("m-guard", 0, [full_events[0], full_events[2]], path=db)
            self.assertEqual(partial_reimport["status"], "incomplete")
            self.assertEqual(player_summary(frame(read_rows(db))).iloc[0]["Morti senza trade %"], 0.0)

            # 5) Reimport invalido (attacker_team mancante) -> status invalid, preserva i dati precedenti
            invalid_reimport = apply_demo_trades("m-guard", 0, [dict(full_events[0], attacker_team=None)], path=db)
            self.assertEqual(invalid_reimport["status"], "invalid")
            self.assertEqual(read_rows(db)[0]["untraded_deaths"], 0.0)

            # 6) Copertura mappa parziale: se su una stessa mappa-squadra un membro ha trade demo e un altro no, demo_coverage = 0.0
            mixed_map = frame([
                sample_row(player_id="p1", traded_deaths=1, untraded_deaths=0, coverage="Statistiche match + trade demo"),
                sample_row(player_id="p2", traded_deaths=None, untraded_deaths=None, coverage="Statistiche match"),
            ])
            self.assertEqual(summary(mixed_map)["demo_coverage"], 0.0)

    def test_advanced_faceit_metrics_denominators_and_local_reprocess(self):
        match = {"match_id": "m-adv", "finished_at": 1700000000}
        stats = {
            "rounds": [{
                "round_stats": {"Map": "de_anubis", "Rounds": "20"},
                "teams": [{
                    "team_id": "t1",
                    "team_stats": {
                        "Team Win": "1",
                        "Final Score": "13",
                        "First Half Score": "8",
                        "Second Half Score": "5",
                        "Overtime score": "0",
                    },
                    "players": [{
                        "player_id": "p1",
                        "nickname": "Senshe",
                        "player_stats": {
                            "Kills": "20", "Deaths": "10", "Assists": "4", "ADR": "95.0",
                            "Headshots": "12", "Damage": "1900",
                            "First Kills": "3", "Entry Count": "5", "Entry Wins": "3",
                            "Sniper Kills": "5", "Flash Count": "10", "Flash Successes": "6",
                            "Enemies Flashed": "8", "Utility Count": "5", "Utility Damage": "150",
                            "1v1Count": "2", "1v1Wins": "1", "1v2Count": "1", "1v2Wins": "1",
                            "Double Kills": "3", "Triple Kills": "1", "Quadro Kills": "0", "Penta Kills": "0",
                        },
                    }],
                }],
            }]
        }
        match["_projectcs_stats"] = stats
        rows = normalize_match(match, stats, {"p1": "Senshe"})
        df = frame(rows)
        ps = player_summary(df).iloc[0]
        self.assertEqual(ps["HS %"], 60.0)
        self.assertEqual(ps["Tentativi apertura"], 5)
        self.assertEqual(ps["Aperture %"], 60.0)
        self.assertEqual(ps["Freq. aperture %"], 25.0)
        self.assertEqual(ps["Morti in apertura %"], 20.0)
        self.assertEqual(ps["UDR"], 7.5)
        self.assertEqual(ps["Flash lanciate"], 10)
        self.assertEqual(ps["Flash %"], 60.0)
        self.assertEqual(ps["Nemici flash/r"], 0.4)
        self.assertEqual(ps["Sniper KPR"], 0.25)
        self.assertEqual(ps["Sniper %"], 25.0)
        self.assertEqual(ps["Tentativi 1v1"], 2)
        self.assertEqual(ps["1v1 %"], 50.0)
        self.assertEqual(ps["Tentativi 1v2"], 1)
        self.assertEqual(ps["1v2 %"], 100.0)
        self.assertEqual(ps["Multikill"], 4)

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "reprocess.sqlite"
            save_match(match, rows, db)
            info = reprocess_stored_matches(db)
            self.assertEqual(info["matches"], 1)
            self.assertEqual(info["player_maps"], 1)
            self.assertEqual(info["failed"], [])

    def test_partial_multikill_returns_none(self):
        # Se una categoria multikill su 4 e' None, Multikill non deve sommare parziali
        row = sample_row(double_kills=2, triple_kills=None, quadro_kills=0, penta_kills=0)
        ps = player_summary(frame([row])).iloc[0]
        self.assertIsNone(ps["Multikill"])

        # Se anche una sola mappa del campione ha categorie multikill incomplete, il totale aggregato e' None
        complete_row = sample_row(match_id="m1", double_kills=2, triple_kills=1, quadro_kills=0, penta_kills=0)
        partial_row = sample_row(match_id="m2", double_kills=1, triple_kills=None, quadro_kills=0, penta_kills=0)
        ps_mixed = player_summary(frame([complete_row, partial_row])).iloc[0]
        self.assertIsNone(ps_mixed["Multikill"])

    def test_read_rows_does_not_reprocess_and_caches_by_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "pure_read.sqlite"
            row = sample_row(match_id="m-legacy", entry_count=None)
            save_match({"match_id": "m-legacy"}, [row], db)
            with patch("projectcs.db.reprocess_stored_matches") as reproc:
                first = read_rows(db)
                second = read_rows(db)
                reproc.assert_not_called()
            self.assertEqual(len(first), 1)
            self.assertEqual(first, second)

            # Aggiornando il DB, la cache si invalida automaticamente
            row2 = sample_row(match_id="m-legacy-2", entry_count=4)
            save_match({"match_id": "m-legacy-2"}, [row2], db)
            updated = read_rows(db)
            self.assertEqual(len(updated), 2)

    def test_commit_operational_error_propagates_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "lock.sqlite"
            save_match({"match_id": "init"}, [sample_row(match_id="init")], db)

            class FailingCommitConn(sqlite3.Connection):
                calls = 0

                def commit(self):
                    FailingCommitConn.calls += 1
                    raise sqlite3.OperationalError("simulated commit failure")

            orig_connect = sqlite3.connect

            def custom_connect(*args, **kwargs):
                kwargs["factory"] = FailingCommitConn
                return orig_connect(*args, **kwargs)

            with patch("projectcs.db.sqlite3.connect", side_effect=custom_connect):
                with self.assertRaises(sqlite3.OperationalError) as ctx:
                    with connect(db, readonly=False) as conn:
                        conn.execute(
                            "INSERT INTO reviews(created_at, kind, category, observation, status) VALUES (?, ?, ?, ?, ?)",
                            ("now", "Positivo", "Call", "Rollback test", "Aperta"),
                        )
                self.assertIn("simulated commit failure", str(ctx.exception))
                self.assertEqual(FailingCommitConn.calls, 1)

            # Verifica che il record non sia stato persistito
            self.assertEqual(len(read_records("reviews", db)), 0)

    def test_schema_v1_migration_preserves_rows_and_adds_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "v1.sqlite"
            conn = sqlite3.connect(db)
            try:
                conn.executescript(
                    """
                    CREATE TABLE schema_meta (version INTEGER PRIMARY KEY);
                    INSERT INTO schema_meta VALUES (1);
                    CREATE TABLE players (
                        player_id TEXT PRIMARY KEY, nickname TEXT NOT NULL, steam_id TEXT
                    );
                    INSERT INTO players VALUES ('p1', 'Senshe', NULL);
                    CREATE TABLE matches (
                        match_id TEXT PRIMARY KEY, started_at TEXT, source TEXT NOT NULL,
                        raw_json TEXT NOT NULL, imported_at TEXT NOT NULL
                    );
                    CREATE TABLE player_maps (
                        match_id TEXT NOT NULL, map_no INTEGER NOT NULL, team_id TEXT NOT NULL,
                        player_id TEXT NOT NULL, nickname TEXT NOT NULL, date TEXT NOT NULL,
                        map TEXT NOT NULL, roster_count INTEGER NOT NULL, won INTEGER,
                        rounds INTEGER, kills REAL, deaths REAL, assists REAL, adr REAL,
                        headshots REAL, score_for INTEGER, score_against INTEGER,
                        source TEXT NOT NULL, coverage TEXT NOT NULL,
                        PRIMARY KEY(match_id, map_no, team_id, player_id)
                    );
                    """
                )
                good_stats = {
                    "rounds": [{
                        "round_stats": {"Map": "de_ancient", "Rounds": "20"},
                        "teams": [{
                            "team_id": "t1",
                            "team_stats": {"Team Win": "1"},
                            "players": [{
                                "player_id": "p1",
                                "nickname": "Senshe",
                                "player_stats": {"Kills": "18", "Deaths": "12", "ADR": "88.0", "Entry Count": "4", "Entry Wins": "2"},
                            }],
                        }],
                    }]
                }
                good_match = {"match_id": "raw-good", "finished_at": 1700000000, "_projectcs_stats": good_stats}
                conn.execute(
                    "INSERT INTO matches VALUES (?, ?, ?, ?, ?)",
                    ("raw-good", "2026-11-01T20:00:00Z", "faceit", json.dumps(good_match), "2026-11-01T21:00:00Z"),
                )
                conn.execute(
                    "INSERT INTO matches VALUES (?, ?, ?, ?, ?)",
                    ("raw-missing", "2026-11-02T20:00:00Z", "faceit", json.dumps({"match_id": "raw-missing"}), "2026-11-02T21:00:00Z"),
                )
                conn.execute(
                    "INSERT INTO player_maps VALUES ('raw-good', 0, 't1', 'p1', 'Senshe', '2026-11-01T20:00:00Z', 'de_ancient', 5, 1, 20, 18, 12, 4, 88.0, 9, 13, 7, 'faceit', 'Statistiche match')"
                )
                conn.execute(
                    "INSERT INTO player_maps VALUES ('raw-missing', 0, 't1', 'p1', 'Senshe', '2026-11-02T20:00:00Z', 'de_mirage', 5, 1, 20, 15, 10, 3, 75.0, 7, 13, 7, 'faceit', 'Statistiche match')"
                )
                conn.commit()
            finally:
                conn.close()

            # Prima della migrazione in scrittura: read_rows su schema v1 misto conserva entrambe le righe
            pre_migration = read_rows(db)
            self.assertEqual([r["match_id"] for r in pre_migration], ["raw-good", "raw-missing"])
            self.assertEqual(pre_migration[0]["entry_count"], 4.0)
            self.assertIsNone(pre_migration[1]["entry_count"])

            # Connessione in scrittura migra a v2 preservando entrambe le righe
            with connect(db, readonly=False) as mconn:
                version = mconn.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0]
                self.assertEqual(version, 2)

            rows = read_rows(db)
            self.assertEqual(len(rows), 2)
            self.assertIn("entry_count", rows[0])

    def test_existing_match_ids_excludes_incomplete_matches_including_missing_adr(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "partial.sqlite"
            # 1) Match parziale: raw_json senza _projectcs_stats e player_maps vuota
            save_match({"match_id": "incomplete"}, [], db)
            self.assertEqual(existing_match_ids(db), set())

            # 2) Match con solo ADR mancante -> non deve essere considerato completo
            stats_no_adr = {
                "rounds": [{
                    "round_stats": {"Map": "de_mirage", "Rounds": "24"},
                    "teams": [{
                        "team_id": "t1",
                        "team_stats": {"Team Win": "1"},
                        "players": [{"player_id": "p1", "nickname": "Senshe", "player_stats": {"Kills": "15", "Deaths": "12"}}],
                    }],
                }]
            }
            match_no_adr = {"match_id": "adr-missing", "finished_at": 1700000000, "_projectcs_stats": stats_no_adr}
            rows_no_adr = normalize_match(match_no_adr, stats_no_adr, {"p1": "Senshe"})
            save_match(match_no_adr, rows_no_adr, db)
            self.assertNotIn("adr-missing", existing_match_ids(db))

            # 3) Match completo: raw_json con _projectcs_stats.rounds e player_maps con rounds, won, kills, deaths, adr
            complete_match = {
                "match_id": "complete",
                "_projectcs_stats": {"rounds": [{"round_stats": {"Map": "de_mirage", "Rounds": "24"}}]},
            }
            save_match(complete_match, [sample_row(match_id="complete")], db)
            self.assertEqual(existing_match_ids(db), {"complete"})

    def test_lineup_summary_groups_core_combinations(self):
        rows = [
            sample_row(match_id="m1", player_id="p1", nickname="Grvpe", roster_count=3, won=1, score_for=13, score_against=8),
            sample_row(match_id="m1", player_id="p2", nickname="oSpEcTeRo", roster_count=3, won=1, score_for=13, score_against=8),
            sample_row(match_id="m1", player_id="p3", nickname="LEWALKER", roster_count=3, won=1, score_for=13, score_against=8),
        ]
        lineups = lineup_summary(frame(rows), min_members=3)
        self.assertEqual(len(lineups), 1)
        self.assertEqual(lineups.iloc[0]["Formazione"], "Grvpe · oSpEcTeRo · LEWALKER")
        self.assertEqual(lineups.iloc[0]["Differenza round"], 5)

    def test_published_snapshot_loads_without_local_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / "player_maps.csv"
            frame([sample_row()]).to_csv(snapshot, index=False)
            loaded = load_data(db_path=root / "absent.sqlite", snapshot_path=snapshot)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.iloc[0]["kills"], 16)
            self.assertEqual(loaded.iloc[0]["source"], "faceit")
            self.assertFalse((root / "absent.sqlite").exists())

    def test_team_counts_are_not_player_counts(self):
        data = frame([sample_row(player_id="a"), sample_row(player_id="b")])
        self.assertEqual(len(team_maps(data)), 1)
        self.assertEqual(len(data), 2)

    def test_opposite_teams_are_separate(self):
        match = {"match_id": "m", "finished_at": 1700000000}
        stats = {
            "rounds": [{
                "round_stats": {"Map": "de_ancient", "Rounds": "20"},
                "teams": [
                    {"team_id": "a", "team_stats": {"Team Win": "1"}, "players": [{"player_id": "p1", "player_stats": {"Kills": "20"}}]},
                    {"team_id": "b", "team_stats": {"Team Win": "0"}, "players": [{"player_id": "p2", "player_stats": {}}]},
                ],
            }]
        }
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
