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

## Dati e gestione
Senza database locale l'app legge published/player_maps.csv e disabilita
l'accesso proprietario, anche se fosse presente una password nei secrets.
Sul tuo PC il database continua ad avere precedenza e la gestione rimane disponibile.
Un aggiornamento dei dati pubblicati richiede un nuovo export e caricamento.
