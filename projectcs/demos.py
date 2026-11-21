"""Gestione coda demo CS2, download rate-limited da FACEIT, parsing e auto-pulizia file .dem.

Uso:
  python -m projectcs.demos --ingest-local --export-snapshot
  python -m projectcs.demos --download-missing --limit 5 --min-interval 12 --export-snapshot
"""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import requests

from projectcs.config import DB_PATH, NAMES, ROOT
from projectcs.db import (
    connect,
    db_available,
    read_demo_status,
    record_demo_status,
    restore_published_demo_state,
    save_demo_analysis,
)

DEMOS_DIR = ROOT / "demos"
DOWNLOAD_API_URL = "https://open.faceit.com/download/v2/demos/download"

# Parametri ufficiali CS2 (pos_x, pos_y, scale) per trasformare coordinate mondo (X, Y) in spazio radar [0..1024]
CS2_RADAR_META = {
    "ancient": {
        "pos_x": -2953.0,
        "pos_y": 2164.0,
        "scale": 5.0,
        "zones": [
            {"name": "A Site", "rx": 290, "ry": 265},
            {"name": "B Site", "rx": 785, "ry": 285},
            {"name": "Mid", "rx": 525, "ry": 465},
            {"name": "CT Spawn", "rx": 530, "ry": 155},
            {"name": "T Spawn", "rx": 515, "ry": 865},
        ],
    },
    "anubis": {
        "pos_x": -2796.0,
        "pos_y": 3328.0,
        "scale": 5.22,
        "zones": [
            {"name": "A Site", "rx": 730, "ry": 310},
            {"name": "B Site", "rx": 310, "ry": 430},
            {"name": "Mid / Bridge", "rx": 515, "ry": 470},
            {"name": "Canal / Water", "rx": 480, "ry": 585},
            {"name": "CT Spawn", "rx": 510, "ry": 210},
            {"name": "T Spawn", "rx": 520, "ry": 860},
        ],
    },
    "dust2": {
        "pos_x": -2476.0,
        "pos_y": 3239.0,
        "scale": 4.4,
        "zones": [
            {"name": "A Site", "rx": 800, "ry": 225},
            {"name": "B Site", "rx": 195, "ry": 210},
            {"name": "Mid Doors", "rx": 470, "ry": 480},
            {"name": "Long A", "rx": 845, "ry": 540},
            {"name": "CT Spawn", "rx": 570, "ry": 240},
            {"name": "T Spawn", "rx": 395, "ry": 885},
        ],
    },
    "mirage": {
        "pos_x": -3230.0,
        "pos_y": 1713.0,
        "scale": 5.0,
        "zones": [
            {"name": "A Site", "rx": 540, "ry": 730},
            {"name": "B Site", "rx": 245, "ry": 285},
            {"name": "Mid", "rx": 545, "ry": 465},
            {"name": "CT Spawn", "rx": 285, "ry": 700},
            {"name": "T Spawn", "rx": 865, "ry": 365},
        ],
    },
    "inferno": {
        "pos_x": -2087.0,
        "pos_y": 3870.0,
        "scale": 4.9,
        "zones": [
            {"name": "A Site", "rx": 785, "ry": 665},
            {"name": "B Site", "rx": 470, "ry": 225},
            {"name": "Banana", "rx": 455, "ry": 465},
            {"name": "Mid", "rx": 555, "ry": 655},
            {"name": "CT Spawn", "rx": 765, "ry": 345},
            {"name": "T Spawn", "rx": 175, "ry": 685},
        ],
    },
    "nuke": {
        "pos_x": -3453.0,
        "pos_y": 2887.0,
        "scale": 7.0,
        "zones": [
            {"name": "A / B Site", "rx": 585, "ry": 565},
            {"name": "Outside", "rx": 650, "ry": 755},
            {"name": "Ramp", "rx": 645, "ry": 395},
            {"name": "CT Spawn", "rx": 800, "ry": 495},
            {"name": "T Spawn", "rx": 265, "ry": 595},
        ],
    },
    "vertigo": {
        "pos_x": -3168.0,
        "pos_y": 1762.0,
        "scale": 4.0,
        "zones": [
            {"name": "A Site", "rx": 745, "ry": 595},
            {"name": "B Site", "rx": 345, "ry": 345},
            {"name": "Mid", "rx": 535, "ry": 455},
            {"name": "CT Spawn", "rx": 535, "ry": 240},
            {"name": "T Spawn", "rx": 475, "ry": 795},
        ],
    },
}


class DemoError(RuntimeError):
    """Errore generico nella pipeline demo."""


class DemoExpiredError(DemoError):
    """La demo non è più disponibile sui server FACEIT (404/410)."""


class DemoRateLimitDeferred(DemoError):
    """Limite di download raggiunto con tempo di attesa superiore a max_wait_seconds."""

    def __init__(self, message, wait_seconds, retry_at_iso):
        super().__init__(message)
        self.wait_seconds = float(wait_seconds)
        self.retry_at_iso = retry_at_iso


def normalize_map_key(map_name):
    if not map_name:
        return ""
    text = str(map_name).strip().lower()
    if text.startswith("de_"):
        text = text[3:]
    return text


def world_to_radar(x, y, map_name):
    """
    Converte coordinate CS2 (X, Y) in coordinate radar [0..1024].
    Se i valori sono nulli o NaN, restituisce (None, None).
    """
    if x is None or y is None:
        return None, None
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None, None
    if math.isnan(fx) or math.isnan(fy):
        return None, None

    meta = CS2_RADAR_META.get(normalize_map_key(map_name))
    if not meta:
        return None, None

    rx = (fx - meta["pos_x"]) / meta["scale"]
    ry = (meta["pos_y"] - fy) / meta["scale"]
    return round(rx, 2), round(ry, 2)


def parse_rate_limit_wait(headers, now_ts=None, default_wait=30.0):
    """
    Estrae i secondi di attesa dagli header HTTP di rate limit:
    - Retry-After (secondi interi o data HTTP RFC 2822)
    - X-RateLimit-Reset (epoch secondi o delta secondi)
    """
    if not headers:
        return float(default_wait)
    now = float(now_ts if now_ts is not None else time.time())
    norm = {str(k).lower(): str(v).strip() for k, v in headers.items() if v is not None}

    retry_after = norm.get("retry-after")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            try:
                dt = parsedate_to_datetime(retry_after)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(1.0, dt.timestamp() - now)
            except (TypeError, ValueError):
                pass

    reset_val = norm.get("x-ratelimit-reset")
    if reset_val:
        try:
            val = float(reset_val)
            # Se > 10^9 è un timestamp epoch UTC, altrimenti un delta in secondi
            if val > 1_000_000_000:
                return max(1.0, val - now)
            return max(1.0, val)
        except ValueError:
            pass

    return float(default_wait)


class RateLimitController:
    """
    Regola la cadenza dei download demo da FACEIT:
    - rispetta un intervallo minimo tra richieste consecutive (min_interval_seconds)
    - monitora X-RateLimit-Remaining e Retry-After / X-RateLimit-Reset
    - attende automaticamente il reset se <= max_wait_seconds, oppure differisce al run successivo.
    """

    def __init__(
        self,
        min_interval_seconds=10.0,
        max_wait_seconds=120.0,
        max_retries=3,
        base_backoff_seconds=15.0,
        time_fn=time.time,
        sleep_fn=time.sleep,
    ):
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.max_wait_seconds = max(0.0, float(max_wait_seconds))
        self.max_retries = max(1, int(max_retries))
        self.base_backoff_seconds = max(1.0, float(base_backoff_seconds))
        self.time_fn = time_fn
        self.sleep_fn = sleep_fn
        self.last_request_ts = None
        self.total_slept_seconds = 0.0

    def pace_before_request(self):
        now = float(self.time_fn())
        if self.last_request_ts is not None and self.min_interval_seconds > 0:
            elapsed = now - self.last_request_ts
            if elapsed < self.min_interval_seconds:
                wait_sec = self.min_interval_seconds - elapsed
                self.sleep_fn(wait_sec)
                self.total_slept_seconds += wait_sec
        self.last_request_ts = float(self.time_fn())

    def inspect_headers_proactive(self, headers):
        """Se X-RateLimit-Remaining == 0 su una risposta 200, attende il reset prima della richiesta successiva."""
        if not headers:
            return
        norm = {str(k).lower(): str(v).strip() for k, v in headers.items() if v is not None}
        rem = norm.get("x-ratelimit-remaining")
        if rem is not None:
            try:
                if int(float(rem)) <= 0:
                    wait_sec = parse_rate_limit_wait(headers, now_ts=self.time_fn(), default_wait=self.base_backoff_seconds)
                    if wait_sec <= self.max_wait_seconds:
                        self.sleep_fn(wait_sec)
                        self.total_slept_seconds += wait_sec
                        self.last_request_ts = float(self.time_fn())
            except ValueError:
                pass

    def handle_rate_limit_response(self, headers, attempt):
        now = float(self.time_fn())
        fallback = self.base_backoff_seconds * (2 ** max(0, attempt - 1))
        wait_sec = parse_rate_limit_wait(headers, now_ts=now, default_wait=fallback)
        retry_at = datetime.fromtimestamp(now + wait_sec, tz=timezone.utc).isoformat()
        if wait_sec > self.max_wait_seconds or attempt >= self.max_retries:
            raise DemoRateLimitDeferred(
                f"Rate limit FACEIT: attesa richiesta {wait_sec:.0f}s (superiore alla soglia o tentativi esauriti).",
                wait_seconds=wait_sec,
                retry_at_iso=retry_at,
            )
        self.sleep_fn(wait_sec)
        self.total_slept_seconds += wait_sec
        self.last_request_ts = float(self.time_fn())


class FaceitDemoDownloader:
    """Client per risolvere e scaricare demo CS2 da FACEIT con controllo rate-limit."""

    def __init__(self, api_key=None, rate_controller=None, session=None):
        self.api_key = (api_key or "").strip()
        self.controller = rate_controller or RateLimitController()
        self.session = session or requests.Session()

    def resolve_download_url(self, resource_url):
        if not resource_url:
            raise DemoExpiredError("Nessun resource_url presente nel match.")
        if not self.api_key:
            return resource_url

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        for attempt in range(1, self.controller.max_retries + 1):
            self.controller.pace_before_request()
            response = self.session.post(
                DOWNLOAD_API_URL,
                headers=headers,
                json={"resource_url": resource_url},
                timeout=30,
            )
            if response.status_code in (404, 410):
                raise DemoExpiredError(f"Demo scaduta o non trovata su FACEIT ({response.status_code}).")
            if response.status_code == 429:
                self.controller.handle_rate_limit_response(response.headers, attempt)
                continue
            if response.status_code in (401, 403):
                # Alcuni CDN consentono il GET diretto sul resource_url senza Downloads API key separata
                return resource_url
            if response.status_code >= 500:
                self.controller.handle_rate_limit_response(response.headers, attempt)
                continue
            if not response.ok:
                raise DemoError(f"Errore risoluzione URL demo FACEIT: HTTP {response.status_code}")

            self.controller.inspect_headers_proactive(response.headers)
            data = response.json()
            signed = (
                data.get("payload", {}).get("download_url")
                if isinstance(data, dict)
                else None
            )
            return signed or resource_url
        raise DemoError("Tentativi esauriti nella risoluzione dell'URL demo.")

    def download_to_file(self, resource_url, destination_path):
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        signed_url = self.resolve_download_url(resource_url)

        for attempt in range(1, self.controller.max_retries + 1):
            self.controller.pace_before_request()
            response = self.session.get(signed_url, stream=True, timeout=60)
            try:
                if response.status_code in (404, 410):
                    raise DemoExpiredError(f"File demo non più disponibile sul CDN ({response.status_code}).")
                if response.status_code == 429:
                    self.controller.handle_rate_limit_response(response.headers, attempt)
                    continue
                if response.status_code >= 500:
                    self.controller.handle_rate_limit_response(response.headers, attempt)
                    continue
                if not response.ok:
                    raise DemoError(f"Download demo fallito: HTTP {response.status_code}")

                self.controller.inspect_headers_proactive(response.headers)
                bytes_written = 0
                with dest.open("wb") as out_file:
                    for chunk in response.iter_content(chunk_size=262144):
                        if chunk:
                            out_file.write(chunk)
                            bytes_written += len(chunk)
                if bytes_written == 0:
                    raise DemoError("File demo scaricato vuoto (0 byte).")
                return dest
            finally:
                response.close()
        raise DemoError("Tentativi esauriti durante il download del file demo.")


def pending_demo_queue(path=DB_PATH, max_age_days=30, now_ts=None, force=False, published_dir=None):
    """
    Costruisce la coda ordinata delle mappe che necessitano di analisi demo:
    - esclude mappe già con coverage completa (trade + round tattici) o demo_status 'complete'/'expired' (salvo force=True)
    - rispetta next_retry_at in demo_status per i rate-limit differiti
    - filtra partite più vecchie di max_age_days (finestra di conservazione demo FACEIT)
    - ordina per roster_count DESC (prima i 5-stack e il core 3+), poi per data più recente.
    """
    path = Path(path)
    if not db_available(path):
        return []

    if published_dir is not None or path.resolve() == Path(DB_PATH).resolve():
        restore_published_demo_state(db_path=path, published_dir=published_dir)

    now_dt = (
        datetime.fromtimestamp(float(now_ts), tz=timezone.utc)
        if now_ts is not None
        else datetime.now(timezone.utc)
    )
    now_iso = now_dt.isoformat()
    cutoff_iso = (
        (now_dt - timedelta(days=float(max_age_days))).isoformat()
        if max_age_days is not None and max_age_days > 0
        else None
    )

    with connect(path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "matches" not in tables or "player_maps" not in tables:
            return []

        status_map = {}
        if "demo_status" in tables:
            for r in conn.execute("SELECT * FROM demo_status"):
                status_map[(r["match_id"], int(r["map_no"]))] = dict(r)

        teams_by_map = {}
        for r in conn.execute("SELECT DISTINCT match_id, map_no, team_id FROM player_maps"):
            if r["team_id"]:
                teams_by_map.setdefault((r["match_id"], int(r["map_no"])), set()).add(str(r["team_id"]))

        rounds_by_team = {}
        if "rounds" in tables:
            r_cols = {r["name"] for r in conn.execute("PRAGMA table_info(rounds)")}
            if "team_id" in r_cols:
                for r in conn.execute(
                    "SELECT match_id, map_no, team_id, COUNT(DISTINCT round_no) AS cnt "
                    "FROM rounds GROUP BY match_id, map_no, team_id"
                ):
                    rounds_by_team[(r["match_id"], int(r["map_no"]), str(r["team_id"] or ""))] = int(r["cnt"] or 0)
            else:
                for r in conn.execute(
                    "SELECT match_id, map_no, COUNT(DISTINCT round_no) AS cnt FROM rounds GROUP BY match_id, map_no"
                ):
                    rounds_by_team[(r["match_id"], int(r["map_no"]), "")] = int(r["cnt"] or 0)

        events_maps = (
            {
                (r["match_id"], int(r["map_no"]))
                for r in conn.execute("SELECT DISTINCT match_id, map_no FROM events")
            }
            if "events" in tables
            else set()
        )

        map_rows = conn.execute(
            """
            SELECT pm.match_id, pm.map_no, MAX(pm.map) AS map, MAX(pm.date) AS date,
                   MAX(pm.roster_count) AS roster_count,
                   MAX(pm.rounds) AS max_rounds,
                   MIN(CASE WHEN pm.coverage = 'Statistiche match + trade demo'
                             AND pm.untraded_deaths IS NOT NULL
                             AND pm.traded_deaths IS NOT NULL
                       THEN 1 ELSE 0 END) AS all_covered,
                   MAX(m.raw_json) AS raw_json
            FROM player_maps pm
            INNER JOIN matches m ON m.match_id = pm.match_id
            GROUP BY pm.match_id, pm.map_no
            """
        ).fetchall()

    queue = []
    for r in map_rows:
        match_id = r["match_id"]
        map_no = int(r["map_no"])
        key = (match_id, map_no)
        st = status_map.get(key)
        expected_rounds = int(r["max_rounds"] or 0)
        map_teams = teams_by_map.get(key, set())
        min_req = max(1, expected_rounds)
        tactical_complete = (
            key in events_maps
            and bool(map_teams)
            and all(rounds_by_team.get((match_id, map_no, tid), 0) >= min_req for tid in map_teams)
        )

        if not force:
            if r["all_covered"] == 1 and tactical_complete:
                continue
            if st and st.get("status") == "expired":
                continue
            if st and st.get("next_retry_at") and str(st["next_retry_at"]) > now_iso:
                continue

        match_date = str(r["date"] or "")
        if not force and cutoff_iso and match_date and match_date < cutoff_iso:
            continue

        demo_urls = []
        try:
            payload = json.loads(r["raw_json"]) if r["raw_json"] else {}
            if isinstance(payload, dict):
                raw_urls = payload.get("demo_url") or []
                if isinstance(raw_urls, str):
                    demo_urls = [raw_urls]
                elif isinstance(raw_urls, list):
                    demo_urls = [str(u) for u in raw_urls if u]
        except (TypeError, ValueError):
            demo_urls = []

        resource_url = demo_urls[map_no] if 0 <= map_no < len(demo_urls) else None
        if not resource_url:
            continue

        queue.append({
            "match_id": match_id,
            "map_no": map_no,
            "map": r["map"],
            "date": match_date,
            "roster_count": int(r["roster_count"] or 0),
            "resource_url": resource_url,
            "attempts": int(st["attempts"]) if st and st.get("attempts") is not None else 0,
        })

    queue.sort(key=lambda item: (item["roster_count"], item["date"], item["match_id"]), reverse=True)
    return queue


def decompress_demo_file(source_path, target_dem_path):
    """Decomprime un file .dem.gz o .dem.zst (o copia un .dem non compresso) in target_dem_path."""
    src = Path(source_path)
    dst = Path(target_dem_path)
    suffixes = "".join(src.suffixes).lower()

    if suffixes.endswith(".gz"):
        with gzip.open(src, "rb") as f_in, dst.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return dst

    if suffixes.endswith(".zst"):
        try:
            import zstandard as zstd

            dctx = zstd.ZstdDecompressor()
            with src.open("rb") as f_in, dst.open("wb") as f_out:
                dctx.copy_stream(f_in, f_out)
            return dst
        except ImportError:
            zstd_bin = shutil.which("zstd")
            if not zstd_bin:
                raise DemoError(
                    "Per decomprimere file .dem.zst installa il pacchetto Python 'zstandard' o l'utility 'zstd'."
                )
            subprocess.run([zstd_bin, "-d", "-f", str(src), "-o", str(dst)], check=True)
            return dst

    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst


def _parse_demo_event(parser, event_name, parse_errors=None, mandatory=False, strict=False, **kwargs):
    try:
        df = parser.parse_event(event_name, **kwargs)
        if df is not None and hasattr(df, "empty") and not df.empty:
            return df
        return None
    except Exception as exc:
        if parse_errors is not None:
            parse_errors[event_name] = str(exc)
        if mandatory or strict:
            raise DemoError(f"Errore estrazione evento '{event_name}': {exc}") from exc
        return None


def _finite_float(val):
    if val is None or isinstance(val, bool):
        return None
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return None
    return fval if math.isfinite(fval) else None


def _infer_plant_site(row, map_name):
    raw_site = row.get("site")
    if raw_site is not None:
        s_str = str(raw_site).strip().upper()
        if s_str in {"A", "B"}:
            return s_str
    bx = row.get("user_X") if row.get("user_X") is not None else row.get("X")
    by = row.get("user_Y") if row.get("user_Y") is not None else row.get("Y")
    rx, ry = world_to_radar(bx, by, map_name)
    meta = CS2_RADAR_META.get(normalize_map_key(map_name))
    if rx is not None and ry is not None and meta:
        site_zones = [z for z in meta.get("zones", []) if z["name"].startswith(("A ", "B "))]
        if site_zones:
            nearest = min(site_zones, key=lambda z: math.hypot(rx - z["rx"], ry - z["ry"]))
            return "A" if nearest["name"].startswith("A") else "B"
    return None


def parse_raw_dem_with_demoparser2(dem_path, match_id, map_no, db_path=DB_PATH, strict=False):
    """
    Estrae eventi player_death (con coordinate X, Y, Z) e round tattici da un file .dem CS2
    usando demoparser2, riconciliando i player_id e i team_id con i membri del match in SQLite.
    Non deduce mai il vincitore del round dalla prima kill se round_end fallisce o manca.
    """
    try:
        from demoparser2 import DemoParser
    except ImportError as exc:
        raise DemoError(
            "Libreria 'demoparser2' non installata. Installa 'demoparser2' per analizzare file binari .dem "
            "oppure fornisci un bundle JSON pre-estratto."
        ) from exc

    # Mappa steam_id_64 / nickname -> (player_id, team_id, nickname) dal match salvato
    steam_to_player = {}
    name_to_player = {}
    tracked_teams = []
    map_name = None
    with connect(db_path, readonly=True) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "players" in tables:
            for r in conn.execute("SELECT player_id, nickname, steam_id FROM players"):
                if r["steam_id"]:
                    steam_to_player[str(r["steam_id"])] = r["player_id"]
                if r["nickname"]:
                    name_to_player[str(r["nickname"]).casefold()] = r["player_id"]
        if "player_maps" in tables:
            for r in conn.execute(
                "SELECT player_id, nickname, team_id, map FROM player_maps WHERE match_id=? AND map_no=?",
                (match_id, int(map_no)),
            ):
                if r["map"] and not map_name:
                    map_name = r["map"]
                tid = str(r["team_id"]) if r["team_id"] else None
                if tid and tid not in tracked_teams:
                    tracked_teams.append(tid)
                if r["nickname"]:
                    name_to_player[str(r["nickname"]).casefold()] = (r["player_id"], tid, r["nickname"])
        if "matches" in tables:
            m_row = conn.execute("SELECT raw_json FROM matches WHERE match_id=?", (match_id,)).fetchone()
            if m_row and m_row["raw_json"]:
                try:
                    payload = json.loads(m_row["raw_json"])
                    for t_key, t_obj in (payload.get("teams") or {}).items():
                        t_id = t_obj.get("faction_id") or t_key
                        for p in t_obj.get("roster") or []:
                            pid = p.get("player_id")
                            nick = p.get("nickname")
                            steam64 = p.get("game_player_id")
                            if pid and steam64:
                                steam_to_player[str(steam64)] = (pid, t_id, nick)
                            if pid and nick:
                                name_to_player[str(nick).casefold()] = (pid, t_id, nick)
                except (TypeError, ValueError):
                    pass

    parse_errors = {}
    parser = DemoParser(str(dem_path))
    match_start_tick = 0
    try:
        ev_list = parser.list_game_events() if hasattr(parser, "list_game_events") else None
        if ev_list is None or "begin_new_match" in ev_list:
            bnm_df = parser.parse_event("begin_new_match")
            if bnm_df is not None and hasattr(bnm_df, "empty") and not bnm_df.empty and "tick" in bnm_df:
                ticks = [int(t) for t in bnm_df["tick"].dropna().tolist()]
                if ticks:
                    match_start_tick = max(ticks)
    except Exception:
        match_start_tick = 0

    deaths_df = _parse_demo_event(
        parser,
        "player_death",
        parse_errors=parse_errors,
        mandatory=True,
        strict=strict,
        player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played"],
    )
    round_end_df = _parse_demo_event(
        parser,
        "round_end",
        parse_errors=parse_errors,
        strict=strict,
        other=["total_rounds_played", "winner", "reason"],
    )
    bomb_planted_df = _parse_demo_event(
        parser,
        "bomb_planted",
        parse_errors=parse_errors,
        strict=strict,
        player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played", "site"],
    )
    bomb_defused_df = _parse_demo_event(
        parser,
        "bomb_defused",
        parse_errors=parse_errors,
        strict=strict,
        other=["total_rounds_played", "site"],
    )
    bomb_exploded_df = _parse_demo_event(
        parser,
        "bomb_exploded",
        parse_errors=parse_errors,
        strict=strict,
        other=["total_rounds_played", "site"],
    )
    freeze_end_df = _parse_demo_event(
        parser,
        "round_freeze_end",
        parse_errors=parse_errors,
        strict=strict,
        player=["team_num", "current_equip_value"],
        other=["total_rounds_played"],
    )

    def _clean_str(val):
        if val is None:
            return ""
        s = str(val).strip()
        return "" if s.lower() in ("none", "nan") else s

    def _row_tick(row):
        t = _finite_float(row.get("tick"))
        return int(t) if t is not None else None

    round_winners = {}
    round_end_ticks = {}
    match_end_tick = 0
    if "round_end" not in parse_errors and round_end_df is not None:
        valid_re_rows = []
        for _, row in round_end_df.iterrows():
            r_tick = _row_tick(row)
            if match_start_tick > 0 and r_tick is not None and r_tick < match_start_tick:
                continue
            w_raw = row.get("winner")
            w_side = None
            if w_raw in (2, "2", "T", "t", "TERRORIST"):
                w_side = "T"
            elif w_raw in (3, "3", "CT", "ct", "COUNTERTERRORIST"):
                w_side = "CT"
            if w_side is None:
                continue
            valid_re_rows.append((row, r_tick, w_side))

        re_offset = 1
        if valid_re_rows:
            played_vals = [int(row.get("total_rounds_played", 0)) for row, _, _ in valid_re_rows]
            if min(played_vals) >= 1:
                re_offset = 0
        for row, r_tick, w_side in valid_re_rows:
            r_no = int(row.get("total_rounds_played", 0)) + re_offset
            round_winners[r_no] = (w_side, row.get("reason"))
            if r_tick is not None:
                round_end_ticks[r_no] = r_tick
                if r_tick > match_end_tick:
                    match_end_tick = r_tick

    events = []
    if deaths_df is not None:
        for _, row in deaths_df.iterrows():
            tick = _row_tick(row)
            if match_start_tick > 0 and tick is not None and tick < match_start_tick:
                continue
            if match_end_tick > 0 and tick is not None and tick > match_end_tick:
                continue
            round_no = int(row.get("total_rounds_played", 0)) + 1
            tick_val = tick if tick is not None else 0
            v_steam = _clean_str(row.get("user_steamid"))
            a_steam = _clean_str(row.get("attacker_steamid"))
            v_name = _clean_str(row.get("user_name"))
            a_name = _clean_str(row.get("attacker_name"))
            if not v_steam and not v_name:
                continue

            v_info = steam_to_player.get(v_steam) or name_to_player.get(v_name.casefold())
            a_info = steam_to_player.get(a_steam) or name_to_player.get(a_name.casefold())
            v_pid = v_info[0] if isinstance(v_info, tuple) else (v_info or v_steam or v_name)
            a_pid = a_info[0] if isinstance(a_info, tuple) else (a_info or a_steam or a_name)
            v_team = v_info[1] if isinstance(v_info, tuple) and v_info[1] else _clean_str(row.get("user_team_num"))
            a_team = a_info[1] if isinstance(a_info, tuple) and a_info[1] else _clean_str(row.get("attacker_team_num"))

            if not a_pid:
                a_pid = "world"
            if not a_team:
                a_team = "world"

            v_side_num = row.get("user_team_num")
            a_side_num = row.get("attacker_team_num")
            v_side = "T" if v_side_num == 2 else ("CT" if v_side_num == 3 else None)
            a_side = "T" if a_side_num == 2 else ("CT" if a_side_num == 3 else None)

            events.append({
                "round_no": round_no,
                "tick": tick_val,
                "time_sec": round(tick_val / 64.0, 3),
                "victim_id": v_pid,
                "victim_name": v_name,
                "victim_team": v_team,
                "victim_side": v_side,
                "attacker_id": a_pid,
                "attacker_name": a_name,
                "attacker_team": a_team,
                "attacker_side": a_side,
                "weapon": row.get("weapon"),
                "headshot": bool(row.get("headshot")),
                "victim_x": _finite_float(row.get("user_X")),
                "victim_y": _finite_float(row.get("user_Y")),
                "victim_z": _finite_float(row.get("user_Z")),
                "attacker_x": _finite_float(row.get("attacker_X")),
                "attacker_y": _finite_float(row.get("attacker_Y")),
                "attacker_z": _finite_float(row.get("attacker_Z")),
            })

    # Se l'estrazione obbligatoria di round_end è fallita con eccezione, non fabbricare mai round fittizi
    if "round_end" in parse_errors:
        return {"events": events, "rounds": [], "parse_errors": parse_errors}

    deaths_by_round = {}
    for ev in events:
        deaths_by_round.setdefault(int(ev["round_no"]), []).append(ev)

    def _in_live_window(row):
        t = _row_tick(row)
        if t is None:
            return True
        if match_start_tick > 0 and t < match_start_tick:
            return False
        if match_end_tick > 0 and t > match_end_tick:
            return False
        return True

    bomb_events_ok = not any(k in parse_errors for k in ("bomb_planted", "bomb_defused", "bomb_exploded"))
    plants_by_round = {}
    if bomb_planted_df is not None:
        for _, row in bomb_planted_df.iterrows():
            if not _in_live_window(row):
                continue
            r_no = int(row.get("total_rounds_played", 0)) + 1
            if "site" in row or "user_X" in row or "X" in row:
                plants_by_round[r_no] = (_infer_plant_site(row, map_name), int(row.get("tick") or 0))

    defused_rounds = set()
    if bomb_defused_df is not None:
        for _, row in bomb_defused_df.iterrows():
            if not _in_live_window(row):
                continue
            if "site" in row or "winner" in row or "tick" in row:
                if "user_name" not in row or "site" in row:
                    defused_rounds.add(int(row.get("total_rounds_played", 0)) + 1)

    exploded_rounds = set()
    if bomb_exploded_df is not None:
        for _, row in bomb_exploded_df.iterrows():
            if not _in_live_window(row):
                continue
            if "site" in row or "winner" in row or "tick" in row:
                if "user_name" not in row or "site" in row:
                    exploded_rounds.add(int(row.get("total_rounds_played", 0)) + 1)

    equip_by_round_side = {}
    freeze_end_ticks = {}
    if freeze_end_df is not None:
        tick_to_round = {}
        raw_equip_lists = {}
        invalid_equip_keys = set()
        has_inline_equip = "current_equip_value" in getattr(freeze_end_df, "columns", [])
        for idx, (_, row) in enumerate(freeze_end_df.iterrows()):
            if not _in_live_window(row):
                continue
            r_no = int(row.get("total_rounds_played", idx)) + 1
            if row.get("tick") is not None:
                fe_tick = int(row["tick"])
                tick_to_round[fe_tick] = r_no
                freeze_end_ticks[r_no] = fe_tick
            if has_inline_equip:
                t_num = row.get("user_team_num") or row.get("team_num")
                s_key = "T" if t_num == 2 else ("CT" if t_num == 3 else None)
                if s_key:
                    f_eq = _finite_float(row.get("current_equip_value"))
                    if f_eq is None:
                        invalid_equip_keys.add((r_no, s_key))
                    else:
                        raw_equip_lists.setdefault((r_no, s_key), []).append(f_eq)

        if not raw_equip_lists and not invalid_equip_keys and tick_to_round and hasattr(parser, "parse_ticks"):
            try:
                ticks_df = parser.parse_ticks(["current_equip_value", "team_num"], ticks=sorted(tick_to_round))
                if ticks_df is not None and hasattr(ticks_df, "empty") and not ticks_df.empty:
                    for _, trow in ticks_df.iterrows():
                        r_no = tick_to_round.get(int(trow.get("tick", -1)))
                        t_num = trow.get("team_num") or trow.get("user_team_num")
                        s_key = "T" if t_num == 2 else ("CT" if t_num == 3 else None)
                        if r_no is not None and s_key:
                            f_eq = _finite_float(trow.get("current_equip_value"))
                            if f_eq is None:
                                invalid_equip_keys.add((r_no, s_key))
                            else:
                                raw_equip_lists.setdefault((r_no, s_key), []).append(f_eq)
            except Exception as exc:
                parse_errors["parse_ticks"] = str(exc)

        max_samples = max((len(v) for v in raw_equip_lists.values()), default=0)
        required_players = 5 if max_samples > 1 else 1
        for k, vals in raw_equip_lists.items():
            if k not in invalid_equip_keys and len(vals) == required_players:
                equip_by_round_side[k] = sum(vals)

    # Rimuove le morti ambientali ('world') avvenute in freeze-time / cambio lato prima di round_freeze_end
    if freeze_end_ticks:
        events = [
            ev for ev in events
            if not (
                ev.get("weapon") == "world"
                and ev.get("tick") is not None
                and ev["tick"] < freeze_end_ticks.get(int(ev["round_no"]), 0)
            )
        ]
        deaths_by_round = {}
        for ev in events:
            deaths_by_round.setdefault(int(ev["round_no"]), []).append(ev)

    all_round_nos = sorted(set(deaths_by_round) | set(round_winners) | set(plants_by_round))
    target_teams = tracked_teams or [None]
    rounds = []

    for team_id in target_teams:
        r1_side = None
        for r_no in all_round_nos:
            fe_t = freeze_end_ticks.get(r_no, 0)
            for ev in deaths_by_round.get(r_no, []):
                if fe_t and ev.get("tick", 0) < fe_t:
                    continue
                if team_id and ev.get("victim_team") == team_id and ev.get("victim_side") in {"T", "CT"}:
                    r1_side = ev["victim_side"] if r_no <= 12 else ("CT" if ev["victim_side"] == "T" else "T")
                    break
                if team_id and ev.get("attacker_team") == team_id and ev.get("attacker_side") in {"T", "CT"}:
                    r1_side = ev["attacker_side"] if r_no <= 12 else ("CT" if ev["attacker_side"] == "T" else "T")
                    break
            if r1_side:
                break
        if not r1_side:
            r1_side = "T"

        for r_no in all_round_nos:
            r_evs = sorted(deaths_by_round.get(r_no, []), key=lambda e: (e.get("tick", 0), e.get("time_sec", 0.0)))
            fe_t = freeze_end_ticks.get(r_no, 0)
            side = None
            for ev in r_evs:
                if fe_t and ev.get("tick", 0) < fe_t:
                    continue
                if team_id and ev.get("victim_team") == team_id and ev.get("victim_side") in {"T", "CT"}:
                    side = ev["victim_side"]
                    break
                if team_id and ev.get("attacker_team") == team_id and ev.get("attacker_side") in {"T", "CT"}:
                    side = ev["attacker_side"]
                    break
                if not team_id and ev.get("victim_side") in {"T", "CT"}:
                    side = ev["victim_side"]
                    break
            if not side:
                if r_no <= 12:
                    side = r1_side
                elif r_no <= 24:
                    side = "CT" if r1_side == "T" else "T"
                else:
                    ot_pos = (r_no - 25) % 6
                    side = r1_side if ot_pos < 3 else ("CT" if r1_side == "T" else "T")

            first_kill = 0
            first_death = 0
            if r_evs:
                first_ev = r_evs[0]
                if team_id:
                    if first_ev.get("attacker_team") == team_id and first_ev.get("victim_team") != team_id:
                        first_kill = 1
                    elif first_ev.get("victim_team") == team_id:
                        first_death = 1
                else:
                    first_death = 1

            w_side, w_reason = round_winners.get(r_no, (None, None))
            if w_side in {"T", "CT"}:
                won = 1 if w_side == side else 0
            elif r_no in defused_rounds:
                won = 1 if side == "CT" else 0
            elif r_no in exploded_rounds:
                won = 1 if side == "T" else 0
            else:
                won = None

            if not bomb_events_ok:
                plant_site = None
                post_plant_won = None
                retake_attempted = None
                retake_won = None
            elif r_no in plants_by_round:
                plant_site, plant_tick = plants_by_round[r_no]
                if side == "T":
                    post_plant_won = won if plant_site in {"A", "B"} and won is not None else None
                    retake_attempted = 0
                    retake_won = None
                else:
                    post_plant_won = None
                    if plant_site in {"A", "B"}:
                        contested_after_plant = (
                            r_no in defused_rounds
                            or won == 1
                            or any(int(ev.get("tick") or 0) > plant_tick for ev in r_evs)
                        )
                        retake_attempted = 1 if contested_after_plant else 0
                        retake_won = won if retake_attempted == 1 and won is not None else None
                    else:
                        retake_attempted = None
                        retake_won = None
            else:
                plant_site = None
                post_plant_won = None
                retake_attempted = 0
                retake_won = None

            eq_val = _finite_float(equip_by_round_side.get((r_no, side)))
            if eq_val is not None:
                eq_val = round(eq_val, 0)
            if r_no in (1, 13):
                buy_type = "Pistol"
            elif eq_val is not None:
                if eq_val < 5000:
                    buy_type = "Eco"
                elif eq_val < 18000:
                    buy_type = "Force Buy"
                else:
                    buy_type = "Full Buy"
            else:
                buy_type = None

            rounds.append({
                "round_no": r_no,
                "team_id": team_id,
                "side": side,
                "won": won,
                "win_reason": str(w_reason) if w_reason is not None else None,
                "first_kill": first_kill,
                "first_death": first_death,
                "plant_site": plant_site,
                "post_plant_won": post_plant_won,
                "retake_attempted": retake_attempted,
                "retake_won": retake_won,
                "equip_value": eq_val,
                "buy_type": buy_type,
            })

    res_dict = {"events": events, "rounds": rounds}
    if parse_errors:
        res_dict["parse_errors"] = parse_errors
    return res_dict


def parse_demo_source(file_path, match_id, map_no, db_path=DB_PATH, demo_parser=None):
    """
    Legge un file demo locale (.json, .dem, .dem.gz, .dem.zst) e restituisce
    {"events": [...], "rounds": [...]}.
    Se il file è compresso (.gz/.zst), estrae in un file temporaneo e lo rimuove subito nel blocco finally.
    """
    src = Path(file_path)
    if not src.exists():
        raise DemoError(f"File demo non trovato: {src}")

    if src.suffix.lower() == ".json":
        payload = json.loads(src.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {"events": payload, "rounds": []}
        if isinstance(payload, dict):
            return {
                "events": payload.get("events") or [],
                "rounds": payload.get("rounds") or [],
            }
        raise DemoError("Formato JSON demo non valido.")

    parser_fn = demo_parser or parse_raw_dem_with_demoparser2
    suffixes = "".join(src.suffixes).lower()
    if suffixes.endswith((".gz", ".zst")):
        tmp_dem = src.parent / f".decompressed_{match_id}_{map_no}_{os.getpid()}.dem"
        try:
            decompress_demo_file(src, tmp_dem)
            return parser_fn(tmp_dem, match_id, map_no, db_path=db_path)
        finally:
            tmp_dem.unlink(missing_ok=True)

    return parser_fn(src, match_id, map_no, db_path=db_path)


def infer_match_and_map_from_filename(filename):
    """
    Riconosce i formati standard dei file demo:
    - <match_id>-1-<map_index_1based>.dem(.zst|.gz)  (formato CDN FACEIT)
    - <match_id>_map<map_no>.dem(.zst|.gz|.json)
    - <match_id>.dem(.zst|.gz|.json)                 (default map_no = 0)
    """
    name = Path(filename).name
    for ext in (".dem.zst", ".dem.gz", ".dem", ".json"):
        if name.lower().endswith(ext):
            stem = name[: -len(ext)]
            break
    else:
        return None, None

    m_faceit = re.match(r"^(.+)-1-(\d+)$", stem)
    if m_faceit:
        return m_faceit.group(1), max(0, int(m_faceit.group(2)) - 1)

    m_map = re.match(r"^(.+)_map(\d+)$", stem, re.IGNORECASE)
    if m_map:
        return m_map.group(1), int(m_map.group(2))

    return stem, 0


def process_local_demo_file(
    file_path,
    match_id=None,
    map_no=None,
    db_path=DB_PATH,
    delete_after_ingest=True,
    delete_on_failure=False,
    demo_parser=None,
    resource_url=None,
):
    """
    Elabora un singolo file demo locale, applica la validazione e riconciliazione sul DB
    e cancella automaticamente il file sorgente quando l'importazione è completa ('complete').
    In caso di errore o dati incompleti, conserva il file locale salvo delete_on_failure=True.
    """
    src = Path(file_path)
    if match_id is None or map_no is None:
        inferred_id, inferred_map = infer_match_and_map_from_filename(src.name)
        match_id = match_id or inferred_id
        map_no = int(map_no if map_no is not None else (inferred_map or 0))

    if not match_id:
        return {"match_id": None, "map_no": None, "status": "unrecognized_file", "deleted": False}

    try:
        parsed = parse_demo_source(src, match_id, map_no, db_path=db_path, demo_parser=demo_parser)
        if isinstance(parsed, dict) and parsed.get("parse_errors"):
            err_msg = "; ".join(f"{k}: {v}" for k, v in parsed["parse_errors"].items())
            raise DemoError(f"Errore estrazione eventi demo: {err_msg}")
        res = save_demo_analysis(
            match_id,
            map_no,
            parsed.get("events") or [],
            rounds=parsed.get("rounds") or [],
            path=db_path,
            resource_url=resource_url,
        )
    except Exception as exc:
        record_demo_status(match_id, map_no, "error", resource_url=resource_url, error=str(exc), path=db_path)
        deleted = False
        if delete_on_failure and src.exists():
            src.unlink(missing_ok=True)
            deleted = True
        return {
            "match_id": match_id,
            "map_no": map_no,
            "status": "error",
            "error": str(exc),
            "deleted": deleted,
        }

    status = res["status"]
    if status != "complete":
        record_demo_status(match_id, map_no, status, resource_url=resource_url, path=db_path)

    deleted = False
    if (status == "complete" and delete_after_ingest) or (status != "complete" and delete_on_failure):
        if src.exists():
            src.unlink(missing_ok=True)
            deleted = True

    return {
        "match_id": match_id,
        "map_no": map_no,
        "status": status,
        "updated": res.get("updated", 0),
        "rounds_saved": res.get("rounds_saved", 0),
        "deleted": deleted,
    }


def ingest_local_demos(
    demos_dir=DEMOS_DIR,
    db_path=DB_PATH,
    delete_after_ingest=True,
    delete_on_failure=False,
    demo_parser=None,
):
    """
    Scansiona la cartella locale 'demos/', elabora tutti i file demo riconosciuti (.json, .dem, .dem.gz, .dem.zst)
    e rimuove automaticamente quelli completati con successo.
    """
    folder = Path(demos_dir)
    if not folder.exists():
        return {"processed": 0, "completed": 0, "deleted": 0, "failed": [], "results": []}

    candidates = sorted(
        p
        for p in folder.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.name.lower().endswith((".json", ".dem", ".dem.gz", ".dem.zst"))
    )
    results = []
    completed = 0
    deleted = 0
    failed = []
    for file_path in candidates:
        item_res = process_local_demo_file(
            file_path,
            db_path=db_path,
            delete_after_ingest=delete_after_ingest,
            delete_on_failure=delete_on_failure,
            demo_parser=demo_parser,
        )
        results.append(item_res)
        if item_res["status"] == "complete":
            completed += 1
        else:
            failed.append(item_res)
        if item_res.get("deleted"):
            deleted += 1

    return {
        "processed": len(results),
        "completed": completed,
        "deleted": deleted,
        "failed": failed,
        "results": results,
    }


def download_and_process_queue(
    db_path=DB_PATH,
    demos_dir=DEMOS_DIR,
    downloader=None,
    demo_parser=None,
    limit=5,
    max_age_days=30,
    force=False,
    now_ts=None,
):
    """
    Scarica gradualmente le demo mancanti dalla coda prioritaria, le analizza e cancella
    sempre il file temporaneo scaricato nel blocco finally.
    Se incontra un rate-limit lungo (DemoRateLimitDeferred), registra next_retry_at e
    interrompe ordinatamente il batch corrente senza perdere i progressi già salvati.
    """
    queue = pending_demo_queue(path=db_path, max_age_days=max_age_days, now_ts=now_ts, force=force)
    selected = queue[: max(0, int(limit))] if limit is not None else queue
    if not selected:
        return {
            "queued": 0,
            "attempted": 0,
            "completed": 0,
            "expired": 0,
            "rate_limited": False,
            "failed": [],
            "results": [],
        }

    dl = downloader or FaceitDemoDownloader()
    folder = Path(demos_dir)
    folder.mkdir(parents=True, exist_ok=True)

    attempted = 0
    completed = 0
    expired = 0
    rate_limited = False
    failed = []
    results = []

    for item in selected:
        match_id = item["match_id"]
        map_no = item["map_no"]
        res_url = item["resource_url"]
        attempted += 1

        ext = ".dem.zst" if res_url.lower().endswith(".zst") else (".dem.gz" if res_url.lower().endswith(".gz") else ".dem")
        tmp_download = folder / f".download_{match_id}_{map_no}_{os.getpid()}{ext}"
        try:
            dl.download_to_file(res_url, tmp_download)
            proc = process_local_demo_file(
                tmp_download,
                match_id=match_id,
                map_no=map_no,
                db_path=db_path,
                delete_after_ingest=True,
                delete_on_failure=True,
                demo_parser=demo_parser,
                resource_url=res_url,
            )
            results.append(proc)
            if proc["status"] == "complete":
                completed += 1
            else:
                failed.append(proc)
        except DemoExpiredError as exc:
            expired += 1
            record_demo_status(match_id, map_no, "expired", resource_url=res_url, error=str(exc), path=db_path)
            results.append({"match_id": match_id, "map_no": map_no, "status": "expired", "deleted": True})
        except DemoRateLimitDeferred as exc:
            rate_limited = True
            record_demo_status(
                match_id,
                map_no,
                "rate_limited",
                resource_url=res_url,
                error=str(exc),
                next_retry_at=exc.retry_at_iso,
                details={"wait_seconds": exc.wait_seconds},
                path=db_path,
            )
            results.append({
                "match_id": match_id,
                "map_no": map_no,
                "status": "rate_limited",
                "retry_at": exc.retry_at_iso,
                "deleted": True,
            })
            break
        except Exception as exc:
            record_demo_status(match_id, map_no, "error", resource_url=res_url, error=str(exc), path=db_path)
            err_item = {"match_id": match_id, "map_no": map_no, "status": "error", "error": str(exc), "deleted": True}
            failed.append(err_item)
            results.append(err_item)
        finally:
            tmp_download.unlink(missing_ok=True)

    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO analysis_runs(match_id,module,version,status,created_at,details_json) VALUES (?,?,?,?,?,?)",
            (
                None,
                "demo_sync",
                "1",
                "rate_limited" if rate_limited else ("partial" if failed else "complete"),
                datetime.now(timezone.utc).isoformat(),
                json.dumps({
                    "queued": len(queue),
                    "attempted": attempted,
                    "completed": completed,
                    "expired": expired,
                    "rate_limited": rate_limited,
                    "failed_count": len(failed),
                }),
            ),
        )

    return {
        "queued": len(queue),
        "attempted": attempted,
        "completed": completed,
        "expired": expired,
        "rate_limited": rate_limited,
        "failed": failed,
        "results": results,
    }


def _load_api_key():
    key = os.environ.get("FACEIT_DOWNLOAD_API_KEY") or os.environ.get("FACEIT_API_KEY")
    if not key:
        try:
            import tomllib

            secrets_path = ROOT / ".streamlit" / "secrets.toml"
            if secrets_path.exists():
                data = tomllib.loads(secrets_path.read_text(encoding="utf-8-sig"))
                key = data.get("FACEIT_DOWNLOAD_API_KEY") or data.get("FACEIT_API_KEY")
        except Exception:
            pass
    return key


def main():
    parser = argparse.ArgumentParser(
        description="Elabora le demo locali in demos/, scarica gradualmente le demo FACEIT mancanti ed elimina i .dem."
    )
    parser.add_argument("--ingest-local", action="store_true", help="Analizza i file presenti nella cartella demos/")
    parser.add_argument("--download-missing", action="store_true", help="Scarica le demo mancanti da FACEIT con rate-limit")
    parser.add_argument("--limit", type=int, default=5, help="Numero massimo di demo da scaricare per esecuzione")
    parser.add_argument("--min-interval", type=float, default=10.0, help="Secondi minimi tra due download consecutivi")
    parser.add_argument("--max-wait", type=float, default=120.0, help="Attesa massima (s) per reset rate-limit prima di differire")
    parser.add_argument("--max-age-days", type=int, default=30, help="Età massima (giorni) dei match per il download remoto demo")
    parser.add_argument("--keep-local", action="store_true", help="Non cancellare i file locali in demos/ dopo l'analisi")
    parser.add_argument("--force", action="store_true", help="Rielabora anche le mappe già completate")
    parser.add_argument("--export-snapshot", action="store_true", help="Aggiorna lo snapshot in published/")
    args = parser.parse_args()

    if not args.ingest_local and not args.download_missing:
        args.ingest_local = True

    exit_code = 0
    if args.ingest_local:
        local_res = ingest_local_demos(
            demos_dir=DEMOS_DIR,
            db_path=DB_PATH,
            delete_after_ingest=not args.keep_local,
        )
        print(
            f"Demo locali: {local_res['processed']} elaborate, "
            f"{local_res['completed']} completate, {local_res['deleted']} file rimossi."
        )
        if local_res["failed"]:
            exit_code = 1

    if args.download_missing:
        controller = RateLimitController(
            min_interval_seconds=args.min_interval,
            max_wait_seconds=args.max_wait,
        )
        downloader = FaceitDemoDownloader(api_key=_load_api_key(), rate_controller=controller)
        dl_res = download_and_process_queue(
            db_path=DB_PATH,
            demos_dir=DEMOS_DIR,
            downloader=downloader,
            limit=args.limit,
            max_age_days=args.max_age_days,
            force=args.force,
        )
        print(
            f"Coda demo FACEIT: {dl_res['queued']} in coda, {dl_res['attempted']} tentate, "
            f"{dl_res['completed']} completate, {dl_res['expired']} scadute."
        )
        if dl_res["rate_limited"]:
            print("Rate limit FACEIT raggiunto: i download rimanenti sono stati pianificati per il prossimo run.")
        if dl_res["failed"]:
            exit_code = 1

    if args.export_snapshot:
        from projectcs.sync import export_snapshot

        try:
            meta = export_snapshot()
            print(
                f"Snapshot esportato: {meta['matches']} partite, {meta['player_maps']} righe, "
                f"{meta.get('demo_events', 0)} eventi demo, {meta.get('demo_rounds', 0)} round demo."
            )
        except Exception as exc:
            print(f"Esportazione snapshot fallita: {exc}")
            return 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
