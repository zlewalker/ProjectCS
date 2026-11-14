import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fixtures import sample_row
from projectcs.data import (
    demo_economy_summary,
    demo_side_summary,
    demo_tactical_summary,
    frame,
    load_demo_events,
    load_demo_rounds,
    prepare_heatmap_events,
)
from projectcs.db import read_demo_status, read_rows, save_demo_analysis, save_match
from projectcs.demos import (
    DemoRateLimitDeferred,
    FaceitDemoDownloader,
    RateLimitController,
    download_and_process_queue,
    infer_match_and_map_from_filename,
    ingest_local_demos,
    pending_demo_queue,
    world_to_radar,
)
from projectcs.sync import export_snapshot
from projectcs.ui import build_radar_heatmap_chart


class DemoPipelineTests(unittest.TestCase):
    def test_filename_inference_and_radar_calibration(self):
        self.assertEqual(
            infer_match_and_map_from_filename("1-a43d778d-1-1.dem.zst"),
            ("1-a43d778d", 0),
        )
        self.assertEqual(
            infer_match_and_map_from_filename("1-a43d778d-1-2.dem.gz"),
            ("1-a43d778d", 1),
        )
        self.assertEqual(
            infer_match_and_map_from_filename("match_abc_map2.json"),
            ("match_abc", 2),
        )
        rx, ry = world_to_radar(-300.0, 600.0, "de_ancient")
        self.assertTrue(0 <= rx <= 1024)
        self.assertTrue(0 <= ry <= 1024)
        self.assertEqual(world_to_radar(None, 100.0, "ancient"), (None, None))
        self.assertEqual(world_to_radar(-300.0, 600.0, "de_train"), (None, None))

    def test_rate_limit_controller_paces_waits_for_reset_and_defers_long_waits(self):
        clock = [1700000000.0]
        slept = []

        def fake_time():
            return clock[0]

        def fake_sleep(sec):
            slept.append(round(float(sec), 2))
            clock[0] += float(sec)

        ctrl = RateLimitController(
            min_interval_seconds=10.0,
            max_wait_seconds=60.0,
            max_retries=3,
            base_backoff_seconds=15.0,
            time_fn=fake_time,
            sleep_fn=fake_sleep,
        )

        # 1) Pacing tra due richieste ravvicinate (dopo 3s attende i restanti 7s)
        ctrl.pace_before_request()
        clock[0] += 3.0
        ctrl.pace_before_request()
        self.assertEqual(slept, [7.0])

        # 2) Risposta 429 con Retry-After <= max_wait_seconds -> attende 25s e prosegue
        ctrl.handle_rate_limit_response({"Retry-After": "25"}, attempt=1)
        self.assertEqual(slept[-1], 25.0)

        # 3) Risposta 200 con X-RateLimit-Remaining=0 e X-RateLimit-Reset tra 18s -> attende proattivamente 18s
        ctrl.inspect_headers_proactive({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(clock[0] + 18.0)})
        self.assertEqual(slept[-1], 18.0)

        # 4) Risposta 429 con Retry-After=180s (> max_wait_seconds=60s) -> solleva DemoRateLimitDeferred
        with self.assertRaises(DemoRateLimitDeferred) as ctx:
            ctrl.handle_rate_limit_response({"Retry-After": "180"}, attempt=1)
        self.assertEqual(ctx.exception.wait_seconds, 180.0)

    def test_queue_prioritizes_core_and_handles_rate_limit_and_expired_demos(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "queue.sqlite"
            demos_dir = root / "demos"

            # Partita 1: solo queue (roster_count=1), recente
            m_solo = {
                "match_id": "m-solo",
                "demo_url": ["https://cdn.faceit.com/cs2/m-solo-1-1.dem.gz"],
                "_projectcs_stats": {"rounds": [{"round_stats": {"Map": "de_ancient", "Rounds": "20"}}]},
            }
            save_match(m_solo, [sample_row(match_id="m-solo", date="2026-11-13T20:00:00Z", roster_count=1, deaths=1)], db)

            # Partita 2: 5-stack (roster_count=5), recente -> deve avere priorità su m-solo!
            m_five = {
                "match_id": "m-five",
                "demo_url": ["https://cdn.faceit.com/cs2/m-five-1-1.dem.gz"],
                "_projectcs_stats": {"rounds": [{"round_stats": {"Map": "de_anubis", "Rounds": "20"}}]},
            }
            save_match(m_five, [sample_row(match_id="m-five", date="2026-11-12T20:00:00Z", roster_count=5, deaths=1)], db)

            # Partita 3: vecchia di 60 giorni -> oltre la finestra di conservazione demo di 30gg
            m_old = {
                "match_id": "m-old",
                "demo_url": ["https://cdn.faceit.com/cs2/m-old-1-1.dem.gz"],
                "_projectcs_stats": {"rounds": [{"round_stats": {"Map": "de_dust2", "Rounds": "20"}}]},
            }
            save_match(m_old, [sample_row(match_id="m-old", date="2026-09-01T20:00:00Z", roster_count=5, deaths=1)], db)

            now_ts = 1794686400.0  # 2026-11-14T20:00:00Z
            queue = pending_demo_queue(path=db, max_age_days=30, now_ts=now_ts)
            self.assertEqual([q["match_id"] for q in queue], ["m-five", "m-solo"])

            # Simuliamo download: m-five restituisce 404 (expired), m-solo va in rate-limit differito
            session = MagicMock()
            resp_404 = MagicMock(status_code=404, ok=False, headers={})
            resp_429 = MagicMock(status_code=429, ok=False, headers={"Retry-After": "300"})
            session.post.side_effect = [resp_404, resp_429]

            ctrl = RateLimitController(
                min_interval_seconds=0.0,
                max_wait_seconds=60.0,
                time_fn=lambda: now_ts,
                sleep_fn=lambda s: None,
            )
            dl = FaceitDemoDownloader(api_key="test-key", rate_controller=ctrl, session=session)
            res = download_and_process_queue(
                db_path=db,
                demos_dir=demos_dir,
                downloader=dl,
                limit=5,
                max_age_days=30,
                now_ts=now_ts,
            )
            self.assertEqual(res["expired"], 1)
            self.assertTrue(res["rate_limited"])
            # Nessun file temporaneo rimasto in demos/
            self.assertEqual(list(demos_dir.glob("*")), [])

            # Al run immediatamente successivo: m-five è expired e m-solo ha next_retry_at futuro -> coda vuota!
            queue_after = pending_demo_queue(path=db, max_age_days=30, now_ts=now_ts + 60.0)
            self.assertEqual(queue_after, [])

            # Dopo 301 secondi (passato il reset del rate-limit): m-solo torna in coda!
            queue_after_reset = pending_demo_queue(path=db, max_age_days=30, now_ts=now_ts + 301.0)
            self.assertEqual([q["match_id"] for q in queue_after_reset], ["m-solo"])

    def test_local_demo_ingestion_auto_deletes_on_success_and_keeps_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "local_ingest.sqlite"
            demos_dir = root / "demos"
            demos_dir.mkdir()

            save_match({"match_id": "m-ok"}, [sample_row(match_id="m-ok", player_id="p1", deaths=1, rounds=2)], db)
            save_match({"match_id": "m-bad"}, [sample_row(match_id="m-bad", player_id="p1", deaths=2, rounds=2)], db)

            valid_bundle = {
                "events": [
                    {
                        "round_no": 1,
                        "time_sec": 10.0,
                        "victim_id": "p1",
                        "victim_name": "Senshe",
                        "victim_team": "A",
                        "attacker_id": "e1",
                        "attacker_name": "Enemy1",
                        "attacker_team": "B",
                        "victim_x": -500.0,
                        "victim_y": 800.0,
                        "attacker_x": -300.0,
                        "attacker_y": 950.0,
                    },
                    {
                        "round_no": 1,
                        "time_sec": 13.0,
                        "victim_id": "e1",
                        "victim_name": "Enemy1",
                        "victim_team": "B",
                        "attacker_id": "p2",
                        "attacker_name": "Grvpe",
                        "attacker_team": "A",
                        "victim_x": -300.0,
                        "victim_y": 950.0,
                        "attacker_x": -480.0,
                        "attacker_y": 820.0,
                    },
                ],
                "rounds": [
                    {"round_no": 1, "side": "T", "won": 1, "first_kill": 0, "first_death": 1, "plant_site": "A", "buy_type": "Pistol", "equip_value": 4000},
                    {"round_no": 2, "side": "CT", "won": 1, "first_kill": 1, "first_death": 0, "retake_attempted": 1, "retake_won": 1, "buy_type": "Full Buy", "equip_value": 24500},
                ],
            }

            ok_file = demos_dir / "m-ok-1-1.json"
            ok_file.write_text(json.dumps(valid_bundle), encoding="utf-8")

            # File incompleto (solo 1 morte invece di 2 per m-bad)
            bad_file = demos_dir / "m-bad-1-1.json"
            bad_file.write_text(json.dumps({"events": valid_bundle["events"]}), encoding="utf-8")

            res = ingest_local_demos(demos_dir=demos_dir, db_path=db, delete_after_ingest=True)
            self.assertEqual(res["processed"], 2)
            self.assertEqual(res["completed"], 1)
            self.assertEqual(res["deleted"], 1)

            # Il file completato è stato cancellato automaticamente; quello incompleto è stato conservato
            self.assertFalse(ok_file.exists())
            self.assertTrue(bad_file.exists())

            # Verifica metriche tattiche, heatmap e snapshot export
            pub_dir = root / "published"
            meta = export_snapshot(db_path=db, published_dir=pub_dir)
            self.assertEqual(meta["demo_events"], 2)
            self.assertEqual(meta["demo_rounds"], 2)

            maps_df = frame(read_rows(db))
            ev_df = load_demo_events(db_path=db)
            rd_df = load_demo_rounds(db_path=db)

            sides = demo_side_summary(rd_df, maps_df)
            self.assertEqual(len(sides), 1)
            self.assertEqual(sides.iloc[0]["Round T"], 1)
            self.assertEqual(sides.iloc[0]["Win T %"], 100.0)
            self.assertEqual(sides.iloc[0]["Round CT"], 1)
            self.assertEqual(sides.iloc[0]["Win CT %"], 100.0)

            tact = demo_tactical_summary(rd_df, maps_df)
            self.assertEqual(tact.iloc[0]["Round 5v4"], 1)
            self.assertEqual(tact.iloc[0]["Conversione 5v4 %"], 100.0)
            self.assertEqual(tact.iloc[0]["Round 4v5"], 1)
            self.assertEqual(tact.iloc[0]["Recupero 4v5 %"], 100.0)
            self.assertEqual(tact.iloc[0]["Plant A"], 1)
            self.assertEqual(tact.iloc[0]["Post-plant A %"], 100.0)
            self.assertEqual(tact.iloc[0]["Retake tentati"], 1)
            self.assertEqual(tact.iloc[0]["Retake %"], 100.0)

            econ = demo_economy_summary(rd_df, maps_df)
            self.assertEqual(len(econ), 2)

            hm = prepare_heatmap_events(ev_df, maps_df)
            self.assertEqual(len(hm), 1)
            self.assertEqual(int((~hm["victim_id"].isin(maps_df["player_id"])).sum()), 0)
            self.assertEqual(hm.iloc[0]["is_opening"], 1)
            self.assertEqual(hm.iloc[0]["is_traded"], 1)

            hm_all = prepare_heatmap_events(ev_df, maps_df, include_opponents=True)
            self.assertEqual(len(hm_all), 2)
            fig = build_radar_heatmap_chart(hm, "ancient", mode="victim", show_vectors=True)
            self.assertGreaterEqual(len(fig.data), 2)

    def test_compressed_gz_demo_decompresses_and_cleans_up_temp_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "gz_test.sqlite"
            demos_dir = root / "demos"
            demos_dir.mkdir()

            save_match({"match_id": "m-gz"}, [sample_row(match_id="m-gz", player_id="p1", deaths=1, rounds=2)], db)

            gz_path = demos_dir / "m-gz-1-1.dem.gz"
            with gzip.open(gz_path, "wb") as f:
                f.write(b"FAKE_CS2_DEM_PAYLOAD")

            def fake_dem_parser(dem_file, match_id, map_no, db_path=None):
                self.assertEqual(Path(dem_file).read_bytes(), b"FAKE_CS2_DEM_PAYLOAD")
                return {
                    "events": [
                        {"round_no": 1, "time_sec": 10.0, "victim_id": "p1", "victim_team": "A", "attacker_id": "e1", "attacker_team": "B"},
                        {"round_no": 1, "time_sec": 18.0, "victim_id": "e1", "victim_team": "B", "attacker_id": "p2", "attacker_team": "A"},
                    ],
                    "rounds": [
                        {"round_no": 1, "side": "T", "won": 1},
                        {"round_no": 2, "side": "CT", "won": 1},
                    ],
                }

            res = ingest_local_demos(
                demos_dir=demos_dir,
                db_path=db,
                delete_after_ingest=True,
                demo_parser=fake_dem_parser,
            )
            self.assertEqual(res["completed"], 1)
            self.assertEqual(res["deleted"], 1)
            # Né il .dem.gz né il .dem temporaneo estratto devono restare su disco
            self.assertEqual(list(demos_dir.iterdir()), [])
            self.assertEqual(read_rows(db)[0]["untraded_deaths"], 1.0)
            self.assertEqual(read_demo_status(db)[0]["status"], "complete")

    def test_atomicity_stateless_merge_team_filter_and_binary_parser(self):
        import pandas as pd
        from types import SimpleNamespace
        from unittest.mock import patch
        from projectcs.demos import parse_raw_dem_with_demoparser2

        events = [
            {"round_no": 1, "time_sec": 10.0, "victim_id": "p1", "victim_team": "A", "attacker_id": "e1", "attacker_team": "B", "victim_x": -500.0, "victim_y": 800.0, "attacker_x": -300.0, "attacker_y": 950.0},
            {"round_no": 1, "time_sec": 13.0, "victim_id": "e1", "victim_team": "B", "attacker_id": "p2", "attacker_team": "A", "victim_x": -300.0, "victim_y": 950.0, "attacker_x": -480.0, "attacker_y": 820.0},
        ]
        rounds = [{"round_no": 1, "side": "T", "won": 1}, {"round_no": 2, "side": "CT", "won": 1}]

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            public = root / "published"

            # 1) Se i round mancano quando max_rounds > 0, save_demo_analysis rifiuta come 'incomplete' senza scrivere parzialmente
            db_inc = root / "incomplete.sqlite"
            save_match({"match_id": "inc", "demo_url": ["https://example.invalid/inc.dem.gz"]}, [sample_row(match_id="inc", team_id="A", player_id="p1", deaths=1, rounds=2, date="2026-11-13T20:00:00Z")], db_inc)
            inc_res = save_demo_analysis("inc", 0, events, rounds=[], path=db_inc)
            self.assertEqual(inc_res["status"], "incomplete")
            self.assertEqual(len(pending_demo_queue(path=db_inc)), 1)

            # 2) Due runner stateless consecutivi preservano e fondono lo storico demo in published/
            for run, analysed in [(1, "A"), (2, "B")]:
                fresh = root / f"runner{run}.sqlite"
                for mid in ["A", "B"]:
                    save_match(
                        {"match_id": mid, "demo_url": ["https://example.invalid/demo.dem.gz"]},
                        [sample_row(match_id=mid, team_id="A", player_id="p1", nickname="Senshe", deaths=1, rounds=2, date="2026-11-13T20:00:00Z", roster_count=3)],
                        fresh,
                    )
                out = save_demo_analysis(analysed, 0, events, rounds=rounds, path=fresh)
                self.assertEqual(out["status"], "complete")
                export_snapshot(db_path=fresh, published_dir=public)

            pub_matches = sorted(pd.read_csv(public / "demo_events.csv")["match_id"].unique().tolist())
            self.assertEqual(pub_matches, ["A", "B"])
            self.assertEqual(pending_demo_queue(path=fresh), [])

            # 3) Filtro team_id su demo_side_summary
            wrong_side = pd.DataFrame([{"match_id": "x", "map_no": 0, "map": "ancient", "team_id": "A", "round_no": 1, "side": "T", "won": 1}])
            selected_other_team = pd.DataFrame([{"match_id": "x", "map_no": 0, "team_id": "B"}])
            self.assertTrue(demo_side_summary(wrong_side, selected_other_team).empty)

            # 4) Parser binario demoparser2 con fixture specifiche per evento:
            #    - round 1 (T): prima morte subita ma round vinto (plant A + esplosione)
            #    - round 2 (T): prima kill ottenuta ma round perso
            #    - round 13 (CT, cambio metà): plant B avversario + ingaggio post-plant + defuse -> retake_attempted=1, retake_won=1
            #    - round 14 (CT): plant A avversario + save senza morti post-plant -> retake_attempted=0, retake_won=None
            #    - round 25 (Overtime): verifica lato e numerazione OT
            class RealisticParser:
                def __init__(self, path):
                    pass

                def parse_event(self, event, **kwargs):
                    if event == "player_death":
                        return pd.DataFrame([
                            # Round 1: Senshe (T=2) muore per primo, poi compagno uccide Enemy (CT=3)
                            {"total_rounds_played": 0, "tick": 640, "user_name": "Senshe", "attacker_name": "Enemy", "user_steamid": "1", "attacker_steamid": "2", "user_team_num": 2, "attacker_team_num": 3},
                            {"total_rounds_played": 0, "tick": 800, "user_name": "Enemy", "attacker_name": "Grvpe", "user_steamid": "2", "attacker_steamid": "3", "user_team_num": 3, "attacker_team_num": 2},
                            # Round 2: Senshe (T=2) fa la prima kill, ma Enemy2 vince il round
                            {"total_rounds_played": 1, "tick": 2000, "user_name": "Enemy", "attacker_name": "Senshe", "user_steamid": "2", "attacker_steamid": "1", "user_team_num": 3, "attacker_team_num": 2},
                            # Round 13 (seconda metà, Senshe ora è CT=3): dopo il plant a tick 10000 avviene il duello di retake a tick 10500
                            {"total_rounds_played": 12, "tick": 10500, "user_name": "Enemy", "attacker_name": "Senshe", "user_steamid": "2", "attacker_steamid": "1", "user_team_num": 2, "attacker_team_num": 3},
                            # Round 14 (Senshe CT=3): morte prima del plant (tick 12000 < plant tick 12500), poi save
                            {"total_rounds_played": 13, "tick": 12000, "user_name": "Senshe", "attacker_name": "Enemy", "user_steamid": "1", "attacker_steamid": "2", "user_team_num": 3, "attacker_team_num": 2},
                            # Round 25 (Overtime): Senshe torna T=2
                            {"total_rounds_played": 24, "tick": 25000, "user_name": "Enemy", "attacker_name": "Senshe", "user_steamid": "2", "attacker_steamid": "1", "user_team_num": 3, "attacker_team_num": 2},
                        ])
                    if event == "round_end":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "winner": "T", "reason": "target_bombed"},
                            {"total_rounds_played": 1, "winner": "CT", "reason": "ct_win"},
                            {"total_rounds_played": 12, "winner": "CT", "reason": "bomb_defused"},
                            {"total_rounds_played": 13, "winner": "T", "reason": "target_bombed"},
                            {"total_rounds_played": 24, "winner": "T", "reason": "t_win"},
                        ])
                    if event == "bomb_planted":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 700, "site": "A"},
                            {"total_rounds_played": 12, "tick": 10000, "site": "B"},
                            {"total_rounds_played": 13, "tick": 12500, "site": "A"},
                        ])
                    if event == "bomb_defused":
                        return pd.DataFrame([{"total_rounds_played": 12, "tick": 10800, "site": "B"}])
                    if event == "bomb_exploded":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 950, "site": "A"},
                            {"total_rounds_played": 13, "tick": 13000, "site": "A"},
                        ])
                    if event == "round_freeze_end":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 100},
                            {"total_rounds_played": 1, "tick": 1500},
                            {"total_rounds_played": 12, "tick": 9500},
                            {"total_rounds_played": 13, "tick": 11500},
                            {"total_rounds_played": 24, "tick": 24500},
                        ])
                    return pd.DataFrame()

                def parse_ticks(self, props, ticks=None):
                    return pd.DataFrame([
                        {"tick": 100, "team_num": 2, "current_equip_value": 4200},
                        {"tick": 1500, "team_num": 2, "current_equip_value": 21500},
                        {"tick": 9500, "team_num": 3, "current_equip_value": 4400},
                        {"tick": 11500, "team_num": 3, "current_equip_value": 3200},
                        {"tick": 24500, "team_num": 2, "current_equip_value": 26000},
                    ])

            with patch.dict("sys.modules", demoparser2=SimpleNamespace(DemoParser=RealisticParser)):
                parsed = parse_raw_dem_with_demoparser2(root / "example.dem", "B", 0, db_path=fresh)
            self.assertEqual(len(parsed["events"]), 6)
            self.assertEqual(len(parsed["rounds"]), 5)
            by_r = {r["round_no"]: r for r in parsed["rounds"]}
            # Round 1: prima morte subita ma round vinto + Pistol
            self.assertEqual((by_r[1]["side"], by_r[1]["first_death"], by_r[1]["first_kill"], by_r[1]["won"], by_r[1]["plant_site"], by_r[1]["buy_type"], by_r[1]["equip_value"]), ("T", 1, 0, 1, "A", "Pistol", 4200.0))
            # Round 2: prima kill ottenuta ma round perso + Full Buy
            self.assertEqual((by_r[2]["side"], by_r[2]["first_kill"], by_r[2]["first_death"], by_r[2]["won"], by_r[2]["buy_type"], by_r[2]["equip_value"]), ("T", 1, 0, 0, "Full Buy", 21500.0))
            # Round 13: cambio metà (CT), retake tentato e vinto su B
            self.assertEqual((by_r[13]["side"], by_r[13]["won"], by_r[13]["plant_site"], by_r[13]["retake_attempted"], by_r[13]["retake_won"], by_r[13]["buy_type"]), ("CT", 1, "B", 1, 1, "Pistol"))
            # Round 14: CT save dopo plant su A -> retake_attempted = 0, retake_won = None, Eco
            self.assertEqual((by_r[14]["side"], by_r[14]["won"], by_r[14]["plant_site"], by_r[14]["retake_attempted"], by_r[14]["retake_won"], by_r[14]["buy_type"]), ("CT", 0, "A", 0, None, "Eco"))
            # Round 25: Overtime (T), Full Buy
            self.assertEqual((by_r[25]["side"], by_r[25]["won"], by_r[25]["buy_type"], by_r[25]["equip_value"]), ("T", 1, "Full Buy", 26000.0))

    def test_v4_review_guards_score_reconciliation_two_teams_and_snapshot_restore(self):
        import pandas as pd
        from types import SimpleNamespace
        from unittest.mock import patch
        from projectcs.db import read_demo_events, read_demo_rounds, restore_published_demo_state
        from projectcs.demos import parse_raw_dem_with_demoparser2

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            # 1) Errore su round_end non deve mai fabbricare vincitori dalla prima kill né salvare 'complete'
            db_err = root / "err.sqlite"
            save_match(
                {"match_id": "m", "demo_url": ["https://example.invalid/demo.dem.gz"]},
                [sample_row(match_id="m", team_id="A", player_id="p1", nickname="Senshe", deaths=1, rounds=2, kills=1, score_for=2, score_against=0, date="2026-11-13T20:00:00Z")],
                db_err,
            )

            class BrokenRoundEndParser:
                def __init__(self, path):
                    pass

                def parse_event(self, event, **kwargs):
                    if event == "round_end":
                        raise RuntimeError("simulated round_end failure")
                    if event == "player_death":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 640, "user_steamid": "1", "user_name": "Senshe", "user_team_num": 2, "attacker_steamid": "9", "attacker_name": "Enemy", "attacker_team_num": 3},
                            {"total_rounds_played": 1, "tick": 2000, "user_steamid": "9", "user_name": "Enemy", "user_team_num": 3, "attacker_steamid": "1", "attacker_name": "Senshe", "attacker_team_num": 2},
                        ])
                    return pd.DataFrame()

            with patch.dict("sys.modules", demoparser2=SimpleNamespace(DemoParser=BrokenRoundEndParser)):
                parsed = parse_raw_dem_with_demoparser2(root / "fake.dem", "m", 0, db_path=db_err)
            self.assertEqual(parsed["rounds"], [])
            self.assertIn("round_end", parsed.get("parse_errors", {}))
            res_err = save_demo_analysis("m", 0, parsed["events"], parsed["rounds"], path=db_err)
            self.assertEqual(res_err["status"], "incomplete")

            # Anche se round_end restituisse esiti che non riconciliano con score_for=2 (es. [0, 1]), save_demo_analysis rifiuta
            res_mismatch = save_demo_analysis(
                "m",
                0,
                parsed["events"],
                [{"round_no": 1, "side": "T", "won": 0}, {"round_no": 2, "side": "T", "won": 1}],
                path=db_err,
            )
            self.assertEqual(res_mismatch["status"], "incomplete")

            # 2) Match con due squadre tracciate (A e B) richiede round completi per entrambe
            db_two = root / "two_teams.sqlite"
            save_match(
                {"match_id": "m2", "demo_url": ["https://example.invalid/demo.dem.gz"]},
                [
                    sample_row(match_id="m2", team_id="A", player_id="p1", nickname="Senshe", deaths=1, rounds=2, kills=1, score_for=2, score_against=0, date="2026-11-13T20:00:00Z"),
                    sample_row(match_id="m2", team_id="B", player_id="p3", nickname="Grvpe", deaths=1, rounds=2, kills=1, score_for=0, score_against=2, date="2026-11-13T20:00:00Z"),
                ],
                db_two,
            )
            ev_two = [
                {"round_no": 1, "time_sec": 10.0, "victim_id": "p1", "victim_team": "A", "attacker_id": "p3", "attacker_team": "B"},
                {"round_no": 1, "time_sec": 13.0, "victim_id": "p3", "victim_team": "B", "attacker_id": "p2", "attacker_team": "A"},
            ]
            only_a_rounds = [{"round_no": 1, "team_id": "A", "side": "T", "won": 1}, {"round_no": 2, "team_id": "A", "side": "T", "won": 1}]
            res_partial_teams = save_demo_analysis("m2", 0, ev_two, only_a_rounds, path=db_two)
            self.assertEqual(res_partial_teams["status"], "incomplete")
            self.assertEqual(len(pending_demo_queue(path=db_two)), 1)

            # 3) Uno snapshot privo di demo_events.csv non deve mai ripristinare demo_status='complete' né svuotare la coda
            db_good = root / "good.sqlite"
            save_match(
                {"match_id": "m", "demo_url": ["https://example.invalid/demo.dem.gz"]},
                [sample_row(match_id="m", team_id="A", player_id="p1", nickname="Senshe", deaths=1, rounds=2, kills=1, score_for=2, score_against=0, date="2026-11-13T20:00:00Z")],
                db_good,
            )
            ok_save = save_demo_analysis(
                "m",
                0,
                parsed["events"],
                [{"round_no": 1, "side": "T", "won": 1}, {"round_no": 2, "side": "T", "won": 1}],
                path=db_good,
            )
            self.assertEqual(ok_save["status"], "complete")
            pub = root / "published_partial"
            export_snapshot(db_path=db_good, published_dir=pub)
            (pub / "demo_events.csv").unlink()

            db_cold = root / "cold.sqlite"
            save_match(
                {"match_id": "m", "demo_url": ["https://example.invalid/demo.dem.gz"]},
                [sample_row(match_id="m", team_id="A", player_id="p1", nickname="Senshe", deaths=1, rounds=2, kills=1, score_for=2, score_against=0, date="2026-11-13T20:00:00Z")],
                db_cold,
            )
            restored = restore_published_demo_state(db_path=db_cold, published_dir=pub)
            self.assertEqual(restored["restored_maps"], 0)
            self.assertEqual(restored["restored_status"], 0)
            self.assertEqual(len(read_demo_events(db_cold)), 0)
            self.assertEqual(len(read_demo_rounds(db_cold)), 0)
            self.assertEqual(read_demo_status(db_cold), [])
            self.assertEqual(len(pending_demo_queue(path=db_cold, published_dir=pub)), 1)

            # 4) Valori equipaggiamento mancanti (NaN/None) o parziali (<5 giocatori) non devono diventare NaN / 'Full Buy'
            class PartialEconParser:
                def __init__(self, path):
                    pass

                def parse_event(self, event, **kwargs):
                    if event == "player_death":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 100, "user_name": "Senshe", "user_steamid": "1", "user_team_num": 2, "attacker_name": "Enemy", "attacker_steamid": "2", "attacker_team_num": 3},
                            {"total_rounds_played": 1, "tick": 300, "user_name": "Enemy", "user_steamid": "2", "user_team_num": 3, "attacker_name": "Senshe", "attacker_steamid": "1", "attacker_team_num": 2},
                        ])
                    if event == "round_end":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "winner": "T"},
                            {"total_rounds_played": 1, "winner": "T"},
                        ])
                    if event == "round_freeze_end":
                        return pd.DataFrame([
                            {"total_rounds_played": 0, "tick": 50},
                            {"total_rounds_played": 1, "tick": 200},
                        ])
                    return pd.DataFrame()

                def parse_ticks(self, props, ticks=None):
                    return pd.DataFrame(
                        [{"tick": 200, "team_num": 2, "current_equip_value": val} for val in [3000, 3000, 3000, 3000, None]]
                    )

            with patch.dict("sys.modules", demoparser2=SimpleNamespace(DemoParser=PartialEconParser)):
                parsed_econ = parse_raw_dem_with_demoparser2(root / "econ.dem", "m", 0, db_path=db_good)
            self.assertIsNone(parsed_econ["rounds"][1]["equip_value"])
            self.assertIsNone(parsed_econ["rounds"][1]["buy_type"])

            # 5) Un batch misto (alcune demo completate e un errore) esporta comunque lo snapshot validato e restituisce exit_code=1
            import sys
            import projectcs.demos as demos_mod

            mixed_batch = {
                "queued": 2,
                "attempted": 2,
                "completed": 1,
                "expired": 0,
                "rate_limited": False,
                "failed": [{"match_id": "bad", "status": "error"}],
            }
            with (
                patch.object(sys, "argv", ["demos", "--download-missing", "--export-snapshot"]),
                patch.object(demos_mod, "download_and_process_queue", return_value=mixed_batch),
                patch.object(demos_mod, "_load_api_key", return_value=""),
                patch("projectcs.sync.export_snapshot", return_value={"matches": 1, "player_maps": 5, "demo_events": 2, "demo_rounds": 2}) as mock_export,
            ):
                rc = demos_mod.main()
            self.assertEqual(rc, 1)
            self.assertTrue(mock_export.called)


if __name__ == "__main__":
    unittest.main()
