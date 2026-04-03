import argparse
from collections import deque
import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from withings_mcp import fetch_weight_measurements


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = BASE_DIR / ".withings_garmin_sync_state.json"
DEFAULT_CONFIG_FILE = ROOT_DIR / "mcp_config.json"
DEFAULT_GARMIN_COMMAND = ["npx", "-y", "@nicolasvegam/garmin-connect-mcp"]
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronise les pesées Withings vers Garmin via le serveur MCP Garmin."
    )
    parser.add_argument("--start-date", help="Date de début au format YYYY-MM-DD")
    parser.add_argument("--end-date", help="Date de fin au format YYYY-MM-DD")
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="Fichier d'état pour les synchros automatiques",
    )
    parser.add_argument(
        "--config-file",
        default=str(DEFAULT_CONFIG_FILE),
        help="Fichier MCP contenant la config du serveur Garmin",
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


def parse_iso_date(value):
    return date.fromisoformat(value)


def load_state(path):
    state_path = Path(path)
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(path, payload):
    state_path = Path(path)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_date_range(args, state):
    end_date = parse_iso_date(args.end_date) if args.end_date else date.today()

    if args.start_date:
        start_date = parse_iso_date(args.start_date)
        return start_date, end_date

    if state.get("last_checked_date"):
        start_date = parse_iso_date(state["last_checked_date"]) - timedelta(days=args.overlap_days)
    else:
        start_date = end_date - timedelta(days=args.days_back)

    if start_date > end_date:
        start_date = end_date

    return start_date, end_date


def resolve_garmin_server(config_file):
    config_path = Path(config_file)
    if not config_path.exists():
        return list(DEFAULT_GARMIN_COMMAND), {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        garmin = config.get("mcpServers", {}).get("garmin", {})
        command = garmin.get("command")
        args = garmin.get("args", [])
        env = garmin.get("env", {})
        if command:
            return [command, *args], env
    except json.JSONDecodeError:
        pass

    return list(DEFAULT_GARMIN_COMMAND), {}


class McpStdioClient:
    def __init__(self, command, env=None, verbose=False):
        self.command = command
        self.env = env or {}
        self.verbose = verbose
        self.process = None
        self.next_id = 1
        self.stderr_lines = deque(maxlen=200)
        self.stderr_thread = None

    def __enter__(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **self.env},
        )
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _drain_stderr(self):
        for raw_line in iter(self.process.stderr.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            self.stderr_lines.append(line)
            if self.verbose:
                print(f"[garmin-mcp] {line}", file=sys.stderr)

    def _get_stderr_snapshot(self):
        if not self.stderr_lines:
            return ""
        return "\n".join(self.stderr_lines)

    def _send_message(self, payload):
        data = (json.dumps(payload) + "\n").encode("utf-8")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _read_message(self):
        while True:
            line = self.process.stdout.readline()
            if not line:
                stderr_output = self._get_stderr_snapshot()
                raise RuntimeError(stderr_output or "Le serveur Garmin MCP s'est arrêté sans réponse.")
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            if decoded.lower().startswith("content-length:"):
                headers = {"content-length": decoded.split(":", 1)[1].strip()}
                while True:
                    header_line = self.process.stdout.readline()
                    if not header_line:
                        stderr_output = self._get_stderr_snapshot()
                        raise RuntimeError(stderr_output or "Le serveur Garmin MCP s'est arrêté sans réponse.")
                    if header_line in (b"\r\n", b"\n"):
                        break
                    name, value = header_line.decode("utf-8").split(":", 1)
                    headers[name.strip().lower()] = value.strip()

                length = int(headers["content-length"])
                body = self.process.stdout.read(length)
                return json.loads(body.decode("utf-8"))

            return json.loads(decoded)

    def _request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        while True:
            message = self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message["result"]

    def _notification(self, method, params=None):
        self._send_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    def initialize(self):
        self._request(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "withings-garmin-sync", "version": "1.0.0"},
            },
        )
        self._notification("notifications/initialized")

    def call_tool(self, name, arguments):
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
        text = "\n".join(part for part in text_parts if part)

        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def get_existing_garmin_dates(client, start_date, end_date):
    payload = client.call_tool(
        "get_weigh_ins",
        {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
    ) or {}
    return {item["summaryDate"] for item in payload.get("dailyWeightSummaries", [])}


def sync_weights(args):
    log_verbose(args.verbose, "Chargement de l'état local")
    state = load_state(args.state_file)
    start_date, end_date = resolve_date_range(args, state)
    garmin_command, garmin_env = resolve_garmin_server(args.config_file)
    log_verbose(
        args.verbose,
        f"Plage analysée: {start_date.isoformat()} → {end_date.isoformat()}",
    )
    log_verbose(args.verbose, f"Fichier d'état: {Path(args.state_file).resolve()}")
    log_verbose(args.verbose, f"Config MCP: {Path(args.config_file).resolve()}")
    log_verbose(args.verbose, f"Commande Garmin: {' '.join(garmin_command)}")
    if garmin_env:
        log_verbose(args.verbose, f"Variables Garmin injectées: {', '.join(sorted(garmin_env))}")

    log_verbose(args.verbose, "Récupération des pesées Withings")
    withings_rows = fetch_weight_measurements(
        start_date=start_date,
        end_date=end_date,
        latest_per_day=True,
    )
    log_verbose(args.verbose, f"Pesées Withings retenues: {len(withings_rows)}")

    log_verbose(args.verbose, "Démarrage du serveur Garmin MCP")
    with McpStdioClient(garmin_command, env=garmin_env, verbose=args.verbose) as garmin:
        log_verbose(args.verbose, "Serveur Garmin MCP initialisé")
        log_verbose(args.verbose, "Lecture des pesées déjà présentes dans Garmin")
        existing_dates = get_existing_garmin_dates(garmin, start_date, end_date)
        log_verbose(args.verbose, f"Dates déjà présentes dans Garmin: {len(existing_dates)}")

        imported = []
        skipped = []

        for row in withings_rows:
            if row["date"] in existing_dates and not args.force:
                skipped.append({**row, "reason": "already_exists"})
                log_verbose(
                    args.verbose,
                    f"Skip {row['date']} ({row['weightKg']} kg) : déjà présent dans Garmin",
                )
                continue

            if args.dry_run:
                imported.append(row)
                log_verbose(
                    args.verbose,
                    f"Dry-run {row['date']} ({row['weightKg']} kg) : import simulé",
                )
                continue

            garmin.call_tool(
                "add_weigh_in",
                {"date": row["date"], "unitKey": "kg", "weight": row["weightKg"]},
            )
            imported.append(row)
            log_verbose(args.verbose, f"Import {row['date']} ({row['weightKg']} kg)")

    if not args.dry_run:
        log_verbose(args.verbose, "Sauvegarde du nouvel état local")
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
        log_verbose(args.verbose, f"État enregistré dans {Path(args.state_file).resolve()}")

    summary = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dryRun": args.dry_run,
        "force": args.force,
        "garminCommand": garmin_command,
        "withingsCount": len(withings_rows),
        "importedCount": len(imported),
        "skippedCount": len(skipped),
        "imported": imported,
        "skipped": skipped,
    }
    log_verbose(
        args.verbose,
        f"Terminé: {len(imported)} import(s), {len(skipped)} skip(s), dryRun={args.dry_run}",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        sync_weights(parse_args())
    except Exception as exc:
        print(f"Erreur de synchro : {exc}", file=sys.stderr)
        sys.exit(1)
