"""Client e normalizzazione per partite CS2 Premier tramite Leetify Public API."""
from datetime import datetime, timezone
import os
from pathlib import Path
import time

import requests

from projectcs.config import DB_PATH, ROOT
from projectcs.db import connect, save_match

BASE_URL = "https://api-public.cs-prod.leetify.com"
PREMIER_SOURCES = {
    "matchmaking",
    "matchmaking_premier",
    "premier",
    "valve",
}
EXCLUDED_SOURCES = {"faceit", "renown", "esportal", "esea", "challengeme", "popflash"}


class LeetifyError(RuntimeError):
    """Errore nelle chiamate o nella normalizzazione dei dati Leetify."""


def _num(value, integer=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(num)) if integer else num


def _parse_iso_ts(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _iso_utc(value):
    ts = _parse_iso_ts(value)
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def is_premier_match(item):
    if not isinstance(item, dict):
        return False
    ds = str(
        item.get("dataSource")
        or item.get("data_source")
        or ""
    ).strip().lower()
    if not ds or ds in EXCLUDED_SOURCES:
        return False
    return ds in PREMIER_SOURCES or "premier" in ds or "matchmaking" in ds


class LeetifyClient:
    def __init__(self, api_key, session=None):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise LeetifyError("LEETIFY_API_KEY mancante.")
        self.session = session or requests.Session()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "_leetify_key": self.api_key,
            "Accept": "application/json",
        }

    def get(self, path, params=None, allow_not_found=False):
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        for attempt in range(4):
            resp = self.session.get(url, headers=self.headers, params=params, timeout=30)
            if allow_not_found and resp.status_code == 404:
                return None
            if resp.status_code == 401 or resp.status_code == 403:
                raise LeetifyError(f"LEETIFY_API_KEY non autorizzata ({resp.status_code}).")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
            if not resp.ok:
                raise LeetifyError(f"Errore API Leetify {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise LeetifyError("Tentativi esauriti con Leetify API.")

    def profile_matches(self, steam64_id):
        data = self.get("/v3/profile/matches", params={"steam64_id": str(steam64_id)}, allow_not_found=True)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("matches") or data.get("data") or []
        return []

    def match_details(self, game_id):
        return self.get(f"/v2/matches/{game_id}", allow_not_found=True)


def normalize_leetify_match(detail, tracked_by_steam):
    """Converte un match dettaglio Leetify (/v2/matches/{id}) in righe player_maps con source='premier'."""
    if not isinstance(detail, dict):
        return []
    if not is_premier_match(detail):
        return []

    raw_id = str(detail.get("id") or detail.get("gameId") or "").strip()
    if not raw_id:
        return []
    match_id = raw_id if raw_id.startswith("leetify:") else f"leetify:{raw_id}"

    raw_map = str(detail.get("map_name") or detail.get("mapName") or "unknown").strip().lower()
    game_map = raw_map.removeprefix("de_")
    date_iso = _iso_utc(detail.get("finished_at") or detail.get("finishedAt") or detail.get("created_at"))

    scores_by_team = {}
    raw_scores = detail.get("team_scores") or detail.get("teamScores") or []
    if isinstance(raw_scores, list):
        for entry in raw_scores:
            if isinstance(entry, dict):
                t_num = entry.get("team_number") if entry.get("team_number") is not None else entry.get("teamNumber")
                sc = _num(entry.get("score"), integer=True)
                if t_num is not None and sc is not None:
                    scores_by_team[int(t_num)] = sc
    elif isinstance(raw_scores, dict):
        for k, v in raw_scores.items():
            try:
                scores_by_team[int(k)] = int(v)
            except (TypeError, ValueError):
                pass

    total_rounds = sum(scores_by_team.values()) if len(scores_by_team) == 2 else _num(detail.get("rounds_count"), integer=True)

    players_stats = detail.get("stats") or detail.get("player_stats") or []
    by_team = {}
    for ps in players_stats:
        if not isinstance(ps, dict):
            continue
        steam_id = str(ps.get("steam64_id") or ps.get("steam64Id") or "").strip()
        if steam_id not in tracked_by_steam:
            continue
        t_num = ps.get("initial_team_number")
        if t_num is None:
            t_num = ps.get("team_number") if ps.get("team_number") is not None else ps.get("teamNumber")
        team_key = int(t_num) if t_num is not None else 0
        by_team.setdefault(team_key, []).append((tracked_by_steam[steam_id], ps))

    rows = []
    for team_num, members in by_team.items():
        roster_count = len(members)
        if not roster_count:
            continue
        team_id = f"premier:{raw_id}:t{team_num}"
        score_for = scores_by_team.get(team_num)
        other_scores = [sc for tk, sc in scores_by_team.items() if tk != team_num]
        score_against = other_scores[0] if len(other_scores) == 1 else None
        won = None
        if score_for is not None and score_against is not None and score_for != score_against:
            won = int(score_for > score_against)

        for info, ps in members:
            pid = info["player_id"]
            nickname = info["nickname"]
            kills = _num(ps.get("total_kills") if ps.get("total_kills") is not None else ps.get("kills"))
            deaths = _num(ps.get("total_deaths") if ps.get("total_deaths") is not None else ps.get("deaths"))
            assists = _num(ps.get("total_assists") if ps.get("total_assists") is not None else ps.get("assists"))
            hs = _num(ps.get("total_hs_kills") if ps.get("total_hs_kills") is not None else ps.get("headshots"))
            dmg = _num(ps.get("total_damage") if ps.get("total_damage") is not None else ps.get("damage"))
            adr = _num(ps.get("dpr") if ps.get("dpr") is not None else ps.get("adr"))
            if adr is None and dmg is not None and total_rounds and total_rounds > 0:
                adr = dmg / total_rounds

            rows.append({
                "match_id": match_id,
                "map_no": 0,
                "team_id": team_id,
                "player_id": pid,
                "nickname": nickname,
                "date": date_iso,
                "map": game_map,
                "roster_count": roster_count,
                "won": won,
                "rounds": total_rounds,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "adr": adr,
                "headshots": hs,
                "damage": dmg,
                "first_kills": None,
                "entry_count": None,
                "entry_wins": None,
                "sniper_kills": None,
                "flash_count": _num(ps.get("flashbang_thrown")),
                "flash_successes": None,
                "enemies_flashed": _num(ps.get("flashbang_hit_foe")),
                "utility_count": _num(ps.get("he_thrown")),
                "utility_damage": _num(ps.get("he_foes_damage_avg")),
                "utility_enemies": None,
                "clutch_kills": None,
                "clutch_1v1_count": None,
                "clutch_1v1_wins": None,
                "clutch_1v2_count": None,
                "clutch_1v2_wins": None,
                "pistol_kills": None,
                "mvps": _num(ps.get("mvps")),
                "double_kills": _num(ps.get("multi2k")),
                "triple_kills": _num(ps.get("multi3k")),
                "quadro_kills": _num(ps.get("multi4k")),
                "penta_kills": _num(ps.get("multi5k")),
                "traded_deaths": None,
                "untraded_deaths": None,
                "first_half_score": None,
                "second_half_score": None,
                "overtime_score": None,
                "score_for": score_for,
                "score_against": score_against,
                "source": "premier",
                "coverage": "leetify_api",
            })
    return rows


def sync_leetify_premier(api_key, path=DB_PATH, since=None, until=None, force=False, client=None):
    """Importa in modo idempotente le partite Premier dei membri del roster da Leetify Public API."""
    cli = client or LeetifyClient(api_key)
    tracked_by_steam = {}
    with connect(path) as conn:
        for r in conn.execute("SELECT player_id, nickname, steam_id FROM players"):
            if r["steam_id"]:
                tracked_by_steam[str(r["steam_id"]).strip()] = {
                    "player_id": r["player_id"],
                    "nickname": r["nickname"],
                }
        existing_ids = {r["match_id"] for r in conn.execute("SELECT match_id FROM matches WHERE source='premier'")}

    if not tracked_by_steam:
        return {"found": 0, "imported": 0, "skipped": 0, "failed": []}

    candidate_ids = set()
    for steam_id in sorted(tracked_by_steam):
        for item in cli.profile_matches(steam_id):
            if not is_premier_match(item):
                continue
            gid = str(item.get("id") or item.get("gameId") or "").strip()
            if not gid:
                continue
            ts = _parse_iso_ts(item.get("finished_at") or item.get("finishedAt"))
            if since is not None and ts is not None and ts < since:
                continue
            if until is not None and ts is not None and ts >= until:
                continue
            candidate_ids.add(gid)

    pending = [
        gid for gid in sorted(candidate_ids)
        if force or f"leetify:{gid}" not in existing_ids
    ]
    skipped = len(candidate_ids) - len(pending)
    imported = 0
    failed = []
    for gid in pending:
        try:
            detail = cli.match_details(gid)
            if not detail:
                continue
            rows = normalize_leetify_match(detail, tracked_by_steam)
            if not rows:
                continue
            payload = dict(detail)
            payload["match_id"] = f"leetify:{gid}"
            payload["source"] = "premier"
            save_match(payload, rows, path=path)
            imported += 1
        except (LeetifyError, requests.RequestException) as exc:
            failed.append({"match_id": f"leetify:{gid}", "error": str(exc)})

    return {
        "found": len(candidate_ids),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
    }
