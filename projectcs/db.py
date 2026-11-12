import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from projectcs.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_meta VALUES (1);
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
 headshots REAL, score_for INTEGER, score_against INTEGER,
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

@contextmanager
def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def read_rows(path=DB_PATH):
    if not path.exists():
        return []
    with connect(path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM player_maps ORDER BY date")]

def save_match(match, rows, path=DB_PATH):
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?)",
                     (match["match_id"], rows[0]["date"] if rows else None,
                      "faceit", json.dumps(match), now))
        conn.execute("DELETE FROM player_maps WHERE match_id=?", (match["match_id"],))
        for row in rows:
            keys = list(row)
            sql = "INSERT INTO player_maps (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")"
            conn.execute(sql, [row[k] for k in keys])

def read_records(table, path=DB_PATH):
    if table not in {"reviews", "objectives"}:
        raise ValueError("Tabella non consentita")
    if not path.exists():
        return []
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC")]

def add_review(match_id, round_no, kind, category, observation, status, action, path=DB_PATH):
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO reviews(created_at,match_id,round_no,kind,category,observation,status,action) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), match_id, round_no, kind, category, observation, status, action))

def add_objective(title, action, metric, path=DB_PATH):
    with connect(path) as conn:
        conn.execute("INSERT INTO objectives(created_at,title,action,metric,status) VALUES (?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(), title, action, metric, "Da allenare"))

def set_objective_status(objective_id, status, path=DB_PATH):
    if status not in {"Da allenare", "In corso", "Da verificare", "Completato"}:
        raise ValueError("Stato non valido")
    with connect(path) as conn:
        conn.execute("UPDATE objectives SET status=? WHERE id=?", (status, objective_id))

def backup(path=DB_PATH):
    if not path.exists():
        return None
    with connect(path) as source:
        target = sqlite3.connect(":memory:")
        source.backup(target)
        result = target.serialize()
        target.close()
        return result
