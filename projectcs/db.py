import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from projectcs.config import DB_PATH, NAMES

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_meta VALUES (2);
CREATE TABLE IF NOT EXISTS players (
 player_id TEXT PRIMARY KEY, nickname TEXT NOT NULL, steam_id TEXT
);
CREATE TABLE IF NOT EXISTS matches (
 match_id TEXT PRIMARY KEY, started_at TEXT, source TEXT NOT NULL,
 raw_json TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_maps (
 match_id TEXT NOT NULL, map_no INTEGER NOT NULL, team_id TEXT NOT NULL,
 player_id TEXT NOT NULL, nickname TEXT NOT NULL, date TEXT NOT NULL,
 map TEXT NOT NULL, roster_count INTEGER NOT NULL, won INTEGER,
 rounds INTEGER, kills REAL, deaths REAL, assists REAL, adr REAL,
 headshots REAL, damage REAL, first_kills REAL, entry_count REAL,
 entry_wins REAL, sniper_kills REAL, flash_count REAL,
 flash_successes REAL, enemies_flashed REAL, utility_count REAL,
 utility_damage REAL, utility_enemies REAL, clutch_kills REAL,
 clutch_1v1_count REAL, clutch_1v1_wins REAL, clutch_1v2_count REAL,
 clutch_1v2_wins REAL, pistol_kills REAL, mvps REAL,
 double_kills REAL, triple_kills REAL, quadro_kills REAL,
 penta_kills REAL, traded_deaths REAL, untraded_deaths REAL,
 first_half_score INTEGER, second_half_score INTEGER,
 overtime_score INTEGER, score_for INTEGER, score_against INTEGER,
 source TEXT NOT NULL, coverage TEXT NOT NULL,
 PRIMARY KEY(match_id,map_no,team_id,player_id)
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, match_id TEXT,
 round_no INTEGER, kind TEXT NOT NULL, category TEXT NOT NULL,
 observation TEXT NOT NULL, status TEXT NOT NULL, action TEXT
);
CREATE TABLE IF NOT EXISTS objectives (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL,
 action TEXT NOT NULL, metric TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_assignments (
 id INTEGER PRIMARY KEY, player_id TEXT, map TEXT, side TEXT,
 role TEXT NOT NULL, valid_from TEXT, valid_to TEXT
);
CREATE TABLE IF NOT EXISTS rounds (
 match_id TEXT, map_no INTEGER, round_no INTEGER, payload_json TEXT,
 PRIMARY KEY(match_id,map_no,round_no)
);
CREATE TABLE IF NOT EXISTS events (
 match_id TEXT, map_no INTEGER, round_no INTEGER, event_index INTEGER,
 tick INTEGER, event_type TEXT, player_id TEXT, payload_json TEXT,
 PRIMARY KEY(match_id,map_no,event_index)
);
CREATE TABLE IF NOT EXISTS analysis_runs (
 id INTEGER PRIMARY KEY, match_id TEXT, module TEXT, version TEXT,
 status TEXT, created_at TEXT, details_json TEXT
);
"""

MIGRATION_COLUMNS = {
    "damage": "REAL",
    "first_kills": "REAL",
    "entry_count": "REAL",
    "entry_wins": "REAL",
    "sniper_kills": "REAL",
    "flash_count": "REAL",
    "flash_successes": "REAL",
    "enemies_flashed": "REAL",
    "utility_count": "REAL",
    "utility_damage": "REAL",
    "utility_enemies": "REAL",
    "clutch_kills": "REAL",
    "clutch_1v1_count": "REAL",
    "clutch_1v1_wins": "REAL",
    "clutch_1v2_count": "REAL",
    "clutch_1v2_wins": "REAL",
    "pistol_kills": "REAL",
    "mvps": "REAL",
    "double_kills": "REAL",
    "triple_kills": "REAL",
    "quadro_kills": "REAL",
    "penta_kills": "REAL",
    "traded_deaths": "REAL",
    "untraded_deaths": "REAL",
    "first_half_score": "INTEGER",
    "second_half_score": "INTEGER",
    "overtime_score": "INTEGER",
}

_INITIALIZED_DBS = set()
_READ_ROWS_CACHE = {}


def _invalidate_cache(path):
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        _READ_ROWS_CACHE.clear()
        return
    stale = [k for k in _READ_ROWS_CACHE if k[0] == resolved]
    for k in stale:
        _READ_ROWS_CACHE.pop(k, None)


def _ensure_schema(conn, path_key):
    if path_key in _INITIALIZED_DBS:
        return
    conn.executescript(SCHEMA)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(player_maps)")}
    for col_name, col_type in MIGRATION_COLUMNS.items():
        if col_name not in existing:
            conn.execute(f"ALTER TABLE player_maps ADD COLUMN {col_name} {col_type}")
    conn.execute("INSERT OR IGNORE INTO schema_meta VALUES (2)")
    conn.commit()
    _INITIALIZED_DBS.add(path_key)


@contextmanager
def connect(path=DB_PATH, readonly=False):
    path = Path(path)
    path_key = str(path.resolve())
    if readonly:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
        return

    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not existed:
        _INITIALIZED_DBS.discard(path_key)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn, path_key)
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def read_rows(path=DB_PATH):
    path = Path(path)
    if not path.exists():
        return []
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if cache_key in _READ_ROWS_CACHE:
        return [dict(r) for r in _READ_ROWS_CACHE[cache_key]]
    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "player_maps" not in tables:
            return []
        pm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(player_maps)")}
        rows = [dict(r) for r in conn.execute("SELECT * FROM player_maps ORDER BY date")]
        if "entry_count" not in pm_cols and "matches" in tables:
            from projectcs.faceit import FaceitError, normalize_match

            tracked = (
                {
                    r["player_id"]: r["nickname"]
                    for r in conn.execute("SELECT player_id, nickname FROM players")
                }
                if "players" in tables
                else {}
            )
            enriched = []
            for item in conn.execute("SELECT raw_json FROM matches ORDER BY started_at"):
                try:
                    payload = json.loads(item["raw_json"])
                    stats = payload.get("_projectcs_stats") if isinstance(payload, dict) else None
                    if not isinstance(stats, dict) or not stats.get("rounds"):
                        continue
                    enriched.extend(normalize_match(payload, stats, tracked))
                except (ValueError, KeyError, TypeError, FaceitError):
                    continue
            if enriched:
                by_key = {}
                for orig in rows:
                    base_row = {col: None for col in MIGRATION_COLUMNS}
                    base_row.update(orig)
                    key = (
                        base_row.get("match_id"),
                        base_row.get("map_no"),
                        base_row.get("team_id"),
                        base_row.get("player_id"),
                    )
                    by_key[key] = base_row
                for item_row in enriched:
                    key = (
                        item_row.get("match_id"),
                        item_row.get("map_no"),
                        item_row.get("team_id"),
                        item_row.get("player_id"),
                    )
                    if key in by_key:
                        prev = by_key[key]
                        if (
                            (prev.get("traded_deaths") is not None or prev.get("untraded_deaths") is not None)
                            and item_row.get("traded_deaths") is None
                            and item_row.get("untraded_deaths") is None
                        ):
                            item_row["traded_deaths"] = prev.get("traded_deaths")
                            item_row["untraded_deaths"] = prev.get("untraded_deaths")
                            item_row["coverage"] = prev.get("coverage")
                        prev.update(item_row)
                    else:
                        by_key[key] = item_row
                rows = sorted(
                    by_key.values(),
                    key=lambda r: (
                        str(r.get("date") or ""),
                        str(r.get("match_id") or ""),
                        int(r.get("map_no") or 0),
                        str(r.get("nickname") or ""),
                    ),
                )
    _READ_ROWS_CACHE[cache_key] = rows
    return [dict(r) for r in rows]


def complete_match_ids_conn(conn):
    """
    Restituisce i match_id la cui importazione è completa:
    - presenti in matches con raw_json valido e _projectcs_stats.rounds popolato
    - almeno una riga in player_maps
    - nessuna riga in player_maps con metriche base (rounds, won, kills, deaths, adr) mancanti
      quando previste o lasciate nulle da una risposta parziale.
    """
    pm_cols = {row["name"] for row in conn.execute("PRAGMA table_info(player_maps)")}
    if not pm_cols:
        return set()
    rows = conn.execute(
        """
        SELECT m.match_id, m.raw_json,
               COUNT(pm.player_id) AS row_count,
               SUM(CASE WHEN pm.rounds IS NULL OR pm.won IS NULL
                          OR pm.kills IS NULL OR pm.deaths IS NULL
                          OR pm.adr IS NULL
                   THEN 1 ELSE 0 END) AS missing_core
        FROM matches m
        INNER JOIN player_maps pm ON pm.match_id = m.match_id
        GROUP BY m.match_id
        """
    ).fetchall()
    complete = set()
    for r in rows:
        if r["row_count"] <= 0 or r["missing_core"] > 0:
            continue
        try:
            payload = json.loads(r["raw_json"])
        except (TypeError, ValueError):
            continue
        stats = payload.get("_projectcs_stats") if isinstance(payload, dict) else None
        if not isinstance(stats, dict) or not stats.get("rounds"):
            continue
        complete.add(r["match_id"])
    return complete


def existing_match_ids(path=DB_PATH):
    path = Path(path)
    if not path.exists():
        return set()
    with connect(path, readonly=True) as conn:
        return complete_match_ids_conn(conn)


def _save_match_conn(conn, match, rows, now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    match_id = match["match_id"]
    preserved = {}
    for prev in conn.execute(
        "SELECT map_no, team_id, player_id, traded_deaths, untraded_deaths, coverage "
        "FROM player_maps WHERE match_id=?",
        (match_id,),
    ):
        if prev["traded_deaths"] is not None or prev["untraded_deaths"] is not None:
            preserved[(prev["map_no"], prev["team_id"], prev["player_id"])] = (
                prev["traded_deaths"],
                prev["untraded_deaths"],
                prev["coverage"],
            )
    conn.execute(
        "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?)",
        (match_id, rows[0]["date"] if rows else None, "faceit", json.dumps(match), now),
    )
    conn.execute("DELETE FROM player_maps WHERE match_id=?", (match_id,))
    for row in rows:
        item = dict(row)
        key = (item["map_no"], item["team_id"], item["player_id"])
        if key in preserved and item.get("traded_deaths") is None and item.get("untraded_deaths") is None:
            item["traded_deaths"], item["untraded_deaths"], item["coverage"] = preserved[key]
        keys = list(item)
        sql = (
            "INSERT INTO player_maps ("
            + ",".join(keys)
            + ") VALUES ("
            + ",".join("?" for _ in keys)
            + ")"
        )
        conn.execute(sql, [item[k] for k in keys])


def save_match(match, rows, path=DB_PATH):
    with connect(path) as conn:
        _save_match_conn(conn, match, rows)
    _invalidate_cache(path)


def reprocess_stored_matches(path=DB_PATH):
    """Ricalcola player_maps dai payload raw_json già salvati in matches senza chiamate di rete."""
    from projectcs.faceit import FaceitError, normalize_match

    path = Path(path)
    if not path.exists():
        return {"matches": 0, "player_maps": 0, "failed": []}

    total_rows = 0
    processed = 0
    failed = []
    now = datetime.now(timezone.utc).isoformat()

    with connect(path) as conn:
        tracked = {
            r["player_id"]: r["nickname"]
            for r in conn.execute("SELECT player_id, nickname FROM players")
        }
        stored = conn.execute("SELECT match_id, raw_json FROM matches ORDER BY started_at").fetchall()

        for item in stored:
            match_id = item["match_id"]
            conn.execute("SAVEPOINT sp_reprocess")
            try:
                payload = json.loads(item["raw_json"])
                if not isinstance(payload, dict):
                    raise ValueError("Payload JSON non valido")
                stats = payload.get("_projectcs_stats")
                if not isinstance(stats, dict) or not stats.get("rounds"):
                    raise ValueError("Statistiche _projectcs_stats assenti o vuote")
                local_tracked = dict(tracked)
                if not local_tracked:
                    for game in stats.get("rounds", []):
                        for team in game.get("teams", []):
                            for player in team.get("players", []):
                                if player.get("nickname") in NAMES and player.get("player_id"):
                                    local_tracked[player["player_id"]] = player["nickname"]
                rows = normalize_match(payload, stats, local_tracked)
                if not rows:
                    raise ValueError("Nessuna riga giocatore generata")
                _save_match_conn(conn, payload, rows, now=now)
                conn.execute("RELEASE SAVEPOINT sp_reprocess")
                processed += 1
                total_rows += len(rows)
            except (ValueError, KeyError, TypeError, FaceitError) as exc:
                conn.execute("ROLLBACK TO SAVEPOINT sp_reprocess")
                conn.execute("RELEASE SAVEPOINT sp_reprocess")
                failed.append({"match_id": match_id, "error": str(exc)})

        conn.execute(
            "INSERT INTO analysis_runs(match_id,module,version,status,created_at,details_json) VALUES (?,?,?,?,?,?)",
            (
                None,
                "faceit_local_reprocess",
                "2",
                "partial" if failed else "complete",
                now,
                json.dumps({"matches": processed, "player_maps": total_rows, "failed": failed}),
            ),
        )
    _invalidate_cache(path)
    return {"matches": processed, "player_maps": total_rows, "failed": failed}


def _extract_event_time_seconds(ev, tickrate):
    if not isinstance(ev, dict):
        return None
    if ev.get("time_sec") is not None:
        try:
            val = float(ev["time_sec"])
            return val if val >= 0 else None
        except (TypeError, ValueError):
            return None
    if ev.get("timestamp_ms") is not None:
        try:
            val = float(ev["timestamp_ms"]) / 1000.0
            return val if val >= 0 else None
        except (TypeError, ValueError):
            return None
    if ev.get("tick") is not None:
        try:
            val = float(ev["tick"]) / float(tickrate)
            return val if val >= 0 else None
        except (TypeError, ValueError):
            return None
    return None


def _validate_death_event(ev, tickrate):
    if not isinstance(ev, dict):
        return False
    victim_id = ev.get("victim_id") or ev.get("player_id")
    attacker_id = ev.get("attacker_id")
    victim_team = ev.get("victim_team") or ev.get("team_id")
    attacker_team = ev.get("attacker_team")
    round_raw = ev.get("round_no")
    if not victim_id or not attacker_id or not victim_team or not attacker_team or round_raw is None:
        return False
    try:
        rno = int(round_raw)
    except (TypeError, ValueError):
        return False
    if rno < 1:
        return False
    if _extract_event_time_seconds(ev, tickrate) is None:
        return False
    return True


def compute_round_trades(events, window_seconds=5.0, tickrate=64.0):
    """
    Calcola per ogni giocatore (player_id) i conteggi di morti scambiate (traded_deaths)
    e morti non scambiate (untraded_deaths) a partire dagli eventi 'player_death' di una mappa.

    Richiede che ogni evento di morte abbia struttura valida: round_no (>= 1), timestamp
    esplicito (time_sec, timestamp_ms o tick), victim_id, victim_team, attacker_id, attacker_team.
    Se un giocatore (o il round in cui muore) presenta eventi incompleti, i suoi conteggi
    vengono restituiti come None per evitare falsi trade o falsi zeri.
    """
    if tickrate <= 0:
        raise ValueError("tickrate deve essere positivo")
    by_round = {}
    invalid_players = set()
    invalid_rounds = set()
    all_victims = set()

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("event_type", "player_death") != "player_death":
            continue
        victim_id = ev.get("victim_id") or ev.get("player_id")
        if victim_id:
            all_victims.add(victim_id)
        if not _validate_death_event(ev, tickrate):
            if victim_id:
                invalid_players.add(victim_id)
            try:
                if ev.get("round_no") is not None:
                    invalid_rounds.add(int(ev["round_no"]))
            except (TypeError, ValueError):
                pass
            continue
        rno = int(ev["round_no"])
        by_round.setdefault(rno, []).append(ev)

    totals = {
        pid: {"traded_deaths": 0, "untraded_deaths": 0}
        for pid in all_victims
    }

    for rno, round_events in by_round.items():
        if rno in invalid_rounds:
            for ev in round_events:
                vid = ev.get("victim_id") or ev.get("player_id")
                if vid:
                    invalid_players.add(vid)
            continue
        ordered = sorted(round_events, key=lambda e: _extract_event_time_seconds(e, tickrate))
        for idx, death in enumerate(ordered):
            victim_id = death.get("victim_id") or death.get("player_id")
            if victim_id in invalid_players:
                continue
            stats = totals.setdefault(victim_id, {"traded_deaths": 0, "untraded_deaths": 0})
            attacker_id = death["attacker_id"]
            victim_team = death.get("victim_team") or death.get("team_id")
            attacker_team = death["attacker_team"]
            t1 = _extract_event_time_seconds(death, tickrate)

            is_enemy_kill = bool(attacker_id != victim_id and attacker_team != victim_team)
            traded = False
            if is_enemy_kill:
                for later in ordered[idx:]:
                    t2 = _extract_event_time_seconds(later, tickrate)
                    if t2 - t1 < 0:
                        continue
                    if t2 - t1 > window_seconds:
                        break
                    later_victim = later.get("victim_id") or later.get("player_id")
                    later_attacker = later["attacker_id"]
                    later_attacker_team = later["attacker_team"]
                    if (
                        later_victim == attacker_id
                        and later_attacker != attacker_id
                        and later_attacker != victim_id
                        and later_attacker_team == victim_team
                    ):
                        traded = True
                        break
            if traded:
                stats["traded_deaths"] += 1
            else:
                stats["untraded_deaths"] += 1

    for pid in invalid_players:
        totals[pid] = {"traded_deaths": None, "untraded_deaths": None}

    return totals


def apply_demo_trades(match_id, map_no, events, path=DB_PATH, window_seconds=5.0, tickrate=64.0):
    """
    Valida e salva gli eventi di morte della demo in 'events' e aggiorna traded_deaths e
    untraded_deaths in 'player_maps' solo quando il killfeed della mappa è valido,
    include gli eventi di entrambi i team e riconcilia le morti di tutti i giocatori
    tracciati nella mappa. Un reimport invalido o incompleto preserva l'ultima analisi valida.
    """
    death_events = [
        ev for ev in (events or [])
        if isinstance(ev, dict) and ev.get("event_type", "player_death") == "player_death"
    ]
    if not death_events:
        return {"status": "empty", "updated": 0}

    if any(not _validate_death_event(ev, tickrate) for ev in death_events):
        return {"status": "invalid", "updated": 0}

    trade_counts = compute_round_trades(death_events, window_seconds=window_seconds, tickrate=tickrate)

    with connect(path) as conn:
        players_in_map = conn.execute(
            "SELECT player_id, team_id, kills, deaths, rounds, score_for, won "
            "FROM player_maps WHERE match_id=? AND map_no=?",
            (match_id, map_no),
        ).fetchall()
        if not players_in_map:
            return {"status": "no_map", "updated": 0}

        max_rounds = max((int(r["rounds"]) for r in players_in_map if r["rounds"] is not None), default=None)
        if max_rounds is not None and max_rounds > 0:
            if any(int(ev["round_no"]) > max_rounds for ev in death_events):
                return {"status": "invalid", "updated": 0}

        # Verifica copertura killfeed: per poter valutare i trade occorrono anche le morti avversarie
        victim_teams = {str(ev["victim_team"]).strip() for ev in death_events}
        all_teams = victim_teams | {str(ev["attacker_team"]).strip() for ev in death_events}
        tracked_pids = {r["player_id"] for r in players_in_map}
        victim_ids = {ev.get("victim_id") or ev.get("player_id") for ev in death_events}
        has_team_kills_or_rounds = any(
            (r["kills"] is not None and float(r["kills"]) > 0)
            or (r["score_for"] is not None and int(r["score_for"]) > 0)
            or (r["won"] is not None and int(r["won"]) > 0)
            for r in players_in_map
        )
        has_tracked_deaths = any(r["deaths"] is not None and int(r["deaths"]) > 0 for r in players_in_map)
        if has_tracked_deaths and (has_team_kills_or_rounds or len(all_teams) >= 2):
            if len(victim_teams) < 2 or victim_ids.issubset(tracked_pids):
                return {"status": "incomplete", "updated": 0}

        # Se raw_json contiene i conteggi morti completi di tutti i giocatori della mappa, riconciliali
        match_row = conn.execute("SELECT raw_json FROM matches WHERE match_id=?", (match_id,)).fetchone()
        if match_row and match_row["raw_json"]:
            try:
                raw_payload = json.loads(match_row["raw_json"])
                rounds_list = (
                    raw_payload.get("_projectcs_stats", {}).get("rounds", [])
                    if isinstance(raw_payload, dict)
                    else []
                )
                if isinstance(rounds_list, list) and 0 <= int(map_no) < len(rounds_list):
                    game = rounds_list[int(map_no)]
                    for team in game.get("teams", []):
                        for p in team.get("players", []):
                            pid = p.get("player_id")
                            raw_deaths = p.get("player_stats", {}).get("Deaths")
                            if pid and raw_deaths is not None:
                                exp_d = int(float(raw_deaths))
                                p_counts = trade_counts.get(pid, {"traded_deaths": 0, "untraded_deaths": 0})
                                if (
                                    p_counts["traded_deaths"] is None
                                    or p_counts["untraded_deaths"] is None
                                    or (p_counts["traded_deaths"] + p_counts["untraded_deaths"]) != exp_d
                                ):
                                    return {"status": "incomplete", "updated": 0}
            except (ValueError, TypeError, KeyError):
                pass

        # Verifica riconciliazione per tutti i membri tracciati nella mappa prima di scrivere
        validated_updates = []
        for row in players_in_map:
            pid = row["player_id"]
            expected_deaths = row["deaths"]
            if expected_deaths is None:
                return {"status": "incomplete", "updated": 0}
            counts = trade_counts.get(pid)
            if counts is None and int(expected_deaths) == 0:
                counts = {"traded_deaths": 0, "untraded_deaths": 0}
            if (
                counts is None
                or counts["traded_deaths"] is None
                or counts["untraded_deaths"] is None
                or (counts["traded_deaths"] + counts["untraded_deaths"]) != int(expected_deaths)
            ):
                return {"status": "incomplete", "updated": 0}
            validated_updates.append((pid, float(counts["traded_deaths"]), float(counts["untraded_deaths"])))

        conn.execute("DELETE FROM events WHERE match_id=? AND map_no=?", (match_id, map_no))
        for idx, ev in enumerate(death_events):
            victim_id = ev.get("victim_id") or ev.get("player_id")
            conn.execute(
                "INSERT INTO events(match_id,map_no,round_no,event_index,tick,event_type,player_id,payload_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    match_id,
                    map_no,
                    int(ev["round_no"]),
                    idx,
                    int(ev.get("tick") or 0),
                    ev.get("event_type", "player_death"),
                    victim_id,
                    json.dumps(ev),
                ),
            )

        for pid, traded_val, untraded_val in validated_updates:
            conn.execute(
                "UPDATE player_maps SET traded_deaths=?, untraded_deaths=?, coverage=? "
                "WHERE match_id=? AND map_no=? AND player_id=?",
                (
                    traded_val,
                    untraded_val,
                    "Statistiche match + trade demo",
                    match_id,
                    map_no,
                    pid,
                ),
            )

    _invalidate_cache(path)
    return {"status": "complete", "updated": len(validated_updates)}


def read_records(table, path=DB_PATH):
    if table not in {"reviews", "objectives"}:
        raise ValueError("Tabella non consentita")
    path = Path(path)
    if not path.exists():
        return []
    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if table not in tables:
            return []
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC")]


def add_review(match_id, round_no, kind, category, observation, status, action, path=DB_PATH):
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO reviews(created_at,match_id,round_no,kind,category,observation,status,action) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), match_id, round_no, kind, category, observation, status, action),
        )


def add_objective(title, action, metric, path=DB_PATH):
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO objectives(created_at,title,action,metric,status) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), title, action, metric, "Da allenare"),
        )


def set_objective_status(objective_id, status, path=DB_PATH):
    if status not in {"Da allenare", "In corso", "Da verificare", "Completato"}:
        raise ValueError("Stato non valido")
    with connect(path) as conn:
        conn.execute("UPDATE objectives SET status=? WHERE id=?", (status, objective_id))


def backup(path=DB_PATH):
    path = Path(path)
    if not path.exists():
        return None
    with connect(path, readonly=True) as source:
        target = sqlite3.connect(":memory:")
        source.backup(target)
        result = target.serialize()
        target.close()
        return result
