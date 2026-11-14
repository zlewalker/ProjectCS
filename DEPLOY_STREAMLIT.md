# Pubblicazione per il test del team

Il pacchetto contiene codice e un export delle statistiche FACEIT reali.
Non contiene chiavi, password, database locale, demo o annotazioni private.
La copia pubblicata serve alla consultazione: non si aggiorna automaticamente.

## GitHub
Carica il contenuto di ProjectCS nella radice di zlewalker/ProjectCS su main.
Nella radice devono comparire streamlit_app.py, requirements.txt,
projectcs/, published/ e .streamlit/config.toml.
Lo ZIP deve essere estratto: non caricare soltanto lo ZIP.
Se il caricamento via browser omette le cartelle nascoste, aggiungi
.streamlit/config.toml dal pulsante Add file > Create new file.

## Streamlit Community Cloud
1. Accedi a https://share.streamlit.io/ con il tuo account.
2. Create app > Yup, I have an app.
3. Repository: zlewalker/ProjectCS
4. Branch: main
5. Main file path: streamlit_app.py
6. Advanced settings: Python 3.12.
7. Non configurare secrets per questa versione in sola lettura.
8. Deploy. Condividi l'URL risultante con i compagni.

## Dati, Demo e automazione Cloud (GitHub Actions)
Senza database locale l'app legge `published/player_maps.csv` (e, quando presenti, `published/demo_events.csv` e `published/demo_rounds.csv`) e disabilita l'accesso proprietario, anche se fosse presente una password nei secrets.
Sul tuo PC il database continua ad avere precedenza e la gestione rimane disponibile.

Per elaborare le demo **senza dover tenere file `.dem` sul PC**:
1. **Tramite GitHub Actions (100% Cloud)**: aggiungi `FACEIT_API_KEY` (ed eventuale `FACEIT_DOWNLOAD_API_KEY`) in *Settings > Secrets and variables > Actions* del repository GitHub `zlewalker/ProjectCS`. Il workflow `.github/workflows/demo_sync.yml` gira ogni 6 ore (o manualmente da *Actions > Run workflow*), scarica un lotto di demo rispettando i rate-limit FACEIT, estrae gli eventi e i round tattici nei CSV compatti dentro `published/`, elimina subito i file `.dem` temporanei e fa il commit automatico su `main`.
2. **Caricamento temporaneo in `demos/`**: se carichi un file demo compresso (`.dem.zst` / `.dem.gz` / `.json`) nella cartella `demos/` (in locale o su GitHub), la pipeline lo valida, aggiorna `published/` e **cancella automaticamente il file demo** subito dopo l'elaborazione.
