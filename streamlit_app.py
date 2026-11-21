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
    ALL_MAN_SITUATIONS,
    DEFAULT_MAN_SITUATIONS,
    demo_economy_summary,
    demo_man_advantage_summary,
    demo_side_summary,
    demo_tactical_summary,
    lineup_summary,
    load_data,
    load_demo_events,
    load_demo_rounds,
    map_summary,
    prepare_heatmap_events,
    summary,
    team_maps,
    using_snapshot,
)
from projectcs import db as db_module
from projectcs import demos as demos_module
from projectcs.ui import (
    CHART_CONFIG, RADAR_CHART_CONFIG, build_map_chart, build_radar_heatmap_chart, build_trend_chart,
    metric_value, player_view, show_players, show_records, show_table, trend_data,
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
    page = st.radio(
        "Navigazione",
        ["Panoramica", "Demo & Heatmap", "Giocatori", "Partite", "Revisione", "Allenamento"],
        label_visibility="collapsed",
    )
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
    "Demo & Heatmap": "Statistiche tattiche da demo (T/CT, 5v4, siti A/B, economia), heatmap 2D e coda download.",
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

elif page == "Demo & Heatmap":
    if filtered.empty:
        st.info("Nessuna partita corrisponde ai filtri.")
        st.stop()
    totals = summary(filtered)
    demo_events_df = load_demo_events()
    demo_rounds_df = load_demo_rounds()
    hm_events = prepare_heatmap_events(demo_events_df, filtered)
    side_table = demo_side_summary(demo_rounds_df, filtered)
    tactical_table = demo_tactical_summary(demo_rounds_df, filtered)
    econ_table = demo_economy_summary(demo_rounds_df, filtered)
    queue_items = [] if using_snapshot() else demos_module.pending_demo_queue()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Copertura demo campione", metric_value(totals["demo_coverage"], "%"))
    k2.metric("Eventi demo nel campione", len(hm_events))
    k3.metric("Round tattici estratti", int(side_table[["Round T", "Round CT"]].sum().sum()) if not side_table.empty else 0)
    k4.metric("In coda FACEIT (≤30gg)", "—" if using_snapshot() else len(queue_items))
    st.caption(
        "Le metriche demo sono mostrate esclusivamente per le mappe con killfeed e round validati e riconciliati. "
        "I file .dem vengono eliminati automaticamente subito dopo l'estrazione per non occupare spazio."
    )

    with st.container(border=True):
        st.subheader("Mappa tattica 2D & Heatmap Radar")
        avail_maps = sorted(filtered["map"].unique())
        pref_maps = [m for m in avail_maps if m in PRIORITY_MAPS] + [m for m in avail_maps if m not in PRIORITY_MAPS]
        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.4, 1.2])
        hm_map = f1.selectbox("Mappa radar", pref_maps, format_func=lambda m: str(m).title())
        hm_player = f2.selectbox("Giocatore", ["Tutti del roster"] + NAMES + ["Tutti (inclusi avversari)"])
        hm_filter = f3.selectbox(
            "Filtro eventi",
            ["Tutte le morti", "Morti senza trade (>5s)", "Duelli di apertura (First Kill/Death)"],
        )
        hm_mode_label = f4.selectbox("Coordinate", ["Posizione vittima", "Posizione killer"])
        show_vec = st.checkbox("Mostra traiettorie duelli (killer → vittima)", value=True)

        coord_mode = "victim" if hm_mode_label == "Posizione vittima" else "attacker"
        include_opp = hm_player == "Tutti (inclusi avversari)"
        mode_events = prepare_heatmap_events(
            demo_events_df,
            filtered,
            mode=coord_mode,
            include_opponents=include_opp,
        )
        map_ev = mode_events[mode_events["map"] == hm_map].copy() if not mode_events.empty else mode_events
        if hm_player not in {"Tutti del roster", "Tutti (inclusi avversari)"} and not map_ev.empty:
            if coord_mode == "victim":
                map_ev = map_ev[
                    (map_ev["victim_name"] == hm_player)
                    | (map_ev["victim_id"].isin(filtered[filtered["nickname"] == hm_player]["player_id"].unique()))
                ]
            else:
                map_ev = map_ev[
                    (map_ev["attacker_name"] == hm_player)
                    | (map_ev["attacker_id"].isin(filtered[filtered["nickname"] == hm_player]["player_id"].unique()))
                ]
        if hm_filter == "Morti senza trade (>5s)" and not map_ev.empty:
            map_ev = map_ev[map_ev["is_traded"] == 0]
        elif hm_filter == "Duelli di apertura (First Kill/Death)" and not map_ev.empty:
            map_ev = map_ev[map_ev["is_opening"] == 1]

        st.plotly_chart(
            build_radar_heatmap_chart(map_ev, hm_map, mode=coord_mode, show_vectors=show_vec),
            width="stretch",
            theme=None,
            config=RADAR_CHART_CONFIG,
        )
        st.caption("Usa la rotella del mouse per fare zoom e clicca + trascina per spostarti sulla mappa (doppio clic per ripristinare la vista).")
        if map_ev.empty or map_ev.dropna(subset=["victim_rx", "victim_ry"]).empty:
            st.caption(
                f"Nessuna coordinata posizionale demo presente per {str(hm_map).title()} con i filtri selezionati. "
                "Il grafico mostra la griglia tattica di riferimento dei siti."
            )

    st.subheader("Split reale Lato T vs Lato CT (da Demo)")
    if side_table.empty:
        st.info("Nessun round demo validato per le mappe nel filtro corrente. I parziali 1°/2° half in Panoramica non indicano il lato T/CT.")
    else:
        show_table(side_table)

    st.subheader("Conversioni 5v4 / 4v5, Post-plant Siti A/B e Retake")
    if tactical_table.empty:
        st.info("Statistiche 5v4, post-plant A/B e retake disponibili dopo l'elaborazione dei round demo.")
    else:
        show_table(tactical_table)

    with st.expander("Altre situazioni numeriche (5v3, 5v2, 5v1, 3v5, 2v5, 1v5 e tutte le XvY)", expanded=False):
        st.caption(
            "Calcolate dalla sequenza delle morti nei round demo validati. "
            "Le situazioni principali 5v4 e 4v5 restano nella tabella sopra."
        )
        selected_situations = st.multiselect(
            "Situazioni da visualizzare",
            ALL_MAN_SITUATIONS,
            default=DEFAULT_MAN_SITUATIONS,
            key="demo_man_situations",
        )
        man_table = demo_man_advantage_summary(
            demo_events_df, demo_rounds_df, filtered, situations=selected_situations
        )
        if man_table.empty:
            st.info("Nessun round demo disponibile per le situazioni selezionate.")
        else:
            show_table(man_table)

    st.subheader("Rendimento per Economia (Pistol, Eco, Force, Full Buy)")
    if econ_table.empty:
        st.info("Nessun dato economico demo disponibile per il filtro selezionato.")
    else:
        show_table(econ_table)

    if owner and not using_snapshot():
        st.divider()
        st.subheader("Gestione coda Demo, Rate-Limit FACEIT e Auto-Pulizia")
        st.caption(
            "Puoi elaborare file depositati in `ProjectCS/demos/` oppure scaricare gradualmente le demo da FACEIT. "
            "Ogni file `.dem` viene eliminato automaticamente subito dopo la validazione."
        )
        c_act1, c_act2 = st.columns(2)
        if c_act1.button("Elabora cartella locale demos/ ed elimina completate", width="stretch"):
            res = demos_module.ingest_local_demos(delete_after_ingest=True)
            st.success(
                f"Elaborate {res['processed']} demo locali: {res['completed']} completate, {res['deleted']} file rimossi."
            )
            st.rerun()
        if c_act2.button("Scarica lotto demo da FACEIT (rate-limited + auto-delete)", width="stretch"):
            ctrl = demos_module.RateLimitController(min_interval_seconds=10.0, max_wait_seconds=60.0)
            dl = demos_module.FaceitDemoDownloader(api_key=secret("FACEIT_DOWNLOAD_API_KEY") or secret("FACEIT_API_KEY"), rate_controller=ctrl)
            dl_res = demos_module.download_and_process_queue(downloader=dl, limit=3)
            st.info(
                f"Coda: {dl_res['queued']} · Tentate: {dl_res['attempted']} · Completate: {dl_res['completed']} · Scadute: {dl_res['expired']}"
            )
            st.rerun()
