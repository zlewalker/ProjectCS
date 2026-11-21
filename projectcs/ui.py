"""Presentazione e grafici. Nessuna modifica alle metriche o ai dati salvati."""
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from projectcs.data import player_summary, team_maps

ACCENT = "#ff762e"
SURFACE = "#191919"
TEXT = "#f4f4f4"
MUTED = "#c2c2c2"
CHART_CONFIG = {"displaylogo": False, "scrollZoom": False,
                "modeBarButtonsToRemove": ["select2d", "lasso2d"]}
RADAR_CHART_CONFIG = {"displaylogo": False, "scrollZoom": True,
                      "modeBarButtonsToRemove": ["select2d", "lasso2d"]}

VIEWS = {
    "Sintesi": ["Giocatore", "Mappe", "ADR", "K/D", "KPR", "DPR", "HS %", "Vittorie %"],
    "Aperture": ["Giocatore", "Mappe", "Tentativi apertura", "Freq. aperture %",
                 "Aperture %", "Morti in apertura %"],
    "AWP e trade": ["Giocatore", "Mappe", "Sniper KPR", "Sniper %", "Multikill",
                    "Morti senza trade %"],
    "Utility": ["Giocatore", "Mappe", "UDR", "Flash lanciate", "Flash %", "Nemici flash/r"],
    "Clutch": ["Giocatore", "Mappe", "Tentativi 1v1", "1v1 %",
               "Tentativi 1v2", "1v2 %", "Multikill"],
}
OLD_VIEWS = {"base": "Sintesi", "entry_awp": "AWP e trade", "support_clutch": "Utility"}

def style_chart(fig, height=410):
    fig.update_layout(
        template="plotly_dark", height=height,
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="Arial, sans-serif", size=15, color=TEXT),
        margin=dict(l=24, r=30, t=32, b=55),
        colorway=[ACCENT, "#78cde8", "#b7a4f5", "#77d49a"],
        hoverlabel=dict(bgcolor="#303030", bordercolor="#777777",
                        font=dict(size=15, color=TEXT)),
        modebar=dict(bgcolor=SURFACE, color=MUTED),
        legend_title_text="")
    fig.update_xaxes(gridcolor="#363636", zeroline=False, automargin=True,
                     tickfont=dict(size=14, color=MUTED), title_font=dict(size=14))
    fig.update_yaxes(gridcolor="#363636", zeroline=False, automargin=True,
                     tickfont=dict(size=15, color=TEXT), title_font=dict(size=14))
    return fig

def build_map_chart(maps):
    chart = maps.iloc[::-1]
    values = chart["Vittorie %"].tolist()
    fig = go.Figure(go.Bar(
        x=values, y=chart["Mappa"].tolist(), orientation="h",
        marker=dict(color=ACCENT, line=dict(width=0)),
        text=[f"{value:.0f}%" if pd.notna(value) else "—" for value in values],
        textposition=["inside" if pd.notna(value) and value >= 22 else "outside" for value in values],
        insidetextfont=dict(color="#101010", size=17),
        outsidetextfont=dict(color=TEXT, size=17),
        cliponaxis=False,
        customdata=chart[["Mappe", "Round", "Differenza round"]].values,
        hovertemplate="<b>%{y}</b><br>Vittorie: %{x:.1f}%<br>%{customdata[0]} mappe · %{customdata[1]} round<br>Differenza round: %{customdata[2]:+}<extra></extra>"))
    fig = style_chart(fig, max(340, min(620, len(chart) * 55 + 95)))
    fig.update_layout(margin=dict(l=100, r=42, t=20, b=55), bargap=.35)
    fig.update_yaxes(tickmode="array", tickvals=chart["Mappa"].tolist(),
                     ticktext=[f"{row['Mappa']}<br><span style='font-size:12px;color:#c2c2c2'>{row['Mappe']} mappe</span>"
                               for row in chart.to_dict("records")])
    fig.update_xaxes(range=[0, 106], tickvals=[0, 25, 50, 75, 100],
                     ticksuffix="%", title_text="Vittorie")
    fig.add_vline(x=50, line_dash="dot", line_color="#999999", line_width=1)
    return fig

def trend_data(df):
    games = team_maps(df).sort_values(
        ["date", "match_id", "map_no", "team_id"], kind="stable").reset_index(drop=True).copy()
    games["Numero"] = range(1, len(games) + 1)
    games["Media mobile"] = games["won"].rolling(10, min_periods=1).mean() * 100
    games["Campione mobile"] = games["won"].rolling(10, min_periods=1).count()
    return games

def build_trend_chart(games, start=1, end=None, adaptive=False):
    if games.empty:
        return style_chart(go.Figure(), 490)
    end = len(games) if end is None else int(end)
    start = int(start)
    if not 1 <= start <= end <= len(games):
        raise ValueError("Intervallo di partite non valido")
    selected = games[(games["Numero"] >= start) & (games["Numero"] <= end)]
    fig = go.Figure(go.Scatter(
        x=games["Numero"], y=games["Media mobile"], mode="lines+markers",
        line=dict(color=ACCENT, width=3),
        marker=dict(size=10, color=ACCENT, line=dict(color="#191919", width=2)),
        connectgaps=False,
        customdata=[
            [row["date"].strftime("%d/%m/%Y %H:%M"), escape(str(row["map"]).title()),
             int(row["roster_count"]), int(row["Campione mobile"]),
             "Vittoria" if row["won"] == 1 else ("Sconfitta" if row["won"] == 0 else "Esito assente")]
            for row in games.to_dict("records")],
        hovertemplate="<b>Partita #%{x} · %{customdata[0]}</b><br>%{customdata[1]} · %{customdata[2]} membri · %{customdata[4]}<br><br><b>%{y:.1f}% vittorie</b> su %{customdata[3]} mappe<extra></extra>",
        name="Media ultime 10", showlegend=False))
    fig = style_chart(fig, 510)
    low, high = -5, 105
    valid = selected["Media mobile"].dropna()
    if adaptive and len(valid):
        low = max(0, min(40, int(valid.min() // 10) * 10 - 10))
        high = min(100, max(60, int(-(-valid.max() // 10)) * 10 + 10))
        if high - low < 40:
            low, high = max(0, low - 10), min(100, high + 10)
    count = min(8, end - start + 1)
    ticks = sorted({round(start + (end - start) * i / max(1, count - 1)) for i in range(count)})
    date_by_number = games.set_index("Numero")["date"]
    fig.update_xaxes(
        title_text="Partite in ordine cronologico",
        range=[start - .65, end + .65],
        tickmode="array", tickvals=ticks,
        ticktext=[f"#{number}<br>{date_by_number[number]:%d/%m}" for number in ticks],
        rangeslider=dict(visible=len(games) > 1, thickness=.12,
                         bgcolor="#242424", bordercolor="#555555", borderwidth=1,
                         range=[.35, len(games) + .65]),
        showgrid=False)
    fig.update_yaxes(range=[low, high], tickvals=list(range(0, 101, 10)),
                     ticksuffix="%", title_text="Vittorie · media ultime 10")
    fig.update_layout(margin=dict(l=65, r=35, t=38, b=92), hovermode="closest")
    fig.add_hline(y=50, line_dash="dot", line_color="#929292", line_width=1)
    last = selected.iloc[-1]
    if pd.notna(last["Media mobile"]):
        fig.add_annotation(x=end, y=last["Media mobile"],
                           text=f"<b>{last['Media mobile']:.0f}%</b>",
                           showarrow=False, xanchor="right", yshift=22,
                           bgcolor="#303030", borderpad=5,
                           font=dict(size=15, color=TEXT))
    return fig

def metric_value(value, suffix=""):
    return "—" if value is None or pd.isna(value) else f"{value:.1f}{suffix}"

def show_table(df):
    if df.empty:
        st.info("Nessun dato disponibile per questo campione.")
        return
    table = df.reset_index(drop=True)
    definitions = {
        "Mappe": ("Mappe", "%d", 75, "Numero di mappe nel campione."),
        "Membri": ("Membri", "%d", 80, "Membri del roster nella stessa squadra."),
        "K/D": ("K/D", "%.2f", 80, "Kill totali / morti totali."),
        "KPR": ("KPR", "%.2f", 85, "Kill per round."),
        "ADR": ("ADR", "%.1f", 85, "Danno medio per round, pesato sui round disponibili."),
        "DPR": ("DPR", "%.2f", 85, "Morti per round. Da leggere nel contesto del ruolo."),
        "HS %": ("HS %", "%.1f%%", 90, "Percentuale di kill con colpo alla testa."),
        "Vittorie %": ("Win %", "%.1f%%", 95, "Vittorie nel campione disponibile."),
        "Tentativi apertura": ("Duelli", "%d", 90, "Tentativi di apertura: denominatore di Entry win %."),
        "Aperture %": ("Entry win %", "%.1f%%", 125, "Aperture vinte / tentativi."),
        "Freq. aperture %": ("Entry / round", "%.1f%%", 135, "Tentativi di apertura / round giocati."),
        "Morti in apertura %": ("Morti entry %", "%.1f%%", 140, "Morti nel primo duello / morti totali."),
        "Morti senza trade %": ("No trade %", "%.1f%%", 125, "Morti non scambiate entro 5s. Richiede demo completa."),
        "UDR": ("Danno util. / r", "%.1f", 135, "Danno da utility per round."),
        "Flash lanciate": ("Flash", "%d", 85, "Flash lanciate nel campione coperto."),
        "Flash %": ("Flash utili %", "%.1f%%", 130, "Flash efficaci / flash lanciate."),
        "Nemici flash/r": ("Nemici flash / r", "%.2f", 150, "Nemici accecati per round."),
        "Sniper KPR": ("Sniper / r", "%.2f", 125, "Kill con fucili di precisione per round."),
        "Sniper %": ("Sniper %", "%.1f%%", 115, "Kill con fucili di precisione / kill totali."),
        "Multikill": ("Round 2K+", "%d", 125, "Round con almeno due kill, su dati completi."),
        "Tentativi 1v1": ("1v1 giocati", "%d", 125, "Tentativi 1v1 nel campione coperto."),
        "1v1 %": ("1v1 vinti %", "%.1f%%", 125, "Clutch 1v1 vinti / tentati."),
        "Tentativi 1v2": ("1v2 giocati", "%d", 125, "Tentativi 1v2 nel campione coperto."),
        "1v2 %": ("1v2 vinti %", "%.1f%%", 125, "Clutch 1v2 vinti / tentati."),
        "Differenza round": ("Δ round", "%+d", 100, "Round vinti meno round persi."),
        "Round": ("Round", "%d", 90, "Round giocati."),
        "Round 1° half": ("1° tempo", "%.1f", 110, "Round vinti nel primo tempo; non indica il lato T/CT."),
        "Round 2° half": ("2° tempo", "%.1f", 110, "Round vinti nel secondo tempo; non indica il lato T/CT."),
        "Mappe demo": ("Mappe demo", "%d", 105, "Mappe con analisi demo completa."),
        "Round T": ("Round T", "%d", 95, "Round giocati dal lato T sulle mappe con demo."),
        "Win T %": ("Win T %", "%.1f%%", 105, "Percentuale di round vinti dal lato T."),
        "Round CT": ("Round CT", "%d", 95, "Round giocati dal lato CT sulle mappe con demo."),
        "Win CT %": ("Win CT %", "%.1f%%", 105, "Percentuale di round vinti dal lato CT."),
        "Round 5v4": ("Round 5v4", "%d", 100, "Round in cui la squadra ottiene la prima kill."),
        "Conversione 5v4 %": ("Conv. 5v4 %", "%.1f%%", 120, "Round vinti dopo aver ottenuto la prima kill."),
        "Round 4v5": ("Round 4v5", "%d", 100, "Round in cui la squadra subisce la prima morte."),
        "Recupero 4v5 %": ("Rec. 4v5 %", "%.1f%%", 120, "Round recuperati e vinti dopo aver subito la prima morte."),
        "Plant A": ("Plant A", "%d", 90, "Piazzamenti bomba sul sito A (lato T)."),
        "Post-plant A %": ("Post-plant A %", "%.1f%%", 125, "Round vinti da T dopo plant sul sito A."),
        "Plant B": ("Plant B", "%d", 90, "Piazzamenti bomba sul sito B (lato T)."),
        "Post-plant B %": ("Post-plant B %", "%.1f%%", 125, "Round vinti da T dopo plant sul sito B."),
        "Retake tentati": ("Retake", "%d", 90, "Retake tentati dal lato CT."),
        "Retake %": ("Retake %", "%.1f%%", 105, "Retake vinti dal lato CT / tentati."),
        "Vinti": ("Vinti", "%d", 85, "Round vinti nella situazione selezionata."),
        "Persi": ("Persi", "%d", 85, "Round persi nella situazione selezionata."),
        "Valore medio equip.": ("Equip. medio $", "$%.0f", 125, "Valore medio equipaggiamento di squadra a inizio round."),
    }
    config = {
        key: st.column_config.NumberColumn(label, format=fmt, width=width, help=help_text)
        for key, (label, fmt, width, help_text) in definitions.items() if key in table
    }
    for key in ["Giocatore", "Mappa", "Data", "Esito", "Punteggio", "Situazione", "Circuito"]:
        if key in table:
            config[key] = st.column_config.TextColumn(key, width=155 if key == "Giocatore" else 110)
    if "Formazione" in table:
        config["Formazione"] = st.column_config.TextColumn("Formazione", width=440)
    if "Matchroom" in table:
        config["Matchroom"] = st.column_config.LinkColumn("FACEIT", display_text="Apri ↗", width=100)
    if "ID partita" in table:
        config["ID partita"] = st.column_config.TextColumn("ID partita", width=340)
    styled = table.style.format(na_rep="—").apply(
        lambda row: ["background-color: " + ("#1a1a1a" if row.name % 2 == 0 else "#252525") +
                     "; color: #f4f4f4"] * len(row), axis=1)
    for key in ["Giocatore", "Formazione"]:
        if key in table:
            styled = styled.set_properties(subset=[key], **{"font-weight": "bold", "color": "#ffb38c"})
    if "Esito" in table:
        styled = styled.map(lambda value: "color: #77d49a; font-weight: bold" if value == "Vittoria"
                            else ("color: #f08080; font-weight: bold" if value == "Sconfitta" else ""),
                            subset=["Esito"])
    st.dataframe(styled, hide_index=True, width="stretch",
                 height="content" if len(table) <= 12 else 600, row_height=42,
                 column_config=config)

def show_players(df, compact=False, view="Sintesi"):
    table = player_summary(df)
    if table.empty:
        st.info("Nessun giocatore in questo campione.")
        return
    name = OLD_VIEWS.get(view, view)
    columns = VIEWS.get(name, VIEWS["Sintesi"])
    if compact:
        columns = ["Mappe", "ADR", "K/D", "DPR", "Vittorie %"]
    show_table(table[[column for column in columns if column in table]])

def player_view(df, key):
    selected = st.radio("Area di analisi", list(VIEWS), horizontal=True,
                        key=key, label_visibility="collapsed")
    show_players(df, view=selected)

def show_records(records, kind="review"):
    for row in records:
        with st.container(border=True):
            if kind == "objective":
                st.markdown('<div class="pcs-card-title">' + escape(str(row.get("title") or "Obiettivo")) + '</div>',
                            unsafe_allow_html=True)
                st.caption(str(row.get("status") or "Da allenare"))
                if row.get("action"):
                    st.write(row["action"])
                if row.get("metric"):
                    st.markdown("**Come verificarlo**")
                    st.write(row["metric"])
            else:
                st.markdown('<div class="pcs-card-title">' + escape(str(row.get("kind") or "Osservazione")) + '</div>',
                            unsafe_allow_html=True)
                context = [str(row.get("category") or ""), str(row.get("status") or "")]
                if row.get("round_no"):
                    context.append("Round " + str(row["round_no"]))
                st.caption(" · ".join(value for value in context if value))
                if row.get("observation"):
                    st.write(row["observation"])
                if row.get("action"):
                    st.markdown("**Azione concordata**")
                    st.write(row["action"])


def _load_radar_image(map_key):
    """Carica il radar CS2 ufficiale 1024x1024 da assets/radars/ se disponibile."""
    from projectcs.config import ROOT

    if not map_key:
        return None
    candidates = [
        ROOT / "assets" / "radars" / f"de_{map_key}_radar_psd.png",
        ROOT / "assets" / "radars" / f"{map_key}_radar_psd.png",
        ROOT / "assets" / "radars" / f"de_{map_key}.png",
    ]
    for path in candidates:
        if path.exists():
            try:
                from PIL import Image

                with Image.open(path) as im:
                    return im.convert("RGBA")
            except Exception:
                return None
    return None


def build_radar_heatmap_chart(events_df, map_name, mode="victim", show_vectors=False):
    """
    Costruisce una mappa tattica 2D / heatmap in coordinate radar CS2 [0..1024]x[0..1024],
    con immagine radar ufficiale CS2 in background, densità 2D, vettori opzionali e
    navigazione interattiva (zoom con rotella + pan clicca-e-trascina).
    """
    from projectcs.demos import CS2_RADAR_META, normalize_map_key

    map_key = normalize_map_key(map_name)
    meta = CS2_RADAR_META.get(map_key, {})
    radar_img = _load_radar_image(map_key)
    x_col = "victim_rx" if mode == "victim" else "attacker_rx"
    y_col = "victim_ry" if mode == "victim" else "attacker_ry"

    fig = go.Figure()
    valid = (
        events_df.dropna(subset=[x_col, y_col]).copy()
        if events_df is not None and not events_df.empty and x_col in events_df
        else pd.DataFrame()
    )

    if len(valid) >= 2:
        fig.add_trace(
            go.Histogram2dContour(
                x=valid[x_col],
                y=valid[y_col],
                autobinx=False,
                autobiny=False,
                xbins=dict(start=0, end=1024, size=24),
                ybins=dict(start=0, end=1024, size=24),
                colorscale=[
                    [0.0, "rgba(0,0,0,0)"],
                    [0.18, "rgba(255,140,0,0.22)"],
                    [0.45, "rgba(255,95,20,0.45)"],
                    [0.75, "rgba(255,65,0,0.68)"],
                    [1.0, "rgba(255,235,140,0.88)"],
                ],
                showscale=False,
                contours=dict(coloring="heatmap", showlines=False),
                ncontours=18,
                hoverinfo="skip",
                name="Densità",
            )
        )

    if show_vectors and not valid.empty and "attacker_rx" in valid and "victim_rx" in valid:
        vec_rows = valid.dropna(subset=["attacker_rx", "attacker_ry", "victim_rx", "victim_ry"])
        vx_lines, vy_lines = [], []
        for r in vec_rows.to_dict("records"):
            vx_lines.extend([r["attacker_rx"], r["victim_rx"], None])
            vy_lines.extend([r["attacker_ry"], r["victim_ry"], None])
        if vx_lines:
            fig.add_trace(
                go.Scatter(
                    x=vx_lines,
                    y=vy_lines,
                    mode="lines",
                    line=dict(color="rgba(120,205,232,0.55)", width=1.6, dash="dot"),
                    hoverinfo="skip",
                    name="Traiettoria duello",
                )
            )

    if not valid.empty:
        custom = []
        colors = []
        for r in valid.to_dict("records"):
            r_no = int(r["round_no"]) if pd.notna(r.get("round_no")) else "?"
            v_label = escape(str(r.get("victim_name") or r.get("victim_id") or "—"))
            a_label = escape(str(r.get("attacker_name") or r.get("attacker_id") or "—"))
            weapon = escape(str(r.get("weapon") or "arma non specificata"))
            traded_str = (
                "Scambiata (trade ≤5s)"
                if r.get("is_traded") == 1
                else ("Senza trade" if r.get("is_traded") == 0 else "—")
            )
            opening_str = " · Apertura round" if r.get("is_opening") == 1 else ""
            custom.append([r_no, v_label, a_label, weapon, traded_str + opening_str])
            colors.append("#77d49a" if r.get("is_traded") == 1 else ACCENT)

        fig.add_trace(
            go.Scatter(
                x=valid[x_col],
                y=valid[y_col],
                mode="markers",
                marker=dict(
                    size=10,
                    color=colors,
                    line=dict(color="#101010", width=1.5),
                    opacity=0.92,
                ),
                customdata=custom,
                hovertemplate=(
                    "<b>Round %{customdata[0]}</b><br>"
                    "Vittima: %{customdata[1]}<br>"
                    "Killer: %{customdata[2]} (%{customdata[3]})<br>"
                    "%{customdata[4]}<extra></extra>"
                ),
                name="Eventi",
            )
        )

    if radar_img is not None:
        fig.add_layout_image(
            dict(
                source=radar_img,
                xref="x",
                yref="y",
                x=0,
                y=0,
                sizex=1024,
                sizey=1024,
                sizing="stretch",
                opacity=0.92,
                layer="below",
            )
        )
    else:
        for z in meta.get("zones", []):
            fig.add_annotation(
                x=z["rx"],
                y=z["ry"],
                text=f"<b>{escape(z['name'])}</b>",
                showarrow=False,
                font=dict(size=12, color="#d8d8d8"),
                bgcolor="rgba(16,16,16,0.75)",
                bordercolor="#555555",
                borderwidth=1,
                borderpad=4,
            )

    fig = style_chart(fig, height=680)
    fig.update_xaxes(
        range=[0, 1024],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        title_text=f"Radar {str(map_name).title()} · spazio [0..1024]",
        constrain="domain",
    )
    fig.update_yaxes(
        range=[1024, 0],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        scaleanchor="x",
        scaleratio=1,
        title_text="",
    )
    fig.update_layout(
        margin=dict(l=16, r=16, t=20, b=40),
        showlegend=False,
        dragmode="pan",
        hovermode="closest",
        paper_bgcolor="#121417",
        plot_bgcolor="#121417",
    )
    return fig
