import csv
import json
import os
import re
import sqlite3
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from projectcs.config import DB_PATH, NAMES, ROOT

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
 match_id TEXT, map_no INTEGER, team_id TEXT NOT NULL DEFAULT '', round_no INTEGER, payload_json TEXT,
 PRIMARY KEY(match_id,map_no,team_id,round_no)
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
CREATE TABLE IF NOT EXISTS demo_status (
 match_id TEXT NOT NULL, map_no INTEGER NOT NULL,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 resource_url TEXT, last_attempt_at TEXT NOT NULL,
 next_retry_at TEXT, error TEXT, details_json TEXT,
 PRIMARY KEY(match_id, map_no)
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
    "leetify_rating": "REAL",
    "ct_leetify_rating": "REAL",
    "t_leetify_rating": "REAL",
    "preaim": "REAL",
    "reaction_time_ms": "REAL",
    "counter_strafe_good": "REAL",
    "counter_strafe_all": "REAL",
    "spray_accuracy": "REAL",
    "trade_kill_opp": "REAL",
    "trade_kills_succeed": "REAL",
    "rounds_survived": "REAL",
    "utility_on_death_avg": "REAL",
}

_INITIALIZED_DBS = set()
_READ_ROWS_CACHE = {}

_PG_TABLE_COLS = {
    "schema_meta": ["version"],
    "players": ["player_id", "nickname", "steam_id"],
    "matches": ["match_id", "started_at", "source", "raw_json", "imported_at"],
    "player_maps": [
        "match_id", "map_no", "team_id", "player_id", "nickname", "date", "map",
        "roster_count", "won", "rounds", "kills", "deaths", "assists", "adr",
        "headshots", "damage", "first_kills", "entry_count", "entry_wins",
        "sniper_kills", "flash_count", "flash_successes", "enemies_flashed",
        "utility_count", "utility_damage", "utility_enemies", "clutch_kills",
        "clutch_1v1_count", "clutch_1v1_wins", "clutch_1v2_count", "clutch_1v2_wins",
        "pistol_kills", "mvps", "double_kills", "triple_kills", "quadro_kills",
        "penta_kills", "traded_deaths", "untraded_deaths", "first_half_score",
        "second_half_score", "overtime_score", "score_for", "score_against",
        "source", "coverage",
        "leetify_rating", "ct_leetify_rating", "t_leetify_rating", "preaim",
        "reaction_time_ms", "counter_strafe_good", "counter_strafe_all",
        "spray_accuracy", "trade_kill_opp", "trade_kills_succeed",
        "rounds_survived", "utility_on_death_avg",
    ],
    "rounds": ["match_id", "map_no", "team_id", "round_no", "payload_json"],
    "events": ["match_id", "map_no", "round_no", "event_index", "tick", "event_type", "player_id", "payload_json"],
    "demo_status": [
        "match_id", "map_no", "status", "attempts", "resource_url",
        "last_attempt_at", "next_retry_at", "error", "details_json",
    ],
}

_PG_PK_COLS = {
    "schema_meta": ["version"],
    "players": ["player_id"],
    "matches": ["match_id"],
    "player_maps": ["match_id", "map_no", "team_id", "player_id"],
    "rounds": ["match_id", "map_no", "team_id", "round_no"],
    "events": ["match_id", "map_no", "event_index"],
    "demo_status": ["match_id", "map_no"],
}

SCHEMA_PG = (
    SCHEMA.replace("INTEGER PRIMARY KEY,", "SERIAL PRIMARY KEY,")
    .replace("INSERT OR IGNORE INTO schema_meta VALUES (2);", "INSERT INTO schema_meta VALUES (2) ON CONFLICT DO NOTHING;")
    .replace(" REAL,", " DOUBLE PRECISION,")
    .replace(" REAL\n", " DOUBLE PRECISION\n")
)


def _normalize_pg_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return ""
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        creds, host_part = rest.rsplit("@", 1)
        if ":" in creds:
            user, pwd = creds.split(":", 1)
            if any(ch in pwd for ch in ("?", "#", " ")):
                pwd = quote(pwd, safe="%")
                url = f"{scheme}://{user}:{pwd}@{host_part}"
    if "supabase." in url and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def get_database_url():
    env_url = os.environ.get("DATABASE_URL")
    if env_url and env_url.strip():
        return _normalize_pg_url(env_url)
    try:
        import streamlit as st
        sec_url = st.secrets.get("DATABASE_URL", "")
        if sec_url and str(sec_url).strip():
            return _normalize_pg_url(str(sec_url))
    except Exception:
        pass
    secrets_path = ROOT / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        try:
            data = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
            sec_url = data.get("DATABASE_URL", "")
            if sec_url and str(sec_url).strip():
                return _normalize_pg_url(str(sec_url))
        except Exception:
            pass
    return ""


def using_postgres(path=DB_PATH):
    if not get_database_url():
        return False
    try:
        return Path(path).resolve() == Path(DB_PATH).resolve()
    except Exception:
        return False


def db_available(path=DB_PATH):
    return using_postgres(path) or Path(path).exists()


def _qmark_to_percent_s(sql):
    out = []
    in_single = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            out.append(ch)
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            in_single = not in_single
        elif ch == "?" and not in_single:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _translate_sql_for_pg(sql):
    stripped = sql.strip().rstrip(";")
    if stripped == "SELECT name FROM sqlite_master WHERE type='table'":
        return "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'", None

    m_pragma = re.match(r"^PRAGMA\s+table_info\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)$", stripped, re.IGNORECASE)
    if m_pragma:
        return (
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (m_pragma.group(1),),
        )

    m_ignore = re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+(.+)$", stripped, re.IGNORECASE | re.DOTALL)
    if m_ignore:
        body = _qmark_to_percent_s(m_ignore.group(1))
        if "on conflict" not in body.lower():
            body = f"{body} ON CONFLICT DO NOTHING"
        return f"INSERT INTO {body}", None

    m_replace = re.match(
        r"^INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]+\))?\s*(VALUES\s*.+)$",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    if m_replace:
        tbl = m_replace.group(1)
        cols_raw = m_replace.group(2)
        vals = _qmark_to_percent_s(m_replace.group(3))
        if cols_raw:
            cols = [c.strip() for c in cols_raw.strip()[1:-1].split(",")]
        else:
            cols = _PG_TABLE_COLS.get(tbl, [])
        pk = _PG_PK_COLS.get(tbl, [])
        non_pk = [c for c in cols if c not in pk]
        cols_clause = f" {cols_raw}" if cols_raw else ""
        if pk and non_pk:
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in non_pk)
            return f"INSERT INTO {tbl}{cols_clause} {vals} ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}", None
        if pk:
            return f"INSERT INTO {tbl}{cols_clause} {vals} ON CONFLICT ({', '.join(pk)}) DO NOTHING", None

    return _qmark_to_percent_s(sql), None


class _PgCursorProxy:
    def __init__(self, rows):
        self._rows = rows or []

    def __iter__(self):
        return iter(self._rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _PgCompatConn:
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=None):
        pg_sql, override_params = _translate_sql_for_pg(sql)
        use_params = override_params if override_params is not None else params
        with self._conn.cursor() as cur:
            cur.execute(pg_sql, use_params)
            rows = cur.fetchall() if cur.description is not None else []
        return _PgCursorProxy(rows)

    def executemany(self, sql, seq_of_params):
        pg_sql, _ = _translate_sql_for_pg(sql)
        with self._conn.cursor() as cur:
            cur.executemany(pg_sql, list(seq_of_params))
        return _PgCursorProxy([])

    def executescript(self, sql):
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _invalidate_cache(path):
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        _READ_ROWS_CACHE.clear()
        return
    stale = [k for k in _READ_ROWS_CACHE if k[0] == resolved]
    for k in stale:
        _READ_ROWS_CACHE.pop(k, None)


def _ensure_schema(conn, path_key, is_postgres=False):
    if path_key in _INITIALIZED_DBS:
        return
    conn.executescript(SCHEMA_PG if is_postgres else SCHEMA)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(player_maps)")}
    for col_name, col_type in MIGRATION_COLUMNS.items():
        if col_name not in existing:
            pg_type = "DOUBLE PRECISION" if is_postgres and col_type == "REAL" else col_type
            conn.execute(f"ALTER TABLE player_maps ADD COLUMN {col_name} {pg_type}")
    rounds_cols = {row["name"] for row in conn.execute("PRAGMA table_info(rounds)")}
    if "team_id" not in rounds_cols and not is_postgres:
        conn.execute("ALTER TABLE rounds RENAME TO _rounds_legacy")
        conn.execute(
            "CREATE TABLE rounds ("
            "match_id TEXT, map_no INTEGER, team_id TEXT NOT NULL DEFAULT '', "
            "round_no INTEGER, payload_json TEXT, "
            "PRIMARY KEY(match_id,map_no,team_id,round_no))"
        )
        for r in conn.execute("SELECT match_id, map_no, round_no, payload_json FROM _rounds_legacy"):
            try:
                p_obj = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except (TypeError, ValueError):
                p_obj = {}
            t_id = str(p_obj.get("team_id") or "")
            conn.execute(
                "INSERT OR REPLACE INTO rounds(match_id,map_no,team_id,round_no,payload_json) VALUES (?,?,?,?,?)",
                (r["match_id"], r["map_no"], t_id, r["round_no"], r["payload_json"]),
            )
        conn.execute("DROP TABLE _rounds_legacy")
    conn.execute("INSERT OR IGNORE INTO schema_meta VALUES (2)")
    conn.commit()
    _INITIALIZED_DBS.add(path_key)


@contextmanager
def connect(path=DB_PATH, readonly=False):
    if using_postgres(path):
        import psycopg
        from psycopg.rows import dict_row

        url = get_database_url()
        pg_conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
        compat = _PgCompatConn(pg_conn)
        pg_key = f"pg:{url.rsplit('@', 1)[-1]}"
        try:
            if not readonly:
                _ensure_schema(compat, pg_key, is_postgres=True)
            yield compat
            if not readonly:
                compat.commit()
        except Exception:
            try:
                compat.rollback()
            except Exception:
                pass
            raise
        finally:
            compat.close()
        return

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
        _ensure_schema(conn, path_key, is_postgres=False)
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
    if not db_available(path):
        return []
    is_pg = using_postgres(path)
    cache_key = None
    if not is_pg:
        path = Path(path)
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
    if not db_available(path):
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
    match_source = (rows[0].get("source") if rows else None) or match.get("source") or "faceit"
    conn.execute(
        "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?)",
        (match_id, rows[0]["date"] if rows else None, match_source, json.dumps(match), now),
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
    from projectcs.leetify import LeetifyError, normalize_leetify_match

    if not db_available(path):
        return {"matches": 0, "player_maps": 0, "failed": []}

    total_rows = 0
    processed = 0
    failed = []
    now = datetime.now(timezone.utc).isoformat()

    with connect(path) as conn:
        tracked = {}
        tracked_by_steam = {}
        for r in conn.execute("SELECT player_id, nickname, steam_id FROM players"):
            tracked[r["player_id"]] = r["nickname"]
            if r["steam_id"]:
                tracked_by_steam[str(r["steam_id"]).strip()] = {
                    "player_id": r["player_id"],
                    "nickname": r["nickname"],
                }
        stored = conn.execute("SELECT match_id, source, raw_json FROM matches ORDER BY started_at").fetchall()

        for item in stored:
            match_id = item["match_id"]
            conn.execute("SAVEPOINT sp_reprocess")
            try:
                payload = json.loads(item["raw_json"])
                if not isinstance(payload, dict):
                    raise ValueError("Payload JSON non valido")
                is_premier = item["source"] == "premier" or payload.get("source") == "premier" or str(match_id).startswith("leetify:")
                if is_premier:
                    rows = normalize_leetify_match(payload, tracked_by_steam)
                else:
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
            except (ValueError, KeyError, TypeError, FaceitError, LeetifyError) as exc:
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


def _validate_and_prepare_demo_trades(conn, match_id, map_no, events, window_seconds=5.0, tickrate=64.0):
    death_events = [
        ev for ev in (events or [])
        if isinstance(ev, dict) and ev.get("event_type", "player_death") == "player_death"
    ]
    if not death_events:
        return {"status": "empty", "updated": 0}

    if any(not _validate_death_event(ev, tickrate) for ev in death_events):
        return {"status": "invalid", "updated": 0}

    trade_counts = compute_round_trades(death_events, window_seconds=window_seconds, tickrate=tickrate)

    players_in_map = conn.execute(
        "SELECT player_id, team_id, kills, deaths, rounds, score_for, score_against, won, roster_count, map, date "
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
                            if p_counts["traded_deaths"] is None or p_counts["untraded_deaths"] is None:
                                return {"status": "incomplete", "updated": 0}
                            tot_d = p_counts["traded_deaths"] + p_counts["untraded_deaths"]
                            if (pid in tracked_pids and tot_d != exp_d) or (pid not in tracked_pids and tot_d < exp_d):
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

    by_round = {}
    for idx, ev in enumerate(death_events):
        by_round.setdefault(int(ev["round_no"]), []).append((idx, ev))
    annotated_events = [dict(ev) for ev in death_events]
    for r_no, indexed_evs in by_round.items():
        ordered = sorted(indexed_evs, key=lambda pair: (_extract_event_time_seconds(pair[1], tickrate), pair[0]))
        for pos, (orig_idx, ev) in enumerate(ordered):
            t1 = _extract_event_time_seconds(ev, tickrate)
            victim_id = ev.get("victim_id") or ev.get("player_id")
            victim_team = ev["victim_team"]
            attacker_id = ev["attacker_id"]
            attacker_team = ev["attacker_team"]
            is_enemy_kill = attacker_id != victim_id and attacker_team != victim_team
            traded = False
            if is_enemy_kill:
                for _, later in ordered[pos:]:
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
            if annotated_events[orig_idx].get("is_opening") is None:
                annotated_events[orig_idx]["is_opening"] = 1 if pos == 0 else 0
            if annotated_events[orig_idx].get("is_traded") is None:
                annotated_events[orig_idx]["is_traded"] = 1 if traded else 0

    return {
        "status": "complete",
        "players_in_map": players_in_map,
        "max_rounds": max_rounds,
        "annotated_events": annotated_events,
        "validated_updates": validated_updates,
    }


def _write_demo_trades_conn(conn, match_id, map_no, annotated_events, validated_updates):
    conn.execute("DELETE FROM events WHERE match_id=? AND map_no=?", (match_id, map_no))
    for idx, ev in enumerate(annotated_events):
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


def apply_demo_trades(match_id, map_no, events, path=DB_PATH, window_seconds=5.0, tickrate=64.0):
    """
    Valida e salva gli eventi di morte della demo in 'events' e aggiorna traded_deaths e
    untraded_deaths in 'player_maps' solo quando il killfeed della mappa è valido,
    include gli eventi di entrambi i team e riconcilia le morti di tutti i giocatori
    tracciati nella mappa. Un reimport invalido o incompleto preserva l'ultima analisi valida.
    """
    with connect(path) as conn:
        prep = _validate_and_prepare_demo_trades(
            conn,
            match_id,
            map_no,
            events,
            window_seconds=window_seconds,
            tickrate=tickrate,
        )
        if prep["status"] != "complete":
            return {"status": prep["status"], "updated": 0}
        _write_demo_trades_conn(
            conn,
            match_id,
            map_no,
            prep["annotated_events"],
            prep["validated_updates"],
        )

    _invalidate_cache(path)
    return {"status": "complete", "updated": len(prep["validated_updates"])}


def _validate_round_item(item, max_rounds=None):
    if not isinstance(item, dict):
        return False
    r_no = item.get("round_no")
    if r_no is None or isinstance(r_no, bool):
        return False
    try:
        r_int = int(r_no)
    except (TypeError, ValueError):
        return False
    if r_int < 1:
        return False
    if max_rounds is not None and max_rounds > 0 and r_int > max_rounds:
        return False
    side = item.get("side")
    if side is not None and str(side).upper() not in {"T", "CT"}:
        return False
    won = item.get("won")
    if won is not None and int(won) not in {0, 1}:
        return False
    plant_site = item.get("plant_site")
    if plant_site is not None and str(plant_site).upper() not in {"A", "B", ""}:
        return False
    return True


def save_demo_analysis(
    match_id,
    map_no,
    events,
    rounds=None,
    path=DB_PATH,
    window_seconds=5.0,
    tickrate=64.0,
    resource_url=None,
):
    """
    Salva in modo atomico sia gli eventi demo (trade + coordinate posizionali per heatmap)
    sia i dettagli round-per-round (lato T/CT, 5v4/4v5, siti bomba A/B, economia)
    in una singola transazione SQLite.
    Se gli eventi o i round sono invalidi o incompleti, non sovrascrive l'analisi valida precedente.
    """
    round_items = [dict(r) for r in (rounds or []) if isinstance(r, dict)]
    if rounds is not None and len(round_items) != len(list(rounds)):
        return {"status": "invalid", "updated": 0, "rounds_saved": 0}

    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        prep = _validate_and_prepare_demo_trades(
            conn,
            match_id,
            map_no,
            events,
            window_seconds=window_seconds,
            tickrate=tickrate,
        )
        if prep["status"] != "complete":
            return {"status": prep["status"], "updated": 0, "rounds_saved": 0}

        pm_rows = prep["players_in_map"]
        max_rounds = prep["max_rounds"]
        distinct_teams = sorted({str(r["team_id"]) for r in pm_rows if r["team_id"]})

        # Per considerare completa l'analisi demo tattica, i round devono essere presenti e riconciliati
        if max_rounds is not None and max_rounds > 0 and not round_items:
            return {"status": "incomplete", "updated": 0, "rounds_saved": 0}

        prepared_rounds = []
        if round_items:
            if any(not _validate_round_item(r, max_rounds=max_rounds) for r in round_items):
                return {"status": "invalid", "updated": 0, "rounds_saved": 0}
            if any(r.get("won") is None or r.get("side") is None for r in round_items):
                return {"status": "incomplete", "updated": 0, "rounds_saved": 0}

            rounds_by_team = {}
            rounds_list_by_team = {}
            for r in round_items:
                raw_tid = r.get("team_id")
                tid = str(raw_tid).strip() if raw_tid is not None and str(raw_tid).strip() != "" else None
                if tid is None:
                    if len(distinct_teams) == 1:
                        tid = distinct_teams[0]
                    else:
                        return {"status": "invalid", "updated": 0, "rounds_saved": 0}
                elif distinct_teams and tid not in distinct_teams:
                    return {"status": "invalid", "updated": 0, "rounds_saved": 0}

                r_int = int(r["round_no"])
                team_set = rounds_by_team.setdefault(tid, set())
                if r_int in team_set:
                    return {"status": "invalid", "updated": 0, "rounds_saved": 0}
                team_set.add(r_int)

                payload = dict(r)
                payload["match_id"] = match_id
                payload["map_no"] = int(map_no)
                payload["team_id"] = tid
                payload["round_no"] = r_int
                rounds_list_by_team.setdefault(tid, []).append(payload)
                prepared_rounds.append((tid, r_int, payload))

            if distinct_teams and set(rounds_by_team.keys()) != set(distinct_teams):
                return {"status": "incomplete", "updated": 0, "rounds_saved": 0}

            if max_rounds is not None and max_rounds > 0:
                if any(len(r_set) != max_rounds for r_set in rounds_by_team.values()):
                    return {"status": "incomplete", "updated": 0, "rounds_saved": 0}

            team_scores = {}
            for row in pm_rows:
                tid = str(row["team_id"]) if row["team_id"] else None
                if tid and tid not in team_scores:
                    team_scores[tid] = (row["score_for"], row["score_against"])

            for tid, t_rounds in rounds_list_by_team.items():
                sf, sa = team_scores.get(tid, (None, None))
                if sf is not None:
                    sf_int = int(sf)
                    sa_int = int(sa) if sa is not None else None
                    score_matches_rounds = (
                        max_rounds is None
                        or (sf_int <= max_rounds and (sa_int is None or (sf_int + sa_int) == max_rounds))
                    )
                    if score_matches_rounds:
                        derived_wins = sum(int(item["won"]) for item in t_rounds)
                        if derived_wins != sf_int:
                            return {"status": "incomplete", "updated": 0, "rounds_saved": 0}

        # Tutte le validazioni sono superate: scrittura atomica unica su events, player_maps, rounds e demo_status
        _write_demo_trades_conn(
            conn,
            match_id,
            map_no,
            prep["annotated_events"],
            prep["validated_updates"],
        )

        conn.execute("DELETE FROM rounds WHERE match_id=? AND map_no=?", (match_id, int(map_no)))
        for tid, r_int, payload in sorted(prepared_rounds, key=lambda item: (item[0], item[1])):
            conn.execute(
                "INSERT INTO rounds(match_id,map_no,team_id,round_no,payload_json) VALUES (?,?,?,?,?)",
                (match_id, int(map_no), tid, r_int, json.dumps(payload)),
            )

        prev = conn.execute(
            "SELECT attempts, resource_url FROM demo_status WHERE match_id=? AND map_no=?",
            (match_id, int(map_no)),
        ).fetchone()
        attempts = (int(prev["attempts"]) + 1) if prev else 1
        res_url = resource_url or (prev["resource_url"] if prev else None)
        conn.execute(
            "INSERT OR REPLACE INTO demo_status"
            "(match_id,map_no,status,attempts,resource_url,last_attempt_at,next_retry_at,error,details_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                match_id,
                int(map_no),
                "complete",
                attempts,
                res_url,
                now,
                None,
                None,
                json.dumps({"updated_players": len(prep["validated_updates"]), "rounds_saved": len(prepared_rounds)}),
            ),
        )

    _invalidate_cache(path)
    return {
        "status": "complete",
        "updated": len(prep["validated_updates"]),
        "rounds_saved": len(prepared_rounds),
    }


def record_demo_status(
    match_id,
    map_no,
    status,
    resource_url=None,
    error=None,
    next_retry_at=None,
    details=None,
    path=DB_PATH,
):
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        prev = conn.execute(
            "SELECT attempts, resource_url FROM demo_status WHERE match_id=? AND map_no=?",
            (match_id, int(map_no)),
        ).fetchone()
        attempts = (int(prev["attempts"]) + 1) if prev else 1
        res_url = resource_url or (prev["resource_url"] if prev else None)
        conn.execute(
            "INSERT OR REPLACE INTO demo_status"
            "(match_id,map_no,status,attempts,resource_url,last_attempt_at,next_retry_at,error,details_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                match_id,
                int(map_no),
                status,
                attempts,
                res_url,
                now,
                next_retry_at,
                str(error) if error else None,
                json.dumps(details or {}),
            ),
        )


def read_demo_events(path=DB_PATH):
    if not db_available(path):
        return []
    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "events" not in tables:
            return []
        map_lookup = {}
        if "player_maps" in tables:
            for r in conn.execute("SELECT DISTINCT match_id, map_no, map, date FROM player_maps"):
                map_lookup[(r["match_id"], int(r["map_no"]))] = (r["map"], r["date"])
        rows = []
        for r in conn.execute(
            "SELECT match_id, map_no, round_no, event_index, tick, event_type, player_id, payload_json "
            "FROM events ORDER BY match_id, map_no, round_no, event_index"
        ):
            try:
                payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except (TypeError, ValueError):
                payload = {}
            map_name, match_date = map_lookup.get((r["match_id"], int(r["map_no"])), (payload.get("map"), payload.get("date")))
            item = {
                "match_id": r["match_id"],
                "map_no": int(r["map_no"]),
                "round_no": int(r["round_no"]) if r["round_no"] is not None else None,
                "event_index": int(r["event_index"]),
                "tick": int(r["tick"]) if r["tick"] is not None else 0,
                "event_type": r["event_type"],
                "map": map_name,
                "date": match_date,
                "victim_id": payload.get("victim_id") or r["player_id"],
                "victim_name": payload.get("victim_name"),
                "victim_team": payload.get("victim_team"),
                "victim_side": payload.get("victim_side"),
                "attacker_id": payload.get("attacker_id"),
                "attacker_name": payload.get("attacker_name"),
                "attacker_team": payload.get("attacker_team"),
                "attacker_side": payload.get("attacker_side"),
                "time_sec": payload.get("time_sec"),
                "weapon": payload.get("weapon"),
                "headshot": payload.get("headshot"),
                "is_opening": payload.get("is_opening"),
                "is_traded": payload.get("is_traded"),
                "victim_x": payload.get("victim_x"),
                "victim_y": payload.get("victim_y"),
                "victim_z": payload.get("victim_z"),
                "attacker_x": payload.get("attacker_x"),
                "attacker_y": payload.get("attacker_y"),
                "attacker_z": payload.get("attacker_z"),
            }
            rows.append(item)
        return rows


def read_demo_rounds(path=DB_PATH):
    if not db_available(path):
        return []
    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "rounds" not in tables:
            return []
        team_lookup = {}
        teams_per_map = {}
        if "player_maps" in tables:
            for r in conn.execute("SELECT DISTINCT match_id, map_no, map, date, team_id, roster_count FROM player_maps"):
                m_key = (r["match_id"], int(r["map_no"]))
                t_key = (r["match_id"], int(r["map_no"]), str(r["team_id"]))
                team_lookup[t_key] = (r["map"], r["date"], r["team_id"], r["roster_count"])
                teams_per_map.setdefault(m_key, []).append((r["map"], r["date"], r["team_id"], r["roster_count"]))

        rounds_cols = {r["name"] for r in conn.execute("PRAGMA table_info(rounds)")}
        select_sql = (
            "SELECT match_id, map_no, team_id, round_no, payload_json FROM rounds ORDER BY match_id, map_no, team_id, round_no"
            if "team_id" in rounds_cols
            else "SELECT match_id, map_no, '' AS team_id, round_no, payload_json FROM rounds ORDER BY match_id, map_no, round_no"
        )
        rows = []
        for r in conn.execute(select_sql):
            try:
                payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except (TypeError, ValueError):
                continue
            m_key = (r["match_id"], int(r["map_no"]))
            explicit_tid = payload.get("team_id") or r["team_id"] or None
            if explicit_tid and (r["match_id"], int(r["map_no"]), str(explicit_tid)) in team_lookup:
                map_name, match_date, resolved_team, roster_cnt = team_lookup[(r["match_id"], int(r["map_no"]), str(explicit_tid))]
            elif not explicit_tid and len(teams_per_map.get(m_key, [])) == 1:
                map_name, match_date, resolved_team, roster_cnt = teams_per_map[m_key][0]
            elif teams_per_map.get(m_key):
                first_map, first_date, _, _ = teams_per_map[m_key][0]
                map_name, match_date, resolved_team, roster_cnt = first_map, first_date, explicit_tid, payload.get("roster_count")
            else:
                map_name, match_date, resolved_team, roster_cnt = (
                    payload.get("map"),
                    payload.get("date"),
                    explicit_tid,
                    payload.get("roster_count"),
                )
            rows.append({
                "match_id": r["match_id"],
                "map_no": int(r["map_no"]),
                "round_no": int(r["round_no"]),
                "map": payload.get("map") or map_name,
                "date": payload.get("date") or match_date,
                "team_id": resolved_team,
                "roster_count": payload.get("roster_count") if payload.get("roster_count") is not None else roster_cnt,
                "side": payload.get("side"),
                "won": payload.get("won"),
                "win_reason": payload.get("win_reason"),
                "first_kill": payload.get("first_kill"),
                "first_death": payload.get("first_death"),
                "plant_site": payload.get("plant_site"),
                "post_plant_won": payload.get("post_plant_won"),
                "retake_attempted": payload.get("retake_attempted"),
                "retake_won": payload.get("retake_won"),
                "equip_value": payload.get("equip_value"),
                "buy_type": payload.get("buy_type"),
            })
        return rows


def read_demo_status(path=DB_PATH):
    if not db_available(path):
        return []
    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "demo_status" not in tables:
            return []
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM demo_status ORDER BY last_attempt_at DESC, match_id, map_no"
            )
        ]


def _parse_csv_int(val):
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _parse_csv_float(val):
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_csv_bool(val):
    if val is None or str(val).strip() == "":
        return None
    text = str(val).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def restore_published_demo_state(db_path=DB_PATH, published_dir=None):
    """
    Ripristina nello SQLite locale (es. su runner stateless di GitHub Actions) le analisi demo
    e gli stati coda già pubblicati in `published/` per le partite presenti in `player_maps`
    ma prive di copertura demo completa nel DB corrente.
    """
    if using_postgres(db_path):
        return {"restored_maps": 0, "restored_status": 0}
    db_file = Path(db_path)
    if not db_file.exists():
        return {"restored_maps": 0, "restored_status": 0}

    candidates = []
    if published_dir is not None:
        candidates.append(Path(published_dir))
    else:
        candidates.append(db_file.resolve().parent / "published")
        if db_file.resolve() == Path(DB_PATH).resolve():
            candidates.append(Path(DB_PATH).resolve().parent.parent / "published")

    pub_dir = next((c for c in candidates if c.exists() and c.is_dir()), None)
    if pub_dir is None:
        return {"restored_maps": 0, "restored_status": 0}

    events_csv = pub_dir / "demo_events.csv"
    rounds_csv = pub_dir / "demo_rounds.csv"
    status_csv = pub_dir / "demo_status.csv"
    if not events_csv.exists() and not rounds_csv.exists() and not status_csv.exists():
        return {"restored_maps": 0, "restored_status": 0}

    with connect(db_file, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "player_maps" not in tables:
            return {"restored_maps": 0, "restored_status": 0}
        existing_maps = {
            (r["match_id"], int(r["map_no"]))
            for r in conn.execute("SELECT DISTINCT match_id, map_no FROM player_maps")
        }
        if not existing_maps:
            return {"restored_maps": 0, "restored_status": 0}

        completed_maps = set()
        if "demo_status" in tables and "rounds" in tables and "events" in tables:
            pm_teams_by_map = {}
            pm_max_rounds = {}
            for r in conn.execute("SELECT match_id, map_no, team_id, rounds FROM player_maps"):
                k = (r["match_id"], int(r["map_no"]))
                if r["team_id"]:
                    pm_teams_by_map.setdefault(k, set()).add(str(r["team_id"]))
                if r["rounds"] is not None:
                    pm_max_rounds[k] = max(pm_max_rounds.get(k, 0), int(r["rounds"]))

            rd_counts = {}
            for r in conn.execute(
                "SELECT match_id, map_no, team_id, COUNT(DISTINCT round_no) AS cnt "
                "FROM rounds GROUP BY match_id, map_no, team_id"
            ):
                rd_counts[(r["match_id"], int(r["map_no"]), str(r["team_id"] or ""))] = int(r["cnt"] or 0)

            ev_maps = {
                (r["match_id"], int(r["map_no"]))
                for r in conn.execute("SELECT DISTINCT match_id, map_no FROM events")
            }
            for r in conn.execute("SELECT match_id, map_no FROM demo_status WHERE status = 'complete'"):
                k = (r["match_id"], int(r["map_no"]))
                if k not in ev_maps:
                    continue
                exp_r = pm_max_rounds.get(k, 0)
                t_set = pm_teams_by_map.get(k, set())
                if t_set and all(rd_counts.get((k[0], k[1], tid), 0) >= max(1, exp_r) for tid in t_set):
                    completed_maps.add(k)

        existing_status_keys = (
            {
                (r["match_id"], int(r["map_no"]))
                for r in conn.execute("SELECT match_id, map_no FROM demo_status")
            }
            if "demo_status" in tables
            else set()
        )

    errors = []
    events_by_map = {}
    if events_csv.exists():
        try:
            with events_csv.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    mid = row.get("match_id")
                    mno = _parse_csv_int(row.get("map_no"))
                    if not mid or mno is None:
                        continue
                    key = (mid, mno)
                    if key not in existing_maps or key in completed_maps:
                        continue
                    events_by_map.setdefault(key, []).append({
                        "round_no": _parse_csv_int(row.get("round_no")),
                        "event_index": _parse_csv_int(row.get("event_index")) or 0,
                        "tick": _parse_csv_int(row.get("tick")) or 0,
                        "event_type": row.get("event_type") or "player_death",
                        "map": row.get("map") or None,
                        "date": row.get("date") or None,
                        "victim_id": row.get("victim_id") or None,
                        "victim_name": row.get("victim_name") or None,
                        "victim_team": row.get("victim_team") or None,
                        "victim_side": row.get("victim_side") or None,
                        "attacker_id": row.get("attacker_id") or None,
                        "attacker_name": row.get("attacker_name") or None,
                        "attacker_team": row.get("attacker_team") or None,
                        "attacker_side": row.get("attacker_side") or None,
                        "time_sec": _parse_csv_float(row.get("time_sec")),
                        "weapon": row.get("weapon") or None,
                        "headshot": _parse_csv_bool(row.get("headshot")),
                        "is_opening": _parse_csv_int(row.get("is_opening")),
                        "is_traded": _parse_csv_int(row.get("is_traded")),
                        "victim_x": _parse_csv_float(row.get("victim_x")),
                        "victim_y": _parse_csv_float(row.get("victim_y")),
                        "victim_z": _parse_csv_float(row.get("victim_z")),
                        "attacker_x": _parse_csv_float(row.get("attacker_x")),
                        "attacker_y": _parse_csv_float(row.get("attacker_y")),
                        "attacker_z": _parse_csv_float(row.get("attacker_z")),
                    })
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            errors.append(f"demo_events.csv: {exc}")
            events_by_map = {}

    rounds_by_map = {}
    if rounds_csv.exists():
        try:
            with rounds_csv.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    mid = row.get("match_id")
                    mno = _parse_csv_int(row.get("map_no"))
                    if not mid or mno is None:
                        continue
                    key = (mid, mno)
                    if key not in existing_maps or key in completed_maps:
                        continue
                    rounds_by_map.setdefault(key, []).append({
                        "round_no": _parse_csv_int(row.get("round_no")),
                        "map": row.get("map") or None,
                        "date": row.get("date") or None,
                        "team_id": row.get("team_id") or None,
                        "roster_count": _parse_csv_int(row.get("roster_count")),
                        "side": row.get("side") or None,
                        "won": _parse_csv_int(row.get("won")),
                        "win_reason": row.get("win_reason") or None,
                        "first_kill": _parse_csv_int(row.get("first_kill")),
                        "first_death": _parse_csv_int(row.get("first_death")),
                        "plant_site": row.get("plant_site") or None,
                        "post_plant_won": _parse_csv_int(row.get("post_plant_won")),
                        "retake_attempted": _parse_csv_int(row.get("retake_attempted")),
                        "retake_won": _parse_csv_int(row.get("retake_won")),
                        "equip_value": _parse_csv_float(row.get("equip_value")),
                        "buy_type": row.get("buy_type") or None,
                    })
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            errors.append(f"demo_rounds.csv: {exc}")
            rounds_by_map = {}

    restored_maps = 0
    for (mid, mno), ev_list in sorted(events_by_map.items()):
        rd_list = rounds_by_map.get((mid, mno)) or []
        if rd_list:
            res = save_demo_analysis(mid, mno, ev_list, rounds=rd_list, path=db_file)
            if res.get("status") == "complete":
                restored_maps += 1
                existing_status_keys.add((mid, mno))

    restored_status = 0
    if status_csv.exists():
        try:
            with status_csv.open("r", newline="", encoding="utf-8") as f:
                status_rows = list(csv.DictReader(f))
            if status_rows:
                with connect(db_file) as conn:
                    for s_row in status_rows:
                        mid = s_row.get("match_id")
                        mno = _parse_csv_int(s_row.get("map_no"))
                        if not mid or mno is None:
                            continue
                        key = (mid, mno)
                        if key not in existing_maps or key in existing_status_keys:
                            continue
                        st_val = str(s_row.get("status") or "").strip()
                        # Non ripristinare mai 'complete' se l'analisi (eventi + round) non è stata validata e salvata
                        if not st_val or st_val == "complete":
                            continue
                        conn.execute(
                            "INSERT OR IGNORE INTO demo_status"
                            "(match_id,map_no,status,attempts,resource_url,last_attempt_at,next_retry_at,error,details_json) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                mid,
                                mno,
                                st_val,
                                _parse_csv_int(s_row.get("attempts")) or 1,
                                s_row.get("resource_url") or None,
                                s_row.get("last_attempt_at") or datetime.now(timezone.utc).isoformat(),
                                s_row.get("next_retry_at") or None,
                                s_row.get("error") or None,
                                s_row.get("details_json") or "{}",
                            ),
                        )
                        existing_status_keys.add(key)
                        restored_status += 1
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            errors.append(f"demo_status.csv: {exc}")

    result = {"restored_maps": restored_maps, "restored_status": restored_status}
    if errors:
        result["errors"] = errors
    return result


def read_records(table, path=DB_PATH):
    if table not in {"reviews", "objectives"}:
        raise ValueError("Tabella non consentita")
    if not db_available(path):
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
    if using_postgres(path):
        return None
    path = Path(path)
    if not path.exists():
        return None
    with connect(path, readonly=True) as source:
        target = sqlite3.connect(":memory:")
        source.backup(target)
        result = target.serialize()
        target.close()
        return result


def migrate_sqlite_to_postgres(sqlite_path=DB_PATH, pg_url=None):
    """Migra tutte le tabelle dallo SQLite locale a Supabase PostgreSQL in modo idempotente."""
    import psycopg
    from psycopg.rows import dict_row

    src_path = Path(sqlite_path)
    if not src_path.exists():
        raise FileNotFoundError(f"SQLite non trovato: {src_path}")
    url = _normalize_pg_url(pg_url) if pg_url else get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL non configurata.")

    src = sqlite3.connect(f"file:{src_path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    src.row_factory = sqlite3.Row
    pg_raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=30)
    pg = _PgCompatConn(pg_raw)
    try:
        _ensure_schema(pg, f"pg_migrate:{url.rsplit('@', 1)[-1]}", is_postgres=True)
        counts = {}
        for tbl in ("players", "matches", "player_maps", "rounds", "events", "demo_status"):
            rows = [dict(r) for r in src.execute(f"SELECT * FROM {tbl}")]
            if not rows:
                counts[tbl] = 0
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join("?" for _ in cols)
            sql = f"INSERT OR REPLACE INTO {tbl}({','.join(cols)}) VALUES ({placeholders})"
            pg.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
            counts[tbl] = len(rows)

        for tbl, cols in (
            ("reviews", ["id", "created_at", "match_id", "round_no", "kind", "category", "observation", "status", "action"]),
            ("objectives", ["id", "created_at", "title", "action", "metric", "status"]),
        ):
            rows = [dict(r) for r in src.execute(f"SELECT * FROM {tbl}")]
            if rows:
                placeholders = ",".join("?" for _ in cols)
                pg.executemany(
                    f"INSERT INTO {tbl}({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
                    [tuple(r[c] for c in cols) for r in rows],
                )
                pg.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), COALESCE((SELECT MAX(id) FROM {tbl}), 1))")
            counts[tbl] = len(rows)

        pg.commit()
        return counts
    except Exception:
        pg.rollback()
        raise
    finally:
        src.close()
        pg.close()

