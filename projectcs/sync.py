"""Uso:
  python -m projectcs.sync --since 2026-08-20 --until 2026-11-13
  python -m projectcs.sync --reprocess-local --export-snapshot
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import requests

from projectcs.config import DB_PATH, NAMES, ROOT
from projectcs.db import (
    complete_match_ids_conn,
    connect,
    read_demo_events,
    read_demo_rounds,
    read_demo_status,
    read_rows,
    reprocess_stored_matches,
    restore_published_demo_state,
    save_match,
)
from projectcs.faceit import FaceitClient, FaceitError, normalize_match


def timestamp(text):
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _write_and_validate_csv(tmp_path, rows):
    fieldnames = list(rows[0])
    with tmp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with tmp_path.open("r", newline="", encoding="utf-8") as check_file:
        reader = csv.DictReader(check_file)
        if reader.fieldnames != fieldnames:
            raise RuntimeError("Intestazione CSV temporaneo non valida.")
        count = sum(1 for _ in reader)
        if count != len(rows):
            raise RuntimeError("Conteggio righe CSV temporaneo incoerente.")


def export_snapshot(db_path=DB_PATH, published_dir=None):
    published = Path(published_dir) if published_dir else (ROOT / "published")
    published.mkdir(parents=True, exist_ok=True)
    restore_published_demo_state(db_path=db_path, published_dir=published)

    rows = read_rows(db_path)
    if not rows:
        raise RuntimeError("Impossibile esportare lo snapshot: nessuna riga disponibile.")

    demo_events = read_demo_events(db_path)
    demo_rounds = read_demo_rounds(db_path)
    demo_status = read_demo_status(db_path)

    csv_path = published / "player_maps.csv"
    events_path = published / "demo_events.csv"
    rounds_path = published / "demo_rounds.csv"
    status_path = published / "demo_status.csv"
    meta_path = published / "metadata.json"

    pid = os.getpid()
    csv_tmp = published / f".player_maps.{pid}.csv.tmp"
    events_tmp = published / f".demo_events.{pid}.csv.tmp"
    rounds_tmp = published / f".demo_rounds.{pid}.csv.tmp"
    status_tmp = published / f".demo_status.{pid}.csv.tmp"
    meta_tmp = published / f".metadata.{pid}.json.tmp"

    metadata = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "matches": len({r["match_id"] for r in rows}),
        "player_maps": len(rows),
        "demo_events": len(demo_events),
        "demo_rounds": len(demo_rounds),
    }

    try:
        _write_and_validate_csv(csv_tmp, rows)
        if demo_events:
            _write_and_validate_csv(events_tmp, demo_events)
        if demo_rounds:
            _write_and_validate_csv(rounds_tmp, demo_rounds)
        if demo_status:
            _write_and_validate_csv(status_tmp, demo_status)

        meta_tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        parsed_meta = json.loads(meta_tmp.read_text(encoding="utf-8"))
        if parsed_meta.get("player_maps") != len(rows):
            raise RuntimeError("Metadata temporaneo incoerente.")

        prev_csv_bytes = csv_path.read_bytes() if csv_path.exists() else None
        prev_events_bytes = events_path.read_bytes() if events_path.exists() else None
        prev_rounds_bytes = rounds_path.read_bytes() if rounds_path.exists() else None
        prev_status_bytes = status_path.read_bytes() if status_path.exists() else None

        os.replace(csv_tmp, csv_path)
        try:
            if demo_events:
                os.replace(events_tmp, events_path)
            else:
                events_path.unlink(missing_ok=True)
            if demo_rounds:
                os.replace(rounds_tmp, rounds_path)
            else:
                rounds_path.unlink(missing_ok=True)
            if demo_status:
                os.replace(status_tmp, status_path)
            else:
                status_path.unlink(missing_ok=True)
            os.replace(meta_tmp, meta_path)
        except Exception:
            if prev_csv_bytes is not None:
                csv_path.write_bytes(prev_csv_bytes)
            else:
                csv_path.unlink(missing_ok=True)
            if prev_events_bytes is not None:
                events_path.write_bytes(prev_events_bytes)
            else:
                events_path.unlink(missing_ok=True)
            if prev_rounds_bytes is not None:
                rounds_path.write_bytes(prev_rounds_bytes)
            else:
                rounds_path.unlink(missing_ok=True)
            if prev_status_bytes is not None:
                status_path.write_bytes(prev_status_bytes)
            else:
                status_path.unlink(missing_ok=True)
            raise
        return metadata
    finally:
        csv_tmp.unlink(missing_ok=True)
        events_tmp.unlink(missing_ok=True)
        rounds_tmp.unlink(missing_ok=True)
        status_tmp.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Importa lo storico dei cinque membri, incluse solo/duo.")
    parser.add_argument("--since", help="Data iniziale UTC inclusa: YYYY-MM-DD")
    parser.add_argument("--until", help="Data finale UTC esclusa; default adesso")
    parser.add_argument("--force", action="store_true", help="Riscarica anche le partite già presenti nel database")
    parser.add_argument("--reprocess-local", action="store_true", help="Ricalcola player_maps dai raw_json locali senza rete")
    parser.add_argument("--export-snapshot", action="store_true", help="Aggiorna published/player_maps.csv e metadata.json")
    args = parser.parse_args()

    if args.reprocess_local:
        try:
            result = reprocess_stored_matches()
        except Exception as error:
            print(f"Rielaborazione locale fallita: {error}")
            return 1
        failed_local = result.get("failed", [])
        if failed_local:
            print(
                f"Rielaborazione locale incompleta: {result['matches']} partite aggiornate, "
                f"{len(failed_local)} fallite. Snapshot non esportato."
            )
            return 1
        print(f"Rielaborate in locale {result['matches']} partite ({result['player_maps']} righe).")
        if args.export_snapshot:
            try:
                meta = export_snapshot()
            except Exception as error:
                print(f"Esportazione snapshot fallita: {error}")
                return 1
            print(f"Snapshot esportato: {meta['matches']} partite, {meta['player_maps']} righe.")
        return 0

    if not args.since:
        parser.error("--since è obbligatorio salvo uso di --reprocess-local")

    since = timestamp(args.since)
    until = timestamp(args.until) if args.until else int(datetime.now(timezone.utc).timestamp())
    if since >= until:
        parser.error("--since deve precedere --until")
    key = os.environ.get("FACEIT_API_KEY")
    if not key:
        try:
            import tomllib
            with (ROOT / ".streamlit" / "secrets.toml").open("rb") as file:
                key = tomllib.load(file).get("FACEIT_API_KEY")
        except FileNotFoundError:
            pass
    try:
        client = FaceitClient(key)
        tracked = {}
        for nickname in NAMES:
            player = client.player(nickname)
            tracked[player["player_id"]] = nickname
            with connect() as conn:
                conn.execute("INSERT OR REPLACE INTO players VALUES (?,?,?)",
                             (player["player_id"], nickname, player.get("steam_id_64")))
        matches = {}
        for pid in tracked:
            for item in client.history(pid, since, until - 1):
                if str(item.get("status", "")).casefold() == "finished":
                    matches[item["match_id"]] = item

        with connect() as conn:
            already_stored = complete_match_ids_conn(conn)

        pending = sorted(matches) if args.force else [m for m in sorted(matches) if m not in already_stored]
        skipped = len(matches) - len(pending)
        print(f"Trovate {len(matches)} partite uniche ({skipped} già in archivio, {len(pending)} da scaricare). Importazione idempotente.")
        failed = []
        for index, match_id in enumerate(pending, 1):
            try:
                match = client.get(f"/matches/{match_id}")
                stats = client.get(f"/matches/{match_id}/stats")
                rows = normalize_match(match, stats, tracked)
                if not rows:
                    raise FaceitError("Statistiche non ancora disponibili")
                match["_projectcs_stats"] = stats
                save_match(match, rows)
                print(f"[{index}/{len(pending)}] {match_id}: {len(rows)} righe")
            except (FaceitError, requests.RequestException) as error:
                failed.append({"match_id": match_id, "error": type(error).__name__})
                print(f"[{index}/{len(pending)}] {match_id}: non importata, riprovare.")
        with connect() as conn:
            conn.execute("INSERT INTO analysis_runs(match_id,module,version,status,created_at,details_json) VALUES (?,?,?,?,?,?)",
                         (None, "faceit_sync", "2", "partial" if failed else "complete",
                          datetime.now(timezone.utc).isoformat(),
                          json.dumps({"matches": len(matches), "downloaded": len(pending), "skipped": skipped, "failed": failed})))
        if failed:
            print(f"{len(failed)} partite da riprovare. I dati preesistenti sono conservati in caso di errore.")
            return 1
        if args.export_snapshot:
            try:
                meta = export_snapshot()
                print(f"Snapshot esportato: {meta['matches']} partite, {meta['player_maps']} righe.")
            except Exception as error:
                print(f"Esportazione snapshot fallita: {error}")
                return 1
        return 0
    except (FaceitError, requests.RequestException) as error:
        print("Importazione interrotta: controlla chiave API, connessione e intervallo date.")
        print(str(error) if isinstance(error, FaceitError) else type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
