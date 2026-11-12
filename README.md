# ProjectCS · prototipo 0.1

Dashboard Streamlit per un roster CS2 orientato a ESEA Main e tornei.
Roster: Grvpe, -Zoen-, oSpEcTeRo, LEWALKER, Senshe.

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
- Filtri per periodo, mappa e composizione nella **stessa squadra**.
- Panoramica mappe, andamento e statistiche individuali pesate.
- Confronto dentro/fuori dal core: almeno 3 membri contro solo/duo.
- Elenco partite, dettaglio e download CSV.
- Note positive/negative, obiettivi e stati, modificabili solo dal proprietario.
- Importatore ufficiale FACEIT, paginato e idempotente, con archivio locale SQLite.
- Tabelle predisposte per round, eventi temporali, assegnazioni dei ruoli e versioni delle analisi.

Le statistiche visualizzate provengono esclusivamente dalle partite importate da FACEIT.
Accesso API e importazione live verificati il 12 novembre 2026. Le chiavi restano locali.

## Configurazione

Copia `.streamlit/secrets.example.toml` in `.streamlit/secrets.toml`
e inserisci una chiave FACEIT **server-side**. Imposta una password amministratore
casuale di almeno 32 caratteri. Non committare i secrets.

```powershell
python -m projectcs.sync --since 2026-08-20
# Intervallo UTC con data finale esclusa:
python -m projectcs.sync --since 2026-10-01 --until 2026-11-01
```

L'importatore cerca lo storico di tutti e cinque: comprende solo, duo e gruppi.
Il 20 agosto è soltanto un punto di partenza proposto; le date precise di formazione
del core e ingresso di Senshe non sono state ancora concordate.
Nickname usati per risolvere gli ID stabili FACEIT; gli ID sono salvati nel database.
Risposte grezze sono conservate per poter rivedere la normalizzazione.
Partite già presenti vengono aggiornate, senza duplicare le righe.
In caso di statistiche non disponibili, il match è segnalato e i dati precedenti
restano intatti. Ripetere l'importazione per riprovare.
I limiti di storico FACEIT richiedono intervalli più brevi per giocatori molto attivi.
Una mappa-squadra è distinta dalle altre squadre e dalle altre mappe della stessa serie.

## Metriche e limiti

- Win rate: vittorie / mappe-squadra con esito disponibile.
- K/D: somma kill / somma morti, su righe con entrambi i valori.
  Se le morti totali sono zero il rapporto non viene visualizzato.
- KPR: somma kill / somma round, su righe con entrambi i valori.
- ADR: media pesata sui round **con ADR disponibile**.
- DPR: morti totali / round giocati, su righe con entrambi i valori; sostituisce il conteggio dei round con ADR nella tabella.
- I dati mancanti restano nulli, mai convertiti in zero.
- I filtri della scheda giocatore valgono per la prestazione selezionata.
  Il confronto dentro/fuori mantiene mappe e periodo ma ignora il filtro composizione.
- Nessuna correzione per Elo avversario, ruolo o economia è ancora applicata.
- Nessun voto complessivo, diagnosi causale o coach AI.
- Soglia di 10 mappe e win rate sotto 45% sono segnali esplorativi configurati
  nel prototipo, non benchmark validati.
- Lati T/CT, aperture, trade, conversioni numeriche, post-plant, retake,
  utility ed economia **non sono ancora calcolati**.
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
Il pacchetto include published/player_maps.csv: una copia delle statistiche FACEIT reali.
Senza database locale la dashboard legge questa copia e disabilita la gestione.
Non servono secrets per il test pubblico. I dati pubblicati non si aggiornano da soli.
Per la procedura completa consulta DEPLOY_STREAMLIT.md.
Non abilitare scritture online finché non è stato collegato un archivio persistente.
Il deploy non è automatico dal solo push GitHub: serve creare l'app in Community Cloud.

## Verifica

```powershell
python -m unittest discover -s tests -v
```

Test su pesi, dati mancanti, separazione delle squadre, idempotenza,
persistenza delle note e avvio delle pagine Streamlit.

## Evoluzione

1. Ampliare le analisi sulle statistiche reali importate.
2. Scegliere storage persistente online e autenticazione proprietario più completa.
3. Importare demo locali e aggiungere analisi round/eventi con versione.
4. Automatizzare gli aggiornamenti, verificando disponibilità e accesso ai download.
5. Estendere a posizioni e heatmap senza sostituire gli ID esistenti.

Fonti: [FACEIT Data API](https://docs.faceit.com/docs/data-api/data/),
[Downloads API](https://docs.faceit.com/getting-started/Guides/download-api/),
[Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud).
