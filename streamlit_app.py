import hashlib
import hmac
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Supporta anche Python portable con percorso del progetto esplicito.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from projectcs.config import NAMES, ROSTER
from projectcs.data import load_data, map_summary, player_summary, summary, team_maps, using_snapshot
from projectcs.db import (add_objective, add_review, read_records,
                          set_objective_status)

st.set_page_config(page_title="ProjectCS · Team intelligence", page_icon="◈", layout="wide")
st.markdown("""
<style>
.block-container {max-width:1480px;padding-top:2.2rem;padding-bottom:3rem}
h1 {letter-spacing:-1.6px;font-weight:750!important}
h2,h3 {letter-spacing:-.5px}
[data-testid="stMetric"] {background:#141c26;border:1px solid #25303d;border-radius:12px;padding:18px}
[data-testid="stMetricLabel"] {color:#aab6c5}
[data-testid="stMetricValue"] {color:#e8edf3}
[data-testid="stSidebar"] {border-right:1px solid #25303d}
.eyebrow {color:#c8f36b;letter-spacing:3px;font-size:12px;font-weight:700}
.hero-sub {color:#9baabe;font-size:16px;margin-top:-8px;margin-bottom:26px}
.section-label {color:#9baabe;font-size:12px;letter-spacing:2px;margin:20px 0 10px}
</style>
""", unsafe_allow_html=True)

def secret(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        return st.secrets.get(name, "")
    except FileNotFoundError:
        return ""

def owner_access():
    if using_snapshot():
        return False
    expected = secret("ADMIN_PASSWORD")
    if not expected:
        st.caption("Gestione disabilitata · configura ADMIN_PASSWORD nei secrets.")
        return False
    if st.session_state.get("owner_digest") == hashlib.sha256(expected.encode()).hexdigest():
        if st.button("Esci dalla gestione", width="stretch"):
            st.session_state.pop("owner_digest", None)
            st.rerun()
        return True
    with st.expander("Accesso proprietario"):
        with st.form("owner_login"):
            password = st.text_input("Password amministratore", type="password")
            submit = st.form_submit_button("Accedi")
        if submit:
            if time.time() < st.session_state.get("login_after", 0):
                st.error("Attendi alcuni secondi prima di riprovare.")
            elif hmac.compare_digest(password.encode(), expected.encode()):
                st.session_state["owner_digest"] = hashlib.sha256(expected.encode()).hexdigest()
                st.rerun()
            else:
                st.session_state["login_after"] = time.time() + 5
                st.error("Password non valida.")
    return False

def style_chart(fig):
    fig.update_layout(
        template='plotly_dark', height=390,
        paper_bgcolor='#121b27', plot_bgcolor='#121b27',
        font=dict(family='Arial, sans-serif', size=15, color='#edf3fc'),
        margin=dict(l=24, r=30, t=28, b=46),
        colorway=['#c8f36b', '#73d5f3', '#ac94e8', '#f3b66b', '#e67f88'],
        legend_title_text='',
        hoverlabel=dict(bgcolor='#25364b', bordercolor='#698098', font_size=15),
        modebar=dict(bgcolor='#121b27', color='#b6c7dc'))
    fig.update_xaxes(gridcolor='#314056', zeroline=False,
                     tickfont=dict(size=14, color='#e5edf8'), title_font=dict(size=14))
    fig.update_yaxes(gridcolor='#314056', zeroline=False,
                     tickfont=dict(size=15, color='#edf3fc'), title_font=dict(size=14))
    return fig

def metric_value(value, suffix=""):
    return "—" if value is None or pd.isna(value) else f"{value:.1f}{suffix}"

def show_table(df):
    numeric = {
        'Mappe': st.column_config.NumberColumn('Mappe', format='%d', width='small'),
        'K/D': st.column_config.NumberColumn('K/D', format='%.2f', help='Kill totali divise per morti totali.', width='small'),
        'KPR': st.column_config.NumberColumn('KPR', format='%.2f', help='Kill per round giocato.', width='small'),
        'ADR': st.column_config.NumberColumn('ADR', format='%.1f', help='Danno medio per round, pesato sui round disponibili.', width='small'),
        'DPR': st.column_config.NumberColumn('DPR', format='%.2f', help='Morti per round giocato. Da interpretare nel contesto del ruolo.', width='small'),
        'Vittorie %': st.column_config.NumberColumn('Win %', format='%.1f%%', help='Percentuale di vittorie nel campione.', width='small'),
        'Differenza round': st.column_config.NumberColumn('Δ round', format='%+d', width='small'),
        'Round': st.column_config.NumberColumn('Round', format='%d', width='small'),
        'Giocatore': st.column_config.TextColumn('Giocatore', width='medium'),
        'ID partita': st.column_config.TextColumn('ID partita', width='large'),
    }
    styled = df.style.apply(
        lambda row: ['background-color: ' + ('#131f2e' if row.name % 2 == 0 else '#1c2a3d') + '; color: #eff5ff'] * len(row),
        axis=1)
    if 'Giocatore' in df.columns:
        styled = styled.set_properties(subset=['Giocatore'], **{'font-weight': 'bold', 'color': '#c8f36b'})
    height = min(650, max(140, (len(df) + 1) * 46 + 8))
    st.dataframe(styled, hide_index=True, width='stretch', height=height,
                 row_height=44, column_config={key: value for key, value in numeric.items() if key in df.columns})

def show_players(df, compact=False):
    table = player_summary(df)
    if table.empty:
        st.info('Nessun giocatore in questo campione.')
        return
    table = table[['Giocatore', 'Mappe', 'K/D', 'KPR', 'ADR', 'DPR', 'Vittorie %']]
    if compact:
        table = table.drop(columns=['Giocatore'])
    show_table(table)

with st.sidebar:
    st.markdown("## ◈ ProjectCS")
    st.caption("TEAM INTELLIGENCE / PROTOTIPO 0.1")
    page = st.radio("Navigazione", ["Panoramica", "Giocatori", "Partite", "Revisione", "Allenamento"],
                    label_visibility="collapsed")
    st.divider()
    owner = owner_access()
    st.caption("Gestione attiva" if owner else "Consultazione")
    st.divider()
    st.caption("Obiettivo · ESEA Main e tornei")
    st.caption("Ancient / Anubis / Dust2")
    st.caption("Mirage · permaban attuale")

data = load_data()
st.markdown('<div class="eyebrow">PROJECTCS / PERFORMANCE LAB</div>', unsafe_allow_html=True)
st.title(page)
if data.empty and page not in {"Revisione", "Allenamento"}:
    st.info("L'archivio è vuoto. Le partite appariranno dopo il prossimo aggiornamento.")
    st.stop()

base = data.copy()
filtered = data.copy()
if not data.empty:
    with st.expander("Filtri del campione", expanded=True):
        c1, c2, c3 = st.columns([1.2, 1.5, 1.5])
        minimum = data["date"].min().date()
        maximum = data["date"].max().date()
        interval = c1.date_input("Periodo", value=(minimum, maximum), min_value=minimum, max_value=max(maximum, minimum + timedelta(days=1)))
        selected_maps = c2.multiselect("Mappe", sorted(data["map"].unique()), default=sorted(data["map"].unique()))
        composition = c3.selectbox("Composizione nella stessa squadra",
                                  ["Core · almeno 3", "Quintetto · 5", "Gruppo · 4", "Gruppo · 3", "Fuori dal core · 1–2", "Tutte"])
        if len(interval) == 2:
            base = data[(data["date"].dt.date >= interval[0]) & (data["date"].dt.date <= interval[1])]
        base = base[base["map"].isin(selected_maps)]
        filtered = base.copy()
        if composition == "Core · almeno 3":
            filtered = base[base["roster_count"] >= 3]
        elif composition == "Quintetto · 5":
            filtered = base[base["roster_count"] == 5]
        elif composition == "Gruppo · 4":
            filtered = base[base["roster_count"] == 4]
        elif composition == "Gruppo · 3":
            filtered = base[base["roster_count"] == 3]
        elif composition == "Fuori dal core · 1–2":
            filtered = base[base["roster_count"] <= 2]
    st.caption(f"{len(team_maps(filtered))} mappe-squadra · {filtered['match_id'].nunique()} partite uniche · {len(filtered)} presenze giocatore. Membri su squadre opposte sono separati.")

if page == "Panoramica":
    st.markdown('<div class="hero-sub">Dal risultato della partita alla prossima sessione di allenamento.</div>', unsafe_allow_html=True)
    if filtered.empty:
        st.info("Nessuna partita corrisponde ai filtri.")
        st.stop()
    totals = summary(filtered)
    a, b, c, d = st.columns(4)
    a.metric("Mappe nel campione", totals["games"])
    b.metric("Vittorie", metric_value(totals["winrate"], "%"))
    c.metric("Round osservati", totals["rounds"])
    d.metric("Copertura demo", "0%")
    st.caption("Un'unità del campione è una mappa giocata da una squadra con almeno un membro. I round sono conteggiati una volta per squadra.")
    left, right = st.columns([1.15, 1])
    maps = map_summary(filtered)
    with left, st.container(border=True):
        st.subheader("Map pool")
        fig = px.bar(maps, x='Vittorie %', y='Mappa', orientation='h',
                     text='Vittorie %', custom_data=['Mappe', 'Round', 'Differenza round'],
                     color_discrete_sequence=['#c8f36b'], range_x=[0, 100],
                     labels={'Vittorie %': 'Vittorie (%)', 'Mappa': ''})
        fig.update_traces(
            texttemplate='%{x:.0f}%', textposition='auto',
            textfont=dict(size=17, color='#0b1016'),
            marker_line_width=0,
            hovertemplate='<b>%{y}</b><br>Vittorie: %{x:.1f}%<br>Campione: %{customdata[0]} mappe<br>Round: %{customdata[1]}<br>Δ round: %{customdata[2]}<extra></extra>')
        fig.update_yaxes(tickmode='array', tickvals=maps['Mappa'].tolist(),
                         ticktext=[f"{row['Mappa']}  ·  {row['Mappe']}" for row in maps.to_dict('records')])
        fig.update_xaxes(tickvals=[0, 25, 50, 75, 100], ticksuffix='%')
        fig.add_vline(x=50, line_dash='dot', line_color='#8ca0b9', line_width=1)
        st.plotly_chart(style_chart(fig), width='stretch', theme=None, config={'displaylogo': False})
        st.caption("Accanto alla mappa: numero di mappe giocate. Linea tratteggiata: 50% di vittorie.")
    with right, st.container(border=True):
        st.subheader("Andamento")
        games = team_maps(filtered).sort_values("date").copy()
        games["Vittorie ultime 10 mappe %"] = games["won"].rolling(10, min_periods=1).mean() * 100
        games['Campione'] = games['won'].rolling(10, min_periods=1).count()
        fig = px.line(games, x='date', y='Vittorie ultime 10 mappe %', markers=True,
                      custom_data=['Campione', 'map', 'roster_count'],
                      color_discrete_sequence=['#73d5f3'], range_y=[0, 100],
                      labels={'date': '', 'Vittorie ultime 10 mappe %': 'Vittorie (%)'})
        fig.update_traces(
            line=dict(width=3), marker=dict(size=7, line=dict(width=1, color='#121b27')),
            fill='tozeroy', fillcolor='rgba(115,213,243,0.09)',
            hovertemplate='<b>%{x|%d/%m/%Y}</b><br>Vittorie: %{y:.1f}%<br>Finestra: %{customdata[0]} mappe<br>%{customdata[1]} · %{customdata[2]} membri<extra></extra>')
        fig.update_yaxes(tickvals=[0, 25, 50, 75, 100], ticksuffix='%')
        fig.update_xaxes(tickformat='%d/%m', nticks=6)
        fig.add_hline(y=50, line_dash='dot', line_color='#8ca0b9', line_width=1)
        st.plotly_chart(style_chart(fig), width='stretch', theme=None, config={'displaylogo': False})
        st.caption("Media mobile fino a 10 mappe; le prime osservazioni hanno un campione ridotto.")
    st.subheader("Segnali da approfondire")
    for row in maps.to_dict("records"):
        if row["Mappe"] < 10:
            st.caption(f"{row['Mappa']} · {row['Mappe']} mappe: campione piccolo, nessuna priorità automatica.")
        elif row["Vittorie %"] < 45:
            st.info(f"{row['Mappa']} · {row['Vittorie %']:.0f}% di vittorie su {row['Mappe']} mappe. Candidata alla revisione; il dato non identifica la causa.")
        else:
            st.caption(f"{row['Mappa']} · {row['Mappe']} mappe, {row['Vittorie %']:.0f}% di vittorie.")
    st.subheader("Contributi individuali")
    show_players(filtered)
    st.caption("ADR: danno medio per round · KPR: kill per round · DPR: morti per round. I rapporti sono calcolati sui conteggi disponibili.")
    with st.expander("Analisi previste dalle demo"):
        st.write("Aperture · trade a 5 secondi · conversione 5v4/4v5 · post-plant · retake · economia · utility.")
        st.info("Non ancora calcolate nel prototipo. Posizioni, movimenti e heatmap sono esclusi dalla v0.1.")

elif page == "Giocatori":
    selected = st.selectbox("Giocatore", NAMES)
    role = next(p["role"] for p in ROSTER if p["nickname"] == selected)
    st.caption(f"Ruolo provvisorio · {role}")
    own = filtered[filtered["nickname"] == selected]
    st.subheader("Prestazione nel campione selezionato")
    show_players(own)
    st.subheader("Dentro e fuori dal core")
    st.caption("Questo confronto mantiene periodo e mappe, ma ignora il filtro composizione per mostrare entrambi i gruppi.")
    player_base = base[base["nickname"] == selected]
    cols = st.columns(2)
    for col, label, group in [
        (cols[0], "Dentro · almeno 3 membri", player_base[player_base["roster_count"] >= 3]),
        (cols[1], "Fuori · solo o duo", player_base[player_base["roster_count"] <= 2]),
    ]:
        with col:
            st.markdown(f"#### {label}")
            show_players(group, compact=True)
    st.caption("Confronto descrittivo: non dimostra un effetto causale del gruppo. Ruoli, avversari ed economia possono differire.")
    if not own.empty:
        st.subheader("Dettaglio per mappa")
        show_table(map_summary(own).round(1))
    st.subheader("Roster")
    show_table(pd.DataFrame(ROSTER).rename(columns={"nickname": "Giocatore", "role": "Ruolo provvisorio"}))
    for member in ROSTER:
        st.link_button(member["nickname"] + " · FACEIT", "https://www.faceit.com/en/players/" + member["nickname"])

elif page == "Partite":
    games = team_maps(filtered).sort_values("date", ascending=False)
    if games.empty:
        st.info("Nessuna partita corrisponde ai filtri.")
        st.stop()
    display = games[["date", "map", "roster_count", "won", "score_for", "score_against", "coverage", "match_id"]].copy()
    display["date"] = display["date"].dt.strftime("%d/%m/%Y")
    display["won"] = display["won"].map({1: "Vittoria", 0: "Sconfitta"}).fillna("Non disponibile")
    display.columns = ["Data", "Mappa", "Membri", "Esito", "Round vinti", "Round persi", "Dati disponibili", "ID partita"]
    show_table(display)
    options = list(games.itertuples())
    chosen = st.selectbox("Apri una mappa-squadra", range(len(options)),
                          format_func=lambda i: f"{options[i].date:%d/%m} · {options[i].map.title()} · {options[i].roster_count} membri · {options[i].match_id}")
    game = options[chosen]
    detail = filtered[(filtered["match_id"] == game.match_id) & (filtered["map_no"] == game.map_no) & (filtered["team_id"] == game.team_id)]
    show_players(detail)
    st.link_button("Apri la matchroom FACEIT", f"https://www.faceit.com/en/cs2/room/{game.match_id}")
    st.download_button("Esporta il campione CSV", filtered.to_csv(index=False).encode("utf-8-sig"),
                       file_name="projectcs-campione.csv", mime="text/csv")
    st.caption("Per associare una nota a questa partita usa la sezione Revisione.")

elif page == "Revisione":
    st.write("Raccogli ciò che ha funzionato e ciò che va verificato. Una nota può riguardare l'intera partita oppure un round.")
    notes = read_records("reviews")
    if notes:
        show_table(pd.DataFrame(notes))
    else:
        st.info("Ancora nessuna nota. Puoi iniziare dal confronto a fine partita.")
    if owner:
        st.subheader("Nuova osservazione")
        with st.form("review"):
            ids = ["Nota generale"] + sorted(data["match_id"].unique().tolist()) if not data.empty else ["Nota generale"]
            match = st.selectbox("Partita", ids)
            round_no = st.number_input("Round · 0 se non specificato", min_value=0, max_value=200)
            c1, c2, c3 = st.columns(3)
            kind = c1.selectbox("Tipo", ["Da migliorare", "Positivo"])
            category = c2.selectbox("Categoria", ["Comunicazione", "Call", "Trade", "Utility", "Economia", "Altro"])
            status = c3.selectbox("Verifica", ["Da verificare", "Confermato in revisione"])
            observation = st.text_area("Cosa avete osservato?", max_chars=3000)
            action = st.text_area("Cosa provare nella prossima partita?", max_chars=2000)
            saved = st.form_submit_button("Salva osservazione")
        if saved:
            if not observation.strip():
                st.error("Inserisci un'osservazione.")
            else:
                add_review(None if match == "Nota generale" else match, round_no or None,
                           kind, category, observation.strip(), status, action.strip())
                st.rerun()
    else:
        st.caption("Solo il proprietario può aggiungere osservazioni.")

elif page == "Allenamento":
    st.write("Trasforma un'osservazione verificata in un obiettivo e controllalo nelle sessioni successive.")
    st.caption("Nessuna diagnosi automatica della comunicazione: le priorità vengono concordate attraverso la revisione.")
    goals = read_records("objectives")
    if goals:
        show_table(pd.DataFrame(goals))
    else:
        st.info("Nessun obiettivo impostato.")
    if owner and goals:
        goal = st.selectbox("Aggiorna obiettivo", goals, format_func=lambda r: f"#{r['id']} · {r['title']}")
        status = st.selectbox("Nuovo stato", ["Da allenare", "In corso", "Da verificare", "Completato"])
        if st.button("Aggiorna stato"):
            set_objective_status(goal["id"], status)
            st.rerun()
    if owner:
        with st.form("objective"):
            title = st.text_input("Obiettivo", max_chars=200)
            action = st.text_area("Azione concreta", max_chars=2000)
            verification = st.text_input("Come verifichiamo il progresso?", max_chars=500)
            submit = st.form_submit_button("Aggiungi obiettivo")
        if submit:
            if title.strip() and action.strip():
                add_objective(title.strip(), action.strip(), verification.strip())
                st.rerun()
            else:
                st.error("Inserisci titolo e azione.")

