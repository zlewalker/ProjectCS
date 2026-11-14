import hashlib
import hmac
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

# Supporta anche Python portable con percorso del progetto esplicito.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from projectcs.config import NAMES, PRIORITY_MAPS, ROSTER
from projectcs.data import (
    lineup_summary,
    load_data,
    map_summary,
    summary,
    team_maps,
    using_snapshot,
)
from projectcs import db as db_module
from projectcs.ui import (
    CHART_CONFIG, build_map_chart, build_trend_chart, metric_value,
    player_view, show_players, show_records, show_table, trend_data,
)

st.set_page_config(page_title="ProjectCS · Team intelligence", page_icon="◈", layout="wide")
st.markdown(
    "<style>" + (Path(__file__).resolve().parent / "assets" / "dashboard.css").read_text(encoding="utf-8") + "</style>",
    unsafe_allow_html=True,
)

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


with st.sidebar:
    st.markdown("## ◈ ProjectCS")
    st.caption("TEAM PERFORMANCE LAB")
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
descriptions = {
    "Panoramica": "Il rendimento del core, una mappa alla volta.",
    "Giocatori": "Contributi individuali, contesto del ruolo e confronto dentro/fuori.",
    "Partite": "Risultati, composizioni e dettagli delle mappe giocate.",
    "Revisione": "Cosa ripetere, cosa verificare e cosa migliorare.",
    "Allenamento": "Obiettivi concreti per la prossima sessione.",
}
st.caption(descriptions[page])
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
        all_maps = sorted(data["map"].unique())
        only_priority = c2.checkbox("Solo map pool del team", value=False, help="Ancient · Anubis · Dust2")
        default_maps = [m for m in all_maps if m in PRIORITY_MAPS] if only_priority else all_maps
        selected_maps = c2.multiselect("Mappe", all_maps, default=default_maps or all_maps)
        composition = c3.selectbox("Composizione nella stessa squadra",
                                  ["Core · almeno 3", "Quintetto · 5", "Gruppo · 4", "Gruppo · 3", "Fuori dal core · 1–2", "Tutte"])
        if len(interval) == 2:
            base = data[(data["date"].dt.date >= interval[0]) & (data["date"].dt.date <= interval[1])]
        elif len(interval) == 1:
            base = data[data["date"].dt.date >= interval[0]]
            c1.caption("Seleziona la data finale per chiudere l'intervallo.")
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
    if filtered.empty:
        st.info("Nessuna partita corrisponde ai filtri.")
        st.stop()
    totals = summary(filtered)
    a, b, c, d = st.columns(4)
    a.metric("Mappe nel campione", totals["games"])
    b.metric("Vittorie", metric_value(totals["winrate"], "%"))
    c.metric("Round osservati", totals["rounds"])
    map_rows = team_maps(filtered)
    round_delta = (map_rows["score_for"] - map_rows["score_against"]).sum(min_count=1)
    d.metric("Differenza round", "—" if pd.isna(round_delta) else f"{round_delta:+.0f}")
    st.caption("Un'unità del campione è una mappa giocata da una squadra con almeno un membro. I round sono conteggiati una volta per squadra.")
    maps = map_summary(filtered)
    with st.container(border=True):
        st.subheader("Map pool")
        st.caption("Vittorie per mappa · campione indicato accanto al nome")
        st.plotly_chart(build_map_chart(maps), width="stretch", theme=None, config=CHART_CONFIG)
        st.caption("La linea tratteggiata indica il 50% di vittorie. I risultati non sono corretti per forza degli avversari.")

    with st.container(border=True):
        st.subheader("Andamento del core")
        st.caption("Un punto per mappa, con spazio uniforme anche nelle sessioni più lunghe.")
        games = trend_data(filtered)
        count = len(games)
        if count > 1:
            start, end = st.slider(
                "Partite visibili", min_value=1, max_value=count,
                value=(max(1, count - 19), count),
                help="Sposta gli estremi per esplorare una parte dello storico. Le medie conservano il contesto delle partite precedenti.")
        else:
            start, end = 1, 1
        adaptive = st.toggle("Adatta la scala verticale", value=False,
                             help="Restringe la scala ai valori visibili mantenendo il riferimento del 50%.")
        st.plotly_chart(build_trend_chart(games, start, end, adaptive),
                        width="stretch", theme=None, config=CHART_CONFIG)
        st.caption(f"Partite {start}–{end} di {count} · media mobile sulle ultime 10 mappe disponibili. "
                   "La distanza orizzontale rappresenta l'ordine delle partite, non i giorni trascorsi.")
    st.subheader("Segnali da approfondire")
    for row in maps.to_dict("records"):
        if row["Mappe"] < 10:
            st.caption(f"{row['Mappa']} · {row['Mappe']} mappe: campione piccolo, nessuna priorità automatica.")
        elif row["Vittorie %"] < 45:
            st.info(f"{row['Mappa']} · {row['Vittorie %']:.0f}% di vittorie su {row['Mappe']} mappe. Candidata alla revisione; il dato non identifica la causa.")
        else:
            st.caption(f"{row['Mappa']} · {row['Mappe']} mappe, {row['Vittorie %']:.0f}% di vittorie.")
    st.subheader("Contributi individuali")
    player_view(filtered, key="overview_stats")
    st.caption("Le percentuali usano i tentativi disponibili. Passa sulle intestazioni per leggere le definizioni.")
    with st.expander("Sinergie e combinazioni del core (3+ membri)"):
        st.caption("Rispetta tutti i filtri attivi (periodo, mappe e composizione) per le formazioni con almeno 3 membri del roster.")
        lineups = lineup_summary(filtered, min_members=3)
        if lineups.empty:
            st.info("Nessuna combinazione con almeno 3 membri nel campione filtrato attuale.")
        else:
            show_table(lineups)

elif page == "Giocatori":
    selected = st.selectbox("Giocatore", NAMES)
    role = next(p["role"] for p in ROSTER if p["nickname"] == selected)
    st.caption(f"Ruolo provvisorio · {role}")
    own = filtered[filtered["nickname"] == selected]
    st.subheader("Prestazione nel campione selezionato")
    player_view(own, key="player_stats")
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
        st.caption("I valori 1° half e 2° half indicano la media di round vinti nei rispettivi tempi regolamentari (non distinguono il lato T/CT e il 2° half può avere meno round giocati).")
    st.subheader("Confronto ruoli nel campione filtrato")
    st.caption("Utile per confrontare i candidati AWP (-Zoen- e LEWALKER), l'Entry (Senshe) e il supporto/clutch (oSpEcTeRo e Grvpe).")
    player_view(filtered, key="role_stats")
    st.subheader("Roster")
    show_table(pd.DataFrame(ROSTER).rename(columns={"nickname": "Giocatore", "role": "Ruolo provvisorio"}))
    for member in ROSTER:
        st.link_button(member["nickname"] + " · FACEIT", "https://www.faceit.com/en/players/" + member["nickname"])

elif page == "Partite":
    games = team_maps(filtered).sort_values("date", ascending=False)
    if games.empty:
        st.info("Nessuna partita corrisponde ai filtri.")
        st.stop()
    display = pd.DataFrame({
        "Data": games["date"].dt.strftime("%d/%m/%Y"),
        "Mappa": games["map"].str.title(),
        "Membri": games["roster_count"],
        "Esito": games["won"].map({1: "Vittoria", 0: "Sconfitta"}).fillna("Non disponibile"),
        "Punteggio": [
            f"{int(a)} : {int(b)}" if pd.notna(a) and pd.notna(b) else "—"
            for a, b in zip(games["score_for"], games["score_against"])],
        "Round 1° half": games["first_half_score"],
        "Round 2° half": games["second_half_score"],
        "Matchroom": ["https://www.faceit.com/en/cs2/room/" + value for value in games["match_id"]],
    })
    show_table(display)
    st.caption("I due tempi riportano i round vinti; non distinguono il lato T/CT. Gli ID completi restano nell'esportazione CSV.")
    options = list(games.itertuples())
    chosen = st.selectbox("Apri una mappa-squadra", range(len(options)),
                          format_func=lambda i: f"{options[i].date:%d/%m %H:%M} · {options[i].map.title()} · {options[i].roster_count} membri")
    game = options[chosen]
    detail = filtered[(filtered["match_id"] == game.match_id) & (filtered["map_no"] == game.map_no) & (filtered["team_id"] == game.team_id)]
    player_view(detail, key="match_stats")
    st.link_button("Apri la matchroom FACEIT", f"https://www.faceit.com/en/cs2/room/{game.match_id}")
    st.download_button("Esporta il campione CSV", filtered.to_csv(index=False).encode("utf-8-sig"),
                       file_name="projectcs-campione.csv", mime="text/csv")
    st.caption("Per associare una nota a questa partita usa la sezione Revisione.")

elif page == "Revisione":
    st.write("Raccogli ciò che ha funzionato e ciò che va verificato. Una nota può riguardare l'intera partita oppure un round.")
    notes = db_module.read_records("reviews")
    if notes:
        show_records(notes, kind="review")
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
                db_module.add_review(None if match == "Nota generale" else match, round_no or None,
                                     kind, category, observation.strip(), status, action.strip())
                st.rerun()
    else:
        st.caption("Solo il proprietario può aggiungere osservazioni.")

elif page == "Allenamento":
    st.write("Trasforma un'osservazione verificata in un obiettivo e controllalo nelle sessioni successive.")
    st.caption("Nessuna diagnosi automatica della comunicazione: le priorità vengono concordate attraverso la revisione.")
    goals = db_module.read_records("objectives")
    if goals:
        show_records(goals, kind="objective")
    else:
        st.info("Nessun obiettivo impostato.")
    if owner and goals:
        goal = st.selectbox("Aggiorna obiettivo", goals, format_func=lambda r: f"#{r['id']} · {r['title']}")
        status = st.selectbox("Nuovo stato", ["Da allenare", "In corso", "Da verificare", "Completato"])
        if st.button("Aggiorna stato"):
            db_module.set_objective_status(goal["id"], status)
            st.rerun()
    if owner:
        with st.form("objective"):
            title = st.text_input("Obiettivo", max_chars=200)
            action = st.text_area("Azione concreta", max_chars=2000)
            verification = st.text_input("Come verifichiamo il progresso?", max_chars=500)
            submit = st.form_submit_button("Aggiungi obiettivo")
        if submit:
            if title.strip() and action.strip():
                db_module.add_objective(title.strip(), action.strip(), verification.strip())
                st.rerun()
            else:
                st.error("Inserisci titolo e azione.")
