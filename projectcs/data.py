from pathlib import Path
import pandas as pd

from projectcs.config import DB_PATH, NAMES, ROOT
from projectcs.db import db_available, read_demo_events, read_demo_rounds, read_rows

SNAPSHOT_PATH = ROOT / "published" / "player_maps.csv"
DEMO_EVENTS_SNAPSHOT_PATH = ROOT / "published" / "demo_events.csv"
DEMO_ROUNDS_SNAPSHOT_PATH = ROOT / "published" / "demo_rounds.csv"

COLUMNS = [
    "match_id",
    "map_no",
    "team_id",
    "player_id",
    "nickname",
    "date",
    "map",
    "roster_count",
    "won",
    "rounds",
    "kills",
    "deaths",
    "assists",
    "adr",
    "headshots",
    "damage",
    "first_kills",
    "entry_count",
    "entry_wins",
    "sniper_kills",
    "flash_count",
    "flash_successes",
    "enemies_flashed",
    "utility_count",
    "utility_damage",
    "utility_enemies",
    "clutch_kills",
    "clutch_1v1_count",
    "clutch_1v1_wins",
    "clutch_1v2_count",
    "clutch_1v2_wins",
    "pistol_kills",
    "mvps",
    "double_kills",
    "triple_kills",
    "quadro_kills",
    "penta_kills",
    "traded_deaths",
    "untraded_deaths",
    "first_half_score",
    "second_half_score",
    "overtime_score",
    "score_for",
    "score_against",
    "source",
    "coverage",
    "leetify_rating",
    "ct_leetify_rating",
    "t_leetify_rating",
    "preaim",
    "reaction_time_ms",
    "counter_strafe_good",
    "counter_strafe_all",
    "spray_accuracy",
    "trade_kill_opp",
    "trade_kills_succeed",
    "rounds_survived",
    "utility_on_death_avg",
    "flash_assists",
]

NUMERIC_COLUMNS = [
    "won",
    "rounds",
    "kills",
    "deaths",
    "assists",
    "adr",
    "headshots",
    "damage",
    "first_kills",
    "entry_count",
    "entry_wins",
    "sniper_kills",
    "flash_count",
    "flash_successes",
    "enemies_flashed",
    "utility_count",
    "utility_damage",
    "utility_enemies",
    "clutch_kills",
    "clutch_1v1_count",
    "clutch_1v1_wins",
    "clutch_1v2_count",
    "clutch_1v2_wins",
    "pistol_kills",
    "mvps",
    "double_kills",
    "triple_kills",
    "quadro_kills",
    "penta_kills",
    "traded_deaths",
    "untraded_deaths",
    "first_half_score",
    "second_half_score",
    "overtime_score",
    "score_for",
    "score_against",
    "roster_count",
    "leetify_rating",
    "ct_leetify_rating",
    "t_leetify_rating",
    "preaim",
    "reaction_time_ms",
    "counter_strafe_good",
    "counter_strafe_all",
    "spray_accuracy",
    "trade_kill_opp",
    "trade_kills_succeed",
    "rounds_survived",
    "utility_on_death_avg",
    "flash_assists",
]

_SNAPSHOT_CACHE = {}


def frame(rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df["source"] = df["source"].fillna("faceit").astype(str).str.lower()
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def read_snapshot(path=None):
    target = Path(path) if path is not None else Path(SNAPSHOT_PATH)
    if not target.exists():
        return []
    stat = target.stat()
    resolved = str(target.resolve())
    cache_key = (resolved, stat.st_mtime_ns, stat.st_size)
    if cache_key not in _SNAPSHOT_CACHE:
        for stale_key in [k for k in _SNAPSHOT_CACHE if k[0] == resolved]:
            _SNAPSHOT_CACHE.pop(stale_key, None)
        _SNAPSHOT_CACHE[cache_key] = pd.read_csv(target).to_dict("records")
    return [dict(r) for r in _SNAPSHOT_CACHE[cache_key]]


def using_snapshot(db_path=None, snapshot_path=None):
    db_file = Path(db_path) if db_path is not None else Path(DB_PATH)
    snap_file = Path(snapshot_path) if snapshot_path is not None else Path(SNAPSHOT_PATH)
    return not db_available(db_file) and snap_file.exists()


def load_data(row_loader=None, snapshot_check=None, db_path=None, snapshot_path=None):
    if snapshot_check is not None:
        is_snap = snapshot_check()
    elif db_path is not None or snapshot_path is not None:
        is_snap = using_snapshot(db_path=db_path, snapshot_path=snapshot_path)
    else:
        is_snap = using_snapshot()

    if is_snap:
        return frame(read_snapshot(snapshot_path))

    if row_loader is not None:
        return frame(row_loader())
    if db_path is not None:
        return frame(read_rows(db_path))
    return frame(read_rows())


def team_maps(df):
    return df.drop_duplicates(["match_id", "map_no", "team_id"])


def summary(df):
    games = team_maps(df)
    known = games.dropna(subset=["won"])
    has_demo = 0.0
    if len(games) and not df.empty:
        row_demo_ok = (
            df["untraded_deaths"].notna()
            & df["traded_deaths"].notna()
            & (df["coverage"].fillna("Statistiche match") == "Statistiche match + trade demo")
        )
        map_demo_ok = row_demo_ok.groupby(
            [df["match_id"], df["map_no"], df["team_id"]], sort=False
        ).all()
        has_demo = float(map_demo_ok.mean() * 100) if len(map_demo_ok) else 0.0
    return {
        "games": len(games),
        "winrate": known["won"].mean() * 100 if len(known) else None,
        "wins": int(known["won"].sum()) if len(known) else 0,
        "rounds": int(games["rounds"].sum()),
        "demo_coverage": has_demo,
    }


def _paired_ratio(rows, num_col, den_col, scale=1.0):
    valid = rows.dropna(subset=[num_col, den_col])
    if valid.empty:
        return None
    den = valid[den_col].sum()
    if not den or den <= 0:
        return None
    return float(valid[num_col].sum() / den * scale)


def _paired_denominator(rows, num_col, den_col):
    valid = rows.dropna(subset=[num_col, den_col])
    if valid.empty:
        return None
    return int(valid[den_col].sum())


def _opening_deaths_pct(rows):
    valid = rows.dropna(subset=["entry_count", "entry_wins", "deaths"])
    if valid.empty:
        return None
    deaths = valid["deaths"].sum()
    if not deaths or deaths <= 0:
        return None
    opening_deaths = (valid["entry_count"] - valid["entry_wins"]).clip(lower=0).sum()
    return float(opening_deaths / deaths * 100.0)


def _weighted_avg(rows, val_col, weight_col, scale=1.0):
    valid = rows.dropna(subset=[val_col, weight_col])
    if valid.empty:
        return None
    w = valid[weight_col].sum()
    if not w or w <= 0:
        return None
    return float((valid[val_col] * valid[weight_col]).sum() / w * scale)


def player_summary(df):
    if df.empty:
        return pd.DataFrame()
    grouped = {name: rows for name, rows in df.groupby("nickname", sort=False)}
    ordered_names = [n for n in NAMES if n in grouped] + sorted(
        n for n in grouped if n not in NAMES
    )
    result = []
    for name in ordered_names:
        rows = grouped[name]
        adr_rows = rows.dropna(subset=["adr", "rounds"])
        adr_rounds = adr_rows["rounds"].sum()
        known_won = rows.dropna(subset=["won"])
        mk_cols = ["double_kills", "triple_kills", "quadro_kills", "penta_kills"]
        mk_rows = rows.dropna(subset=mk_cols, how="any")
        multikills = (
            int(mk_rows[mk_cols].sum().sum())
            if not mk_rows.empty and len(mk_rows) == len(rows)
            else None
        )
        result.append(
            {
                "Giocatore": name,
                "Mappe": len(rows),
                "K/D": _paired_ratio(rows, "kills", "deaths"),
                "KPR": _paired_ratio(rows, "kills", "rounds"),
                "ADR": (
                    float((adr_rows["adr"] * adr_rows["rounds"]).sum() / adr_rounds)
                    if adr_rounds
                    else None
                ),
                "DPR": _paired_ratio(rows, "deaths", "rounds"),
                "HS %": _paired_ratio(rows, "headshots", "kills", scale=100.0),
                "Morti senza trade %": _paired_ratio(
                    rows, "untraded_deaths", "deaths", scale=100.0
                ),
                "Tentativi apertura": _paired_denominator(rows, "entry_wins", "entry_count"),
                "Aperture %": _paired_ratio(
                    rows, "entry_wins", "entry_count", scale=100.0
                ),
                "Freq. aperture %": _paired_ratio(
                    rows, "entry_count", "rounds", scale=100.0
                ),
                "Morti in apertura %": _opening_deaths_pct(rows),
                "UDR": _paired_ratio(rows, "utility_damage", "rounds"),
                "Flash lanciate": _paired_denominator(rows, "flash_successes", "flash_count"),
                "Flash %": _paired_ratio(
                    rows, "flash_successes", "flash_count", scale=100.0
                ),
                "Nemici flash/r": _paired_ratio(rows, "enemies_flashed", "rounds"),
                "Sniper KPR": _paired_ratio(rows, "sniper_kills", "rounds"),
                "Sniper %": _paired_ratio(
                    rows, "sniper_kills", "kills", scale=100.0
                ),
                "Tentativi 1v1": _paired_denominator(rows, "clutch_1v1_wins", "clutch_1v1_count"),
                "1v1 %": _paired_ratio(
                    rows, "clutch_1v1_wins", "clutch_1v1_count", scale=100.0
                ),
                "Tentativi 1v2": _paired_denominator(rows, "clutch_1v2_wins", "clutch_1v2_count"),
                "1v2 %": _paired_ratio(
                    rows, "clutch_1v2_wins", "clutch_1v2_count", scale=100.0
                ),
                "Multikill": multikills,
                "Vittorie %": (
                    float(known_won["won"].mean() * 100) if len(known_won) else None
                ),
                "Leetify Rating": _weighted_avg(rows, "leetify_rating", "rounds", scale=100.0),
                "Rating T": _weighted_avg(rows, "t_leetify_rating", "rounds", scale=100.0),
                "Rating CT": _weighted_avg(rows, "ct_leetify_rating", "rounds", scale=100.0),
                "Preaim (°)": _weighted_avg(rows, "preaim", "rounds"),
                "TTD (ms)": _weighted_avg(rows, "reaction_time_ms", "rounds"),
                "Counter-strafe %": _paired_ratio(
                    rows, "counter_strafe_good", "counter_strafe_all", scale=100.0
                ),
                "Spray %": (
                    _weighted_avg(
                        rows.assign(
                            _spray=rows["spray_accuracy"].apply(
                                lambda v: v * 100.0 if pd.notna(v) and v <= 1.0 else v
                            )
                        ),
                        "_spray",
                        "rounds",
                    )
                ),
                "Trade kill %": _paired_ratio(
                    rows, "trade_kills_succeed", "trade_kill_opp", scale=100.0
                ),
                "Sopravvivenza %": _paired_ratio(
                    rows, "rounds_survived", "rounds", scale=100.0
                ),
                "Flash assist": (
                    int(rows["flash_assists"].dropna().sum())
                    if not rows["flash_assists"].dropna().empty
                    else None
                ),
                "Util. persa / morte": _weighted_avg(rows, "utility_on_death_avg", "deaths"),
            }
        )
    return pd.DataFrame(result)


def map_summary(df):
    games = team_maps(df)
    if games.empty:
        return pd.DataFrame(
            columns=[
                "Mappa",
                "Mappe",
                "Vittorie %",
                "Differenza round",
                "Round 1° half",
                "Round 2° half",
                "Round",
            ]
        )
    rows = []
    for game_map, group in games.groupby("map"):
        known = group.dropna(subset=["won"])
        fh = group["first_half_score"].dropna()
        sh = group["second_half_score"].dropna()
        rows.append(
            {
                "Mappa": str(game_map).title(),
                "Mappe": len(group),
                "Vittorie %": float(known["won"].mean() * 100) if len(known) else None,
                "Differenza round": (group["score_for"] - group["score_against"]).sum(
                    min_count=1
                ),
                "Round 1° half": float(fh.mean()) if len(fh) else None,
                "Round 2° half": float(sh.mean()) if len(sh) else None,
                "Round": group["rounds"].sum(min_count=1),
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(
        by=["Mappe", "Mappa"], ascending=[False, True], ignore_index=True
    )


def lineup_summary(df, min_members=3):
    """Riepiloga le combinazioni di membri del roster nella stessa squadra."""
    sub = df[df["roster_count"] >= min_members]
    if sub.empty:
        return pd.DataFrame(
            columns=["Formazione", "Membri", "Mappe", "Vittorie %", "Differenza round", "Round"]
        )
    records = []
    for (match_id, map_no, team_id), group in sub.groupby(
        ["match_id", "map_no", "team_id"], sort=False
    ):
        names = sorted(
            group["nickname"].unique(),
            key=lambda n: (NAMES.index(n) if n in NAMES else len(NAMES), n),
        )
        first = group.iloc[0]
        records.append(
            {
                "Formazione": " · ".join(names),
                "Membri": int(first["roster_count"]),
                "won": first["won"],
                "diff": (
                    first["score_for"] - first["score_against"]
                    if pd.notna(first["score_for"]) and pd.notna(first["score_against"])
                    else None
                ),
                "rounds": first["rounds"],
            }
        )
    linedf = pd.DataFrame(records)
    rows = []
    for (lineup, members), grp in linedf.groupby(["Formazione", "Membri"], sort=False):
        known = grp.dropna(subset=["won"])
        rows.append(
            {
                "Formazione": lineup,
                "Membri": int(members),
                "Mappe": len(grp),
                "Vittorie %": float(known["won"].mean() * 100) if len(known) else None,
                "Differenza round": grp["diff"].sum(min_count=1),
                "Round": grp["rounds"].sum(min_count=1),
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(
        by=["Mappe", "Membri", "Formazione"],
        ascending=[False, False, True],
        ignore_index=True,
    )


DEMO_EVENT_COLUMNS = [
    "match_id",
    "map_no",
    "round_no",
    "event_index",
    "tick",
    "event_type",
    "map",
    "date",
    "victim_id",
    "victim_name",
    "victim_team",
    "victim_side",
    "attacker_id",
    "attacker_name",
    "attacker_team",
    "attacker_side",
    "time_sec",
    "weapon",
    "headshot",
    "is_opening",
    "is_traded",
    "victim_x",
    "victim_y",
    "victim_z",
    "attacker_x",
    "attacker_y",
    "attacker_z",
]

DEMO_ROUND_COLUMNS = [
    "match_id",
    "map_no",
    "round_no",
    "map",
    "date",
    "team_id",
    "roster_count",
    "side",
    "won",
    "win_reason",
    "first_kill",
    "first_death",
    "plant_site",
    "post_plant_won",
    "retake_attempted",
    "retake_won",
    "equip_value",
    "buy_type",
]


def load_demo_events(db_path=None, snapshot_path=None):
    is_snap = using_snapshot(db_path=db_path, snapshot_path=SNAPSHOT_PATH)
    snap_target = Path(snapshot_path) if snapshot_path is not None else Path(DEMO_EVENTS_SNAPSHOT_PATH)
    if is_snap:
        rows = read_snapshot(snap_target)
    else:
        rows = read_demo_events(db_path if db_path is not None else DB_PATH)
    df = pd.DataFrame(rows, columns=DEMO_EVENT_COLUMNS)
    if not df.empty:
        for col in ("map_no", "round_no", "event_index", "tick", "time_sec", "is_opening", "is_traded", "victim_x", "victim_y", "victim_z", "attacker_x", "attacker_y", "attacker_z"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_demo_rounds(db_path=None, snapshot_path=None):
    is_snap = using_snapshot(db_path=db_path, snapshot_path=SNAPSHOT_PATH)
    snap_target = Path(snapshot_path) if snapshot_path is not None else Path(DEMO_ROUNDS_SNAPSHOT_PATH)
    if is_snap:
        rows = read_snapshot(snap_target)
    else:
        rows = read_demo_rounds(db_path if db_path is not None else DB_PATH)
    df = pd.DataFrame(rows, columns=DEMO_ROUND_COLUMNS)
    if not df.empty:
        for col in ("map_no", "round_no", "roster_count", "won", "first_kill", "first_death", "post_plant_won", "retake_attempted", "retake_won", "equip_value"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _filter_by_maps_df(demo_df, maps_df):
    if demo_df is None or demo_df.empty:
        return pd.DataFrame(columns=demo_df.columns if demo_df is not None else [])
    if maps_df is None or maps_df.empty:
        return demo_df.iloc[0:0].copy()
    valid_keys = set(zip(maps_df["match_id"].astype(str), maps_df["map_no"].astype(int)))
    if "team_id" in demo_df.columns and "team_id" in maps_df.columns:
        valid_team_keys = set(
            zip(
                maps_df["match_id"].astype(str),
                maps_df["map_no"].astype(int),
                maps_df["team_id"].astype(str),
            )
        )
        mask = [
            (
                (str(m), int(n), str(t)) in valid_team_keys
                if pd.notna(t) and str(t).strip() != ""
                else (str(m), int(n)) in valid_keys
            )
            for m, n, t in zip(
                demo_df["match_id"],
                demo_df["map_no"].fillna(-1),
                demo_df["team_id"],
            )
        ]
    else:
        mask = [
            (str(m), int(n)) in valid_keys
            for m, n in zip(demo_df["match_id"], demo_df["map_no"].fillna(-1))
        ]
    return demo_df.loc[mask].copy()


def demo_side_summary(rounds_df, maps_df=None):
    """
    Calcola il rendimento reale Lato T vs Lato CT dai round demo validati
    (mai stimato dai parziali 1°/2° half).
    """
    sub = _filter_by_maps_df(rounds_df, maps_df) if maps_df is not None else rounds_df
    cols = [
        "Mappa",
        "Mappe demo",
        "Round T",
        "Win T %",
        "Round CT",
        "Win CT %",
    ]
    if sub is None or sub.empty:
        return pd.DataFrame(columns=cols)

    sub = sub.dropna(subset=["map", "side", "won"]).copy()
    sub["side"] = sub["side"].astype(str).str.upper()
    sub = sub[sub["side"].isin(["T", "CT"])]
    if sub.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for map_name, grp in sub.groupby("map", sort=False):
        t_rows = grp[grp["side"] == "T"]
        ct_rows = grp[grp["side"] == "CT"]
        map_count = len(grp.drop_duplicates(["match_id", "map_no"]))
        rows.append({
            "Mappa": map_name,
            "Mappe demo": int(map_count),
            "Round T": int(len(t_rows)),
            "Win T %": float(t_rows["won"].mean() * 100) if len(t_rows) else None,
            "Round CT": int(len(ct_rows)),
            "Win CT %": float(ct_rows["won"].mean() * 100) if len(ct_rows) else None,
        })
    return pd.DataFrame(rows, columns=cols).sort_values(
        by=["Mappe demo", "Mappa"], ascending=[False, True], ignore_index=True
    )


def demo_tactical_summary(rounds_df, maps_df=None):
    """
    Calcola conversioni 5v4 / recuperi 4v5 e post-plant / retake per sito A/B
    con denominatori espliciti su round demo validati.
    """
    sub = _filter_by_maps_df(rounds_df, maps_df) if maps_df is not None else rounds_df
    cols = [
        "Mappa",
        "Round 5v4",
        "Conversione 5v4 %",
        "Round 4v5",
        "Recupero 4v5 %",
        "Plant A",
        "Post-plant A %",
        "Plant B",
        "Post-plant B %",
        "Retake tentati",
        "Retake %",
    ]
    if sub is None or sub.empty:
        return pd.DataFrame(columns=cols)

    sub = sub.dropna(subset=["map", "won"]).copy()
    if sub.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for map_name, grp in sub.groupby("map", sort=False):
        fk = grp[grp["first_kill"] == 1]
        fd = grp[grp["first_death"] == 1]

        t_grp = grp[grp["side"].astype(str).str.upper() == "T"]
        plant_a = t_grp[t_grp["plant_site"].astype(str).str.upper() == "A"]
        plant_b = t_grp[t_grp["plant_site"].astype(str).str.upper() == "B"]

        retakes = grp[grp["retake_attempted"] == 1].dropna(subset=["retake_won"])

        rows.append({
            "Mappa": map_name,
            "Round 5v4": int(len(fk)),
            "Conversione 5v4 %": float(fk["won"].mean() * 100) if len(fk) else None,
            "Round 4v5": int(len(fd)),
            "Recupero 4v5 %": float(fd["won"].mean() * 100) if len(fd) else None,
            "Plant A": int(len(plant_a)),
            "Post-plant A %": float(plant_a["won"].mean() * 100) if len(plant_a) else None,
            "Plant B": int(len(plant_b)),
            "Post-plant B %": float(plant_b["won"].mean() * 100) if len(plant_b) else None,
            "Retake tentati": int(len(retakes)),
            "Retake %": float(retakes["retake_won"].mean() * 100) if len(retakes) else None,
        })
    return pd.DataFrame(rows, columns=cols)


ALL_MAN_SITUATIONS = [
    "5v3", "5v2", "5v1",
    "4v3", "4v2", "4v1",
    "3v2", "3v1", "2v1",
    "4v4", "3v3", "2v2", "1v1",
    "3v5", "2v5", "1v5",
    "3v4", "2v4", "1v4",
    "2v3", "1v3", "1v2",
]
DEFAULT_MAN_SITUATIONS = ["5v3", "5v2", "5v1", "3v5", "2v5", "1v5"]


def demo_man_advantage_summary(events_df, rounds_df, maps_df=None, situations=None):
    """
    Ricostruisce dai round ed eventi demo tutte le situazioni numeriche XvY toccate
    (es. 5v3, 5v2, 5v1, 3v5, 2v5, 1v5, ecc., lasciando 5v4/4v5 alla tabella principale)
    e calcola Round, Vinti, Persi e Vittorie %.
    """
    cols = ["Situazione", "Round", "Vinti", "Persi", "Vittorie %"]
    target = list(DEFAULT_MAN_SITUATIONS if situations is None else situations)
    if not target or events_df is None or events_df.empty or rounds_df is None or rounds_df.empty:
        return pd.DataFrame(columns=cols)

    ev_sub = _filter_by_maps_df(events_df, maps_df) if maps_df is not None else events_df
    rd_sub = _filter_by_maps_df(rounds_df, maps_df) if maps_df is not None else rounds_df
    if ev_sub.empty or rd_sub.empty:
        return pd.DataFrame(columns=cols)

    rd_valid = rd_sub.dropna(subset=["match_id", "map_no", "round_no", "side", "won"]).copy()
    rd_valid["side"] = rd_valid["side"].astype(str).str.upper()
    rd_valid = rd_valid[rd_valid["side"].isin(["T", "CT"])]
    if rd_valid.empty:
        return pd.DataFrame(columns=cols)

    if "event_type" in ev_sub.columns:
        ev_sub = ev_sub[ev_sub["event_type"].fillna("player_death") == "player_death"]
    ev_sorted = ev_sub.sort_values(
        [c for c in ("match_id", "map_no", "round_no", "tick", "event_index") if c in ev_sub.columns],
        kind="stable",
    )

    events_by_round = {}
    for row in ev_sorted.to_dict("records"):
        m_id = row.get("match_id")
        m_no = row.get("map_no")
        r_no = row.get("round_no")
        if m_id is None or pd.isna(m_no) or pd.isna(r_no):
            continue
        events_by_round.setdefault((str(m_id), int(m_no), int(r_no)), []).append(row)

    stats = {label: {"rounds": 0, "wins": 0} for label in ALL_MAN_SITUATIONS}
    excluded = {(5, 5), (5, 4), (4, 5)}

    for r_row in rd_valid.to_dict("records"):
        key = (str(r_row["match_id"]), int(r_row["map_no"]), int(r_row["round_no"]))
        r_events = events_by_round.get(key)
        if not r_events:
            continue

        our_team = str(r_row.get("team_id") or "").strip()
        our_side = str(r_row["side"]).upper()
        won = 1 if int(r_row["won"]) == 1 else 0

        our_alive = 5
        opp_alive = 5
        our_dead = set()
        opp_dead = set()
        seen = set()

        idx = 0
        n_ev = len(r_events)
        while idx < n_ev and our_alive > 0 and opp_alive > 0:
            cur_tick = r_events[idx].get("tick")
            while idx < n_ev and r_events[idx].get("tick") == cur_tick:
                ev = r_events[idx]
                idx += 1
                v_id = str(ev.get("victim_id") or "").strip()
                v_team = str(ev.get("victim_team") or "").strip()
                v_side = str(ev.get("victim_side") or "").strip().upper()

                if our_team and v_team and v_team.lower() not in {"world", "nan", "none"}:
                    is_ours = v_team == our_team
                elif v_side in {"T", "CT"}:
                    is_ours = v_side == our_side
                else:
                    continue

                if is_ours:
                    if v_id and v_id in our_dead:
                        continue
                    if v_id:
                        our_dead.add(v_id)
                    our_alive = max(0, our_alive - 1)
                else:
                    if v_id and v_id in opp_dead:
                        continue
                    if v_id:
                        opp_dead.add(v_id)
                    opp_alive = max(0, opp_alive - 1)

            if our_alive >= 1 and opp_alive >= 1 and (our_alive, opp_alive) not in excluded:
                seen.add(f"{our_alive}v{opp_alive}")

        for label in seen:
            if label in stats:
                stats[label]["rounds"] += 1
                stats[label]["wins"] += won

    rows = []
    for label in target:
        if label not in stats:
            continue
        rnds = stats[label]["rounds"]
        wins = stats[label]["wins"]
        rows.append({
            "Situazione": label,
            "Round": rnds,
            "Vinti": wins,
            "Persi": rnds - wins,
            "Vittorie %": (wins / rnds * 100.0) if rnds else None,
        })
    return pd.DataFrame(rows, columns=cols)


def demo_economy_summary(rounds_df, maps_df=None):
    """Riepiloga i round e la percentuale di vittoria per fascia economica (Pistol, Eco, Force, Full Buy)."""
    sub = _filter_by_maps_df(rounds_df, maps_df) if maps_df is not None else rounds_df
    cols = ["Economia", "Lato", "Round", "Vittorie %", "Valore medio equip."]
    if sub is None or sub.empty:
        return pd.DataFrame(columns=cols)

    sub = sub.dropna(subset=["buy_type", "won"]).copy()
    sub = sub[sub["buy_type"].astype(str).str.strip() != ""]
    if sub.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for (buy, side), grp in sub.groupby(["buy_type", "side"], dropna=False, sort=False):
        eq = grp["equip_value"].dropna()
        rows.append({
            "Economia": str(buy),
            "Lato": str(side).upper() if pd.notna(side) else "Tutti",
            "Round": int(len(grp)),
            "Vittorie %": float(grp["won"].mean() * 100) if len(grp) else None,
            "Valore medio equip.": round(float(eq.mean()), 0) if len(eq) else None,
        })
    return pd.DataFrame(rows, columns=cols)


def prepare_heatmap_events(events_df, maps_df=None, mode="victim", include_opponents=False):
    """
    Filtra gli eventi demo sulle mappe selezionate (e per impostazione predefinita
    sui membri del roster presenti in `maps_df` per il ruolo richiesto da `mode`)
    e calcola le coordinate radar [0..1024] per vittima e attaccante tramite la calibrazione CS2.
    """
    from projectcs.demos import world_to_radar

    sub = _filter_by_maps_df(events_df, maps_df) if maps_df is not None else events_df
    if sub is None or sub.empty:
        return pd.DataFrame(
            columns=DEMO_EVENT_COLUMNS + ["victim_rx", "victim_ry", "attacker_rx", "attacker_ry"]
        )

    if maps_df is not None and not include_opponents:
        valid_pids = (
            set(
                zip(
                    maps_df["match_id"].astype(str),
                    maps_df["map_no"].astype(int),
                    maps_df["player_id"].astype(str),
                )
            )
            if "player_id" in maps_df.columns
            else set()
        )
        valid_nicks = (
            set(
                zip(
                    maps_df["match_id"].astype(str),
                    maps_df["map_no"].astype(int),
                    maps_df["nickname"].astype(str),
                )
            )
            if "nickname" in maps_df.columns
            else set()
        )
        if valid_pids or valid_nicks:
            id_col = "attacker_id" if mode == "attacker" else "victim_id"
            name_col = "attacker_name" if mode == "attacker" else "victim_name"
            roster_mask = [
                ((str(m), int(n), str(pid)) in valid_pids)
                or ((str(m), int(n), str(pname)) in valid_nicks)
                for m, n, pid, pname in zip(
                    sub["match_id"],
                    sub["map_no"].fillna(-1),
                    sub[id_col].fillna(""),
                    sub[name_col].fillna(""),
                )
            ]
            sub = sub.loc[roster_mask].copy()
            if sub.empty:
                return pd.DataFrame(
                    columns=DEMO_EVENT_COLUMNS + ["victim_rx", "victim_ry", "attacker_rx", "attacker_ry"]
                )

    v_coords = [
        world_to_radar(vx, vy, m)
        for vx, vy, m in zip(sub["victim_x"], sub["victim_y"], sub["map"])
    ]
    a_coords = [
        world_to_radar(ax, ay, m)
        for ax, ay, m in zip(sub["attacker_x"], sub["attacker_y"], sub["map"])
    ]
    sub = sub.copy()
    sub["victim_rx"] = [c[0] for c in v_coords]
    sub["victim_ry"] = [c[1] for c in v_coords]
    sub["attacker_rx"] = [c[0] for c in a_coords]
    sub["attacker_ry"] = [c[1] for c in a_coords]
    return sub

