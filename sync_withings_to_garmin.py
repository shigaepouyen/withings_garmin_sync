import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from garmin_auth import GarminAuth
from withings_mcp import fetch_weight_measurements

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = BASE_DIR / ".withings_garmin_sync_state.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronise les pesées Withings vers Garmin Connect."
    )
    parser.add_argument("--start-date", help="Date de début au format YYYY-MM-DD")
    parser.add_argument("--end-date", help="Date de fin au format YYYY-MM-DD")
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="Fichier d'état pour les synchros automatiques",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="Nombre de jours à relire au premier lancement",
    )
    parser.add_argument(
        "--overlap-days",
        type=int,
        default=3,
        help="Jours de recouvrement pour rattraper un éventuel retard de synchro",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche ce qui serait importé sans écrire dans Garmin",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Réimporte même si Garmin a déjà une pesée ce jour-là",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche des logs détaillés sur stderr pendant la synchro",
    )
    return parser.parse_args()


def log_verbose(enabled, message):
    if not enabled:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)


def load_state(path):
    state_path = Path(path)
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(path, payload):
    state_path = Path(path)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_date_range(args, state):
    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()

    if args.start_date:
        return date.fromisoformat(args.start_date), end_date

    if state.get("last_checked_date"):
        start_date = date.fromisoformat(state["last_checked_date"]) - timedelta(days=args.overlap_days)
    else:
        start_date = end_date - timedelta(days=args.days_back)

    return max(start_date, end_date - timedelta(days=365)), end_date


def get_garmin_client(verbose):
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL et GARMIN_PASSWORD requis dans l'environnement")
    client = GarminAuth(email, password)
    log_verbose(verbose, "Authentification Garmin (OAuth1/OAuth2)")
    client.ensure_authenticated()
    log_verbose(verbose, "Connecté à Garmin Connect")
    return client


def get_existing_garmin_dates(client, start_date, end_date):
    data = client.get_weigh_ins(start_date.isoformat(), end_date.isoformat())
    return {item["summaryDate"] for item in data.get("dailyWeightSummaries", []) if item.get("summaryDate")}


def sync_weights(args):
    state = load_state(args.state_file)
    start_date, end_date = resolve_date_range(args, state)

    log_verbose(args.verbose, f"Plage analysée: {start_date} → {end_date}")
    log_verbose(args.verbose, f"Fichier d'état: {Path(args.state_file).resolve()}")

    log_verbose(args.verbose, "Récupération des pesées Withings")
    withings_rows = fetch_weight_measurements(
        start_date=start_date,
        end_date=end_date,
        latest_per_day=True,
    )
    log_verbose(args.verbose, f"Pesées Withings retenues: {len(withings_rows)}")

    garmin = get_garmin_client(args.verbose)
    log_verbose(args.verbose, "Connecté à Garmin Connect")

    existing_dates = get_existing_garmin_dates(garmin, start_date, end_date)
    log_verbose(args.verbose, f"Dates déjà présentes dans Garmin: {len(existing_dates)}")

    imported = []
    skipped = []

    for row in withings_rows:
        if row["date"] in existing_dates and not args.force:
            skipped.append({**row, "reason": "already_exists"})
            log_verbose(args.verbose, f"Skip {row['date']} ({row['weightKg']} kg) : déjà présent")
            continue

        if args.dry_run:
            imported.append(row)
            log_verbose(args.verbose, f"Dry-run {row['date']} ({row['weightKg']} kg)")
            continue

        garmin.add_weigh_in(row["weightKg"], "kg", row["date"])
        imported.append(row)
        log_verbose(args.verbose, f"Import {row['date']} ({row['weightKg']} kg)")

    if not args.dry_run:
        save_state(
            args.state_file,
            {
                "last_checked_date": end_date.isoformat(),
                "last_run_at": datetime.now().isoformat(timespec="seconds"),
                "last_range_start": start_date.isoformat(),
                "last_range_end": end_date.isoformat(),
                "last_imported_count": len(imported),
                "last_skipped_count": len(skipped),
            },
        )

    summary = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dryRun": args.dry_run,
        "force": args.force,
        "withingsCount": len(withings_rows),
        "importedCount": len(imported),
        "skippedCount": len(skipped),
        "imported": imported,
        "skipped": skipped,
    }
    log_verbose(args.verbose, f"Terminé: {len(imported)} import(s), {len(skipped)} skip(s)")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        sync_weights(parse_args())
    except Exception as exc:
        print(f"Erreur de synchro : {exc}", file=sys.stderr)
        sys.exit(1)
