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
}
EXCLUDED_SOURCES = {"faceit", "renown", "esportal", "esea", "challengeme", "popflash", "matchmaking_competitive", "wingman"}


class LeetifyError(RuntimeError):
    """Errore nelle chiamate o nella normalizzazione dei dati Leetify."""


def _num(value, integer=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (num == num) or num in (float("inf"), float("-inf")):
        return None
    return int(round(num)) if integer else num


def _safe_int(value):
    return _num(value, integer=True)


def _reaction_time_ms(ps):
    ms = _num(ps.get("reaction_time_ms"))
    if ms is not None:
        return ms
    raw = _num(ps.get("reaction_time"))
    if raw is None:
        return None
    return raw * 1000.0 if 0 < raw < 10 else raw


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
        return None
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
    return ds in PREMIER_SOURCES


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
            try:
                resp = self.session.get(url, headers=self.headers, params=params, timeout=30)
            except requests.RequestException as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise LeetifyError(f"Errore di rete/timeout Leetify API: {exc}") from exc
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
    if not date_iso:
        return []

    scores_by_team = {}
    raw_scores = detail.get("team_scores") or detail.get("teamScores") or []
    if isinstance(raw_scores, list):
        for entry in raw_scores:
            if isinstance(entry, dict):
                t_num = _safe_int(entry.get("team_number") if entry.get("team_number") is not None else entry.get("teamNumber"))
                sc = _num(entry.get("score"), integer=True)
                if t_num is not None and sc is not None:
                    scores_by_team[t_num] = sc
    elif isinstance(raw_scores, dict):
        for k, v in raw_scores.items():
            tk = _safe_int(k)
            sv = _safe_int(v)
            if tk is not None and sv is not None:
                scores_by_team[tk] = sv

    total_rounds = sum(scores_by_team.values()) if len(scores_by_team) == 2 else _num(detail.get("rounds_count"), integer=True)

    players_stats = detail.get("stats") or detail.get("player_stats") or []
    if not isinstance(players_stats, list):
        return []
    by_team = {}
    for ps in players_stats:
        if not isinstance(ps, dict):
            continue
        steam_id = str(ps.get("steam64_id") or ps.get("steam64Id") or "").strip()
        if steam_id not in tracked_by_steam:
            continue
        t_raw = ps.get("initial_team_number")
        if t_raw is None:
            t_raw = ps.get("team_number") if ps.get("team_number") is not None else ps.get("teamNumber")
        team_key = _safe_int(t_raw)
        if team_key is None:
            team_key = 0
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

            traded_deaths = _num(ps.get("traded_deaths_succeed"))
            untraded_deaths = (
                max(0.0, deaths - traded_deaths)
                if deaths is not None and traded_deaths is not None
                else None
            )
            he_thrown = _num(ps.get("he_thrown"))
            molotov_thrown = _num(ps.get("molotov_thrown"))
            smoke_thrown = _num(ps.get("smoke_thrown"))
            flash_thrown = _num(ps.get("flashbang_thrown"))
            util_parts = [v for v in (he_thrown, molotov_thrown, smoke_thrown, flash_thrown) if v is not None]
            utility_count = sum(util_parts) if util_parts else None
            he_avg = _num(ps.get("he_foes_damage_avg"))
            utility_damage = (
                he_avg * he_thrown
                if he_avg is not None and he_thrown is not None
                else None
            )

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
                "flash_count": flash_thrown,
                "flash_successes": None,
                "enemies_flashed": _num(ps.get("flashbang_hit_foe")),
                "utility_count": utility_count,
                "utility_damage": utility_damage,
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
                "traded_deaths": traded_deaths,
                "untraded_deaths": untraded_deaths,
                "first_half_score": None,
                "second_half_score": None,
                "overtime_score": None,
                "score_for": score_for,
                "score_against": score_against,
                "source": "premier",
                "coverage": "leetify_api",
                "leetify_rating": _num(ps.get("leetify_rating")),
                "ct_leetify_rating": _num(ps.get("ct_leetify_rating")),
                "t_leetify_rating": _num(ps.get("t_leetify_rating")),
                "preaim": _num(ps.get("preaim")),
                "reaction_time_ms": _reaction_time_ms(ps),
                "counter_strafe_good": _num(ps.get("counter_strafing_shots_good")),
                "counter_strafe_all": _num(ps.get("counter_strafing_shots_all")),
                "spray_accuracy": _num(ps.get("spray_accuracy")),
                "trade_kill_opp": _num(ps.get("trade_kill_opportunities")),
                "trade_kills_succeed": _num(ps.get("trade_kills_succeed")),
                "rounds_survived": _num(ps.get("rounds_survived")),
                "utility_on_death_avg": _num(ps.get("utility_on_death_avg")),
                "flash_assists": _num(ps.get("flashbang_leading_to_kill")),
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
        existing_ids = {
            r["match_id"]
            for r in conn.execute(
                "SELECT DISTINCT m.match_id FROM matches m "
                "INNER JOIN player_maps pm ON pm.match_id = m.match_id "
                "WHERE m.source='premier'"
            )
        }

    if not tracked_by_steam:
        raise LeetifyError("Nessuno steam_id configurato nella tabella players per la sincronizzazione Premier.")

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
                skipped += 1
                continue
            d_ts = _parse_iso_ts(detail.get("finished_at") or detail.get("finishedAt") or detail.get("created_at"))
            if d_ts is not None:
                if (since is not None and d_ts < since) or (until is not None and d_ts >= until):
                    skipped += 1
                    continue
            rows = normalize_leetify_match(detail, tracked_by_steam)
            if not rows:
                skipped += 1
                continue
            payload = dict(detail)
            payload["match_id"] = f"leetify:{gid}"
            payload["source"] = "premier"
            save_match(payload, rows, path=path)
            imported += 1
        except (LeetifyError, requests.RequestException, ValueError, TypeError, KeyError, OverflowError) as exc:
            failed.append({"match_id": f"leetify:{gid}", "error": str(exc)})

    return {
        "found": len(candidate_ids),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
    }

