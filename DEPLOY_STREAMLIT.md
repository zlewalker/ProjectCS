# Pubblicazione su Streamlit Community Cloud

Il repository supporta sia la modalità **live su Supabase PostgreSQL** (consigliata, con aggiornamento automatico e note/obiettivi abilitati) sia la modalità **sola lettura da snapshot `published/`**.

## Secrets su Streamlit Community Cloud
1. Accedi a https://share.streamlit.io/ → *Settings → Secrets* dell'app **ProjectCS**.
2. Incolla (sostituendo la password admin):
   ```toml
   DATABASE_URL = "postgresql://postgres.<project-ref>:<url-encoded-password>@aws-1-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
   ADMIN_PASSWORD = "la-tua-password-admin"
   ```
3. Se `DATABASE_URL` non è configurata, l'app ricade automaticamente in sola lettura sui CSV in `published/`.

## Automazione Cloud (GitHub Actions)
In *Settings → Secrets and variables → Actions* del repository `zlewalker/ProjectCS` configura:
- `DATABASE_URL`: connessione Session Pooler Supabase (con RLS abilitato sulle tabelle `public`).
- `FACEIT_API_KEY`: sync automatico partite FACEIT.
- `LEETIFY_API_KEY`: sync automatico partite CS2 Premier (`source = "premier"`).
- `FACEIT_DOWNLOAD_API_KEY` *(opzionale)*: download automatico demo FACEIT con rate-limit e auto-cancellazione.

Il workflow `.github/workflows/demo_sync.yml` gira ogni 6 ore (o manualmente via *Actions → Run workflow*), sincronizza FACEIT e Premier su Supabase, elabora eventuali demo in `demos/` eliminandole dopo la validazione e aggiorna `published/`.
