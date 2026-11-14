# ProjectCS · prototipo 0.2

Dashboard Streamlit per un roster CS2 orientato a ESEA Main e tornei.
Roster: Grvpe, -Zoen-, oSpEcTeRo, LEWALKER, Senshe.

## Interfaccia

- Tema antracite e arancione con pattern decorativo attenuato.
- Tabelle separate per Sintesi, Aperture, AWP e trade, Utility e Clutch, con definizioni nelle intestazioni.
- Confronti dentro/fuori compatti; note e obiettivi mostrati come schede con testo a capo.
- Grafico dell'andamento a tutta larghezza, con punti equidistanti per mappa.
- Sono visibili inizialmente le ultime 20 mappe; il selettore e la panoramica inferiore permettono di esplorare lo storico.
- La media mobile usa lo storico precedente alla finestra visibile; la scala percentuale resta 0–100 salvo scelta esplicita della scala adattiva.
- Le distanze sull'asse orizzontale indicano l'ordine delle partite, non il tempo trascorso.

## Avvio locale

Richiede Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m streamlit run streamlit_app.py
```

Nell'ambiente portable preparato sul PC del proprietario è disponibile anche
`avvia-prototipo.cmd`. Non serve attivare un ambiente virtuale.

## Cosa funziona

- Unico archivio di partite reali importate da FACEIT.
- Filtri per periodo, mappa (con toggle rapido per il map pool prioritario `Ancient / Anubis / Dust2`) e composizione nella **stessa squadra**.
- Panoramica mappe (con parziali 1° e 2° half), andamento, sinergie del core (combinazioni 3+ membri) e statistiche individuali pesate.
- Statistiche avanzate divise per vista (con denominatori visibili accanto alle percentuali):
  - **Sintesi generale**: K/D, KPR, ADR, DPR, HS %, Duelli entry, Aperture %, UDR, Morti senza trade %, Win %.
  - **Aperture, AWP & Trade**: Duelli entry, Freq. aperture %, Aperture %, Morti in apertura %, Morti senza trade %, Sniper KPR, Sniper %, Multikill (2K+).
  - **Utility, Flash & Clutch**: UDR, Flash lanciate, Flash %, Nemici flash/round, 1v1 tentati, Clutch 1v1 %, 1v2 tentati, Clutch 1v2 %.
- Confronto dentro/fuori dal core (almeno 3 membri contro solo/duo) e confronto ruoli tra i candidati del roster.
- Elenco partite con punteggi 1°/2° half, dettaglio mappa e download CSV.
- Note positive/negative, obiettivi e stati, modificabili solo dal proprietario.
- Importatore ufficiale FACEIT paginato, incrementale (salta le partite già in archivio salvo `--force`), con rielaborazione locale (`--reprocess-local`) ed export snapshot (`--export-snapshot`).
- Motore di calcolo **Morti senza trade %** (finestra 5 secondi sugli eventi `player_death` nella tabella `events`), con preservazione automatica dei conteggi demo durante i sync API.

Le statistiche visualizzate provengono esclusivamente dalle partite importate da FACEIT (e dagli eventuali eventi demo associati).
Accesso API e importazione live verificati il 12 novembre 2026. Le chiavi restano locali.

## Configurazione e sincronizzazione

Copia `.streamlit/secrets.example.toml` in `.streamlit/secrets.toml`
e inserisci una chiave FACEIT **server-side**. Imposta una password amministratore
casuale di almeno 32 caratteri. Non committare i secrets.

```powershell
# Importazione incrementale (scarica solo i match non ancora presenti in DB):
python -m projectcs.sync --since 2026-08-20

# Intervallo UTC con data finale esclusa:
python -m projectcs.sync --since 2026-10-01 --until 2026-11-01

# Ricalcolo locale delle metriche dai JSON grezzi già salvati + aggiornamento CSV pubblicato:
python -m projectcs.sync --reprocess-local --export-snapshot
```

L'importatore cerca lo storico di tutti e cinque: comprende solo, duo e gruppi.
Il 20 agosto è soltanto un punto di partenza proposto; le date precise di formazione
del core e ingresso di Senshe non sono state ancora concordate.
Nickname usati per risolvere gli ID stabili FACEIT; gli ID sono salvati nel database.
Risposte grezze sono conservate per poter rivedere la normalizzazione senza nuove chiamate API.
Partite già presenti vengono saltate di default (usa `--force` per riscaricarle) e non duplicano mai le righe.
In caso di statistiche non disponibili, il match è segnalato e i dati precedenti restano intatti.

## Metriche e limiti

Tutti i rapporti sono calcolati sui **conteggi accoppiati disponibili**, mai come media di rapporti o percentuali:

- **Win rate**: vittorie / mappe-squadra con esito disponibile.
- **K/D**: somma kill / somma morti, su righe con entrambi i valori (nullo se morti totali = 0).
- **KPR**: somma kill / somma round, su righe con entrambi i valori.
- **ADR**: media pesata sui round **con ADR disponibile**.
- **DPR**: morti totali / round giocati, su righe con entrambi i valori.
- **HS %**: somma headshot / somma kill × 100, su righe con entrambi i valori.
- **Aperture % (Entry Win %)**: somma `entry_wins` / somma `entry_count` × 100.
- **Freq. aperture % (Entry Rate %)**: somma `entry_count` / somma `rounds` × 100.
- **Morti in apertura % (First Death %)**: somma `(entry_count - entry_wins)` / somma `deaths` × 100.
- **Morti senza trade % (No trade %)**: somma `untraded_deaths` / somma `deaths` × 100, su righe con entrambi i valori.
  - Il calcolo rigoroso a finestra di 5 secondi (`compute_round_trades` / `apply_demo_trades` in `projectcs/db.py`) opera sugli eventi `player_death` delle demo nella tabella `events`, richiedendo round, timestamp/tick, ID e team completi e verificando la quadratura `traded_deaths + untraded_deaths == deaths` prima di salvare.
  - Poiché l'API Data v4 di FACEIT non espone i timestamp del killfeed round-per-round, sulle partite con sole statistiche API (o con eventi demo vuoti/incompleti) `untraded_deaths` resta `None` (`—` in tabella), senza mai essere approssimato o convertito in zero.
- **UDR**: somma `utility_damage` / somma `rounds`, su righe con entrambi i valori.
- **Flash %** e **Nemici flash/r**: somma `flash_successes` / somma `flash_count` × 100 (con denominatore `Flash` visibile) e somma `enemies_flashed` / somma `rounds`.
- **Sniper KPR** e **Sniper %**: somma `sniper_kills` / somma `rounds` e somma `sniper_kills` / somma `kills` × 100.
- **1v1 %** e **1v2 %**: somma clutch vinti / somma clutch tentati × 100 (con denominatori `1v1 tent.` e `1v2 tent.` visibili).
- **Multikill (2K+)**: somma dei round con kill multiple (`2K + 3K + 4K + 5K`), calcolata solo quando tutte e 4 le categorie sono presenti su ogni mappa del campione filtrato (altrimenti `None`).
- **Round 1° half / 2° half**: rappresentano i punteggi dei due tempi regolamentari e non lo split per lato T/CT.
- I dati mancanti restano nulli, mai convertiti in zero.
- I filtri della scheda giocatore valgono per la prestazione selezionata.
  Il confronto dentro/fuori mantiene mappe e periodo ma ignora il filtro composizione.
- Nessuna correzione per Elo avversario, ruolo o economia è ancora applicata.
- Nessun voto complessivo, diagnosi causale o coach AI.
- Soglia di 10 mappe e win rate sotto 45% sono segnali esplorativi configurati nel prototipo, non benchmark validati.
- Lati T/CT, conversioni 5v4/4v5, post-plant, retake ed economia richiedono il parser completo dei round demo.
- Comunicazione e qualità delle call richiedono osservazioni umane.
- Movimenti e heatmap sono rinviati; non viene ancora estratto alcun dato posizionale.

## Note, permessi e archivio

Senza ADMIN_PASSWORD ogni funzione di scrittura nell'interfaccia è disabilitata.
La password abilita una sessione proprietario; usare HTTPS per l'accesso remoto.
I dati sono in `data/projectcs.sqlite`, esclusi da Git.
SQLite è adatto al prototipo locale, **non garantisce persistenza su Community Cloud**.
Prima di raccogliere note online occorre scegliere e integrare un database esterno.
Non usare la cartella della repository pubblica per demo, credenziali o database.

## Deploy Streamlit Community Cloud

Collega GitHub, scegli `zlewalker/ProjectCS`, branch `main`,
entrypoint `streamlit_app.py`, Python 3.12.
Il pacchetto include `published/player_maps.csv`: una copia delle statistiche FACEIT reali.
Senza database locale la dashboard legge questa copia e disabilita la gestione.
Non servono secrets per il test pubblico. I dati pubblicati non si aggiornano da soli.
Per la procedura completa consulta `DEPLOY_STREAMLIT.md`.
Non abilitare scritture online finché non è stato collegato un archivio persistente.

## Verifica

```powershell
python -m unittest discover -s tests -v
```

Test su pesi, dati mancanti, morti senza trade (finestra 5s su demo e nullità su sola API), metriche avanzate FACEIT, separazione delle squadre, sincronizzazione incrementale, idempotenza, persistenza delle note e avvio delle pagine Streamlit.

Fonti: [FACEIT Data API](https://docs.faceit.com/docs/data-api/data/),
[Downloads API](https://docs.faceit.com/getting-started/Guides/download-api/),
[Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud).
