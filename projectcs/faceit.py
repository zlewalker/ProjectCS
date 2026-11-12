"""Client ufficiale FACEIT; nessun accesso ad endpoint privati o scraping."""
from datetime import datetime, timezone
import time

import requests

BASE = "https://open.faceit.com/data/v4"

class FaceitError(RuntimeError):
    pass

class FaceitClient:
    def __init__(self, api_key):
        if not api_key or not api_key.strip():
            raise FaceitError("Manca FACEIT_API_KEY.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + api_key.strip()

    def get(self, path, **params):
        for attempt in range(4):
            response = self.session.get(BASE + path, params=params, timeout=35)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    time.sleep(min(2 ** (attempt + 1), 8))
                    continue
            if not response.ok:
                raise FaceitError(f"FACEIT HTTP {response.status_code} su {path.split('?')[0]}")
            return response.json()
        raise FaceitError("FACEIT temporaneamente non disponibile.")

    def player(self, nickname):
        return self.get("/players", nickname=nickname, game="cs2")

    def history(self, player_id, since, until):
        for offset in range(0, 1001, 100):
            batch = self.get(f"/players/{player_id}/history", game="cs2",
                             **{"from": since, "to": until, "offset": offset, "limit": 100}).get("items", [])
            for match in batch:
                yield match
            if len(batch) < 100:
                return
        raise FaceitError("Limite storico raggiunto: importa un intervallo di date più breve.")

def number(value, integer=False):
    if value is None or value == "":
        return None
    try:
        result = float(str(value).replace("%", "").strip())
        return int(result) if integer else result
    except (ValueError, TypeError):
        return None

def normalize_match(match, stats, tracked):
    """Una riga per membro/mappa/squadra. Mai unire membri su squadre opposte."""
    match_id = match["match_id"]
    rows = []
    fallback_rosters = {
        key: {p.get("player_id") for p in team.get("roster", [])}
        for key, team in match.get("teams", {}).items()
    }
    for map_no, game in enumerate(stats.get("rounds", [])):
        shared = game.get("round_stats", {})
        game_map = str(shared.get("Map", "unknown")).lower().removeprefix("de_")
        rounds = number(shared.get("Rounds"), integer=True)
        teams = game.get("teams", [])
        for team in teams:
            team_id = team.get("team_id", "")
            members = team.get("players", [])
            member_ids = {p.get("player_id") for p in members}
            count = len(member_ids & tracked.keys())
            if not count:
                continue
            faction = next((key for key, ids in fallback_rosters.items() if len(ids & member_ids) >= 3), None)
            team_stats = team.get("team_stats", {})
            score_for = number(team_stats.get("Final Score"), integer=True)
            other = next((t for t in teams if t.get("team_id") != team_id), {})
            score_against = number(other.get("team_stats", {}).get("Final Score"), integer=True)
            played_rounds = rounds
            if played_rounds is None and score_for is not None and score_against is not None:
                played_rounds = score_for + score_against
            won = number(team_stats.get("Team Win"), integer=True)
            if won not in (0, 1):
                if score_for is not None and score_against is not None and score_for != score_against:
                    won = int(score_for > score_against)
                elif len(stats.get("rounds", [])) == 1 and faction and match.get("results", {}).get("winner"):
                    won = int(match["results"]["winner"] == faction)
                else:
                    won = None
            stamp = match.get("finished_at") or match.get("started_at")
            if not stamp:
                raise FaceitError(f"Data assente per {match_id}")
            for player in members:
                pid = player.get("player_id")
                if pid not in tracked:
                    continue
                ps = player.get("player_stats", {})
                rows.append(dict(
                    match_id=match_id, map_no=map_no, team_id=team_id, player_id=pid,
                    nickname=tracked[pid], date=datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    map=game_map, roster_count=count, won=won, rounds=played_rounds,
                    kills=number(ps.get("Kills")), deaths=number(ps.get("Deaths")),
                    assists=number(ps.get("Assists")), adr=number(ps.get("ADR")),
                    headshots=number(ps.get("Headshots")),
                    score_for=score_for, score_against=score_against,
                    source="faceit", coverage="Statistiche match"))
    return rows
