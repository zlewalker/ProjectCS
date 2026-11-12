"""Uso: python -m projectcs.sync --since 2026-08-20 --until 2026-11-13"""
import argparse
from datetime import datetime, timezone
import json
import os

import requests

from projectcs.config import NAMES
from projectcs.db import connect, save_match
from projectcs.faceit import FaceitClient, FaceitError, normalize_match

def timestamp(text):
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

def main():
    parser = argparse.ArgumentParser(description="Importa lo storico dei cinque membri, incluse solo/duo.")
    parser.add_argument("--since", required=True, help="Data iniziale UTC inclusa: YYYY-MM-DD")
    parser.add_argument("--until", help="Data finale UTC esclusa; default adesso")
    args = parser.parse_args()
    since = timestamp(args.since)
    until = timestamp(args.until) if args.until else int(datetime.now(timezone.utc).timestamp())
    if since >= until:
        parser.error("--since deve precedere --until")
    key = os.environ.get("FACEIT_API_KEY")
    if not key:
        try:
            import tomllib
            from projectcs.config import ROOT
            with (ROOT / ".streamlit" / "secrets.toml").open("rb") as file:
                key = tomllib.load(file).get("FACEIT_API_KEY")
        except FileNotFoundError:
            pass
    try:
        client = FaceitClient(key)
        tracked = {}
        for nickname in NAMES:
            player = client.player(nickname)
            tracked[player["player_id"]] = nickname
            with connect() as conn:
                conn.execute("INSERT OR REPLACE INTO players VALUES (?,?,?)",
                             (player["player_id"], nickname, player.get("steam_id_64")))
        matches = {}
        for pid in tracked:
            for item in client.history(pid, since, until - 1):
                if str(item.get("status", "")).casefold() == "finished":
                    matches[item["match_id"]] = item
        print(f"Trovate {len(matches)} partite uniche. Importazione idempotente.")
        failed = []
        for index, match_id in enumerate(sorted(matches), 1):
            try:
                match = client.get(f"/matches/{match_id}")
                stats = client.get(f"/matches/{match_id}/stats")
                rows = normalize_match(match, stats, tracked)
                if not rows:
                    raise FaceitError("Statistiche non ancora disponibili")
                match["_projectcs_stats"] = stats
                save_match(match, rows)
                print(f"[{index}/{len(matches)}] {match_id}: {len(rows)} righe")
            except (FaceitError, requests.RequestException) as error:
                failed.append({"match_id": match_id, "error": type(error).__name__})
                print(f"[{index}/{len(matches)}] {match_id}: non importata, riprovare.")
        with connect() as conn:
            conn.execute("INSERT INTO analysis_runs(match_id,module,version,status,created_at,details_json) VALUES (?,?,?,?,?,?)",
                         (None, "faceit_sync", "1", "partial" if failed else "complete",
                          datetime.now(timezone.utc).isoformat(), json.dumps({"matches": len(matches), "failed": failed})))
        if failed:
            print(f"{len(failed)} partite da riprovare. I dati preesistenti sono conservati in caso di errore.")
            return 1
        return 0
    except (FaceitError, requests.RequestException) as error:
        print("Importazione interrotta: controlla chiave API, connessione e intervallo date.")
        print(str(error) if isinstance(error, FaceitError) else type(error).__name__)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
