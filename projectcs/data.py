import pandas as pd

from projectcs.db import read_rows
from projectcs.config import ROOT, DB_PATH

SNAPSHOT_PATH = ROOT / "published" / "player_maps.csv"

COLUMNS = ["match_id", "map_no", "team_id", "player_id", "nickname", "date", "map",
           "roster_count", "won", "rounds", "kills", "deaths", "assists", "adr",
           "headshots", "score_for", "score_against", "source", "coverage"]

def frame(rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    for column in ["won", "rounds", "kills", "deaths", "assists", "adr", "headshots",
                   "score_for", "score_against", "roster_count"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df

def using_snapshot():
    return not DB_PATH.exists() and SNAPSHOT_PATH.exists()

def load_data():
    if using_snapshot():
        return frame(pd.read_csv(SNAPSHOT_PATH).to_dict("records"))
    return frame(read_rows())

def team_maps(df):
    return df.drop_duplicates(["match_id", "map_no", "team_id"])

def summary(df):
    games = team_maps(df)
    known = games.dropna(subset=["won"])
    return {
        "games": len(games),
        "winrate": known["won"].mean() * 100 if len(known) else None,
        "wins": int(known["won"].sum()) if len(known) else 0,
        "rounds": int(games["rounds"].sum()),
    }

def player_summary(df):
    result = []
    for name, rows in df.groupby("nickname", sort=False):
        kd_rows = rows.dropna(subset=["kills", "deaths"])
        deaths = kd_rows["deaths"].sum()
        kpr_rows = rows.dropna(subset=["kills", "rounds"])
        rounds = kpr_rows["rounds"].sum()
        adr_rows = rows.dropna(subset=["adr", "rounds"])
        adr_rounds = adr_rows["rounds"].sum()
        dpr_rows = rows.dropna(subset=["deaths", "rounds"])
        dpr_rounds = dpr_rows["rounds"].sum()
        result.append({
            "Giocatore": name, "Mappe": len(rows),
            "K/D": kd_rows["kills"].sum() / deaths if deaths else None,
            "KPR": kpr_rows["kills"].sum() / rounds if rounds else None,
            "ADR": (adr_rows["adr"] * adr_rows["rounds"]).sum() / adr_rounds if adr_rounds else None,
            "DPR": dpr_rows["deaths"].sum() / dpr_rounds if dpr_rounds else None,
            "Vittorie %": rows["won"].mean() * 100,
        })
    return pd.DataFrame(result)

def map_summary(df):
    games = team_maps(df)
    rows = []
    for game_map, group in games.groupby("map"):
        known = group.dropna(subset=["won"])
        rows.append({
            "Mappa": game_map.title(), "Mappe": len(group),
            "Vittorie %": known["won"].mean() * 100 if len(known) else None,
            "Differenza round": (group["score_for"] - group["score_against"]).sum(min_count=1),
            "Round": group["rounds"].sum(min_count=1),
        })
    return pd.DataFrame(rows)
