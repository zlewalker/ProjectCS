from pathlib import Path
import pandas as pd

from projectcs.config import DB_PATH, NAMES, ROOT
from projectcs.db import read_demo_events, read_demo_rounds, read_rows

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
]

_SNAPSHOT_CACHE = {}


def frame(rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def read_snapshot(path=None):
    target = Path(path) if path is not None else Path(SNAPSHOT_PATH)
    if not target.exists():
        return []
    stat = target.stat()
    cache_key = (str(target.resolve()), stat.st_mtime_ns, stat.st_size)
    if cache_key not in _SNAPSHOT_CACHE:
        _SNAPSHOT_CACHE[cache_key] = pd.read_csv(target).to_dict("records")
    return [dict(r) for r in _SNAPSHOT_CACHE[cache_key]]


def using_snapshot(db_path=None, snapshot_path=None):
    db_file = Path(db_path) if db_path is not None else Path(DB_PATH)
    snap_file = Path(snapshot_path) if snapshot_path is not None else Path(SNAPSHOT_PATH)
    return not db_file.exists() and snap_file.exists()


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
