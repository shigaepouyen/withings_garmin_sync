import json
import os
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:
    requests = None

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None


# --- CONFIGURATION PERSONNELLE ---
BASE_DIR = Path(__file__).resolve().parent
CLIENT_ID = "f93838e14cabc308c32fee4da5dd238d3fbcb72527a2b42a36efcefb1784f602"
CLIENT_SECRET = "650825de7ed9e662dde083e4037adcdbfd0fe4b959d7f1a8b3ec53105b3e0a1b"
TOKEN_FILE = BASE_DIR / "last_refresh_token.txt"
INITIAL_REFRESH_TOKEN = "c9b24bee0ae09ff1535c68198b98aa6e5855eca3"


mcp = FastMCP("Withings") if FastMCP else None


def http_post_form(url, payload):
    if requests is not None:
        return requests.post(url, data=payload, timeout=30).json()

    data = urlencode(payload).encode("utf-8")
    request = Request(url, data=data, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def http_get_json(url, params, headers=None):
    if requests is not None:
        return requests.get(url, params=params, headers=headers, timeout=30).json()

    query = urlencode(params)
    request = Request(f"{url}?{query}", headers=headers or {}, method="GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_current_refresh_token():
    """Récupère le dernier refresh_token sauvegardé ou utilise l'initial."""
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "r", encoding="utf-8") as file:
            return file.read().strip()
    return INITIAL_REFRESH_TOKEN


def save_refresh_token(token):
    """Sauvegarde le nouveau refresh_token pour la prochaine utilisation."""
    with open(TOKEN_FILE, "w", encoding="utf-8") as file:
        file.write(token)


def get_access_token():
    """Échange le refresh_token contre un access_token tout neuf."""
    current_token = get_current_refresh_token()
    response = http_post_form(
        "https://wbsapi.withings.net/v2/oauth2",
        {
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": current_token,
        },
    )

    if response.get("status") != 0:
        raise Exception(f"Erreur d'authentification Withings: {response}")

    new_refresh = response["body"]["refresh_token"]
    save_refresh_token(new_refresh)
    return response["body"]["access_token"]


def fetch_measure_groups(start_date=None, end_date=None, meastypes="1,6"):
    token = get_access_token()
    params = {
        "action": "getmeas",
        "meastypes": meastypes,
        "category": 1,
    }

    if start_date is not None:
        params["startdate"] = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    if end_date is not None:
        params["enddate"] = int(datetime.combine(end_date, datetime.max.time()).timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    measure_groups = []
    offset = None

    while True:
        current_params = dict(params)
        if offset is not None:
            current_params["offset"] = offset

        response = http_get_json(
            "https://wbsapi.withings.net/v2/measure",
            current_params,
            headers=headers,
        )
        if response.get("status") != 0:
            raise Exception(f"Erreur API Withings : {response.get('error', 'Inconnue')}")

        body = response.get("body", {})
        measure_groups.extend(body.get("measuregrps", []))

        if not body.get("more") or body.get("offset") is None:
            break

        offset = body["offset"]

    return measure_groups


def fetch_weight_measurements(start_date=None, end_date=None, latest_per_day=False):
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    measure_groups = fetch_measure_groups(start_date=start_date, end_date=end_date, meastypes="1")
    results = []

    for group in measure_groups:
        timestamp = group.get("date")
        if timestamp is None:
            continue

        measurement_date = datetime.fromtimestamp(timestamp).date().isoformat()
        for measurement in group.get("measures", []):
            if measurement.get("type") != 1:
                continue

            value = measurement["value"] * (10 ** measurement["unit"])
            results.append(
                {
                    "date": measurement_date,
                    "timestamp": timestamp,
                    "weightKg": round(value, 2),
                }
            )

    results.sort(key=lambda row: (row["date"], row["timestamp"], row["weightKg"]))

    if latest_per_day:
        deduplicated = {}
        for row in results:
            deduplicated[row["date"]] = row
        return [deduplicated[key] for key in sorted(deduplicated)]

    return results


def get_latest_body_metrics() -> str:
    """Récupère le dernier poids et la masse grasse depuis Withings avec la date."""
    try:
        measure_groups = fetch_measure_groups(meastypes="1,6")
        if not measure_groups:
            return "Aucune donnée trouvée sur ton compte Withings."

        latest_group = measure_groups[0]
        date_str = datetime.fromtimestamp(latest_group["date"]).strftime("%d/%m/%Y à %H:%M")

        results = []
        for measure in latest_group["measures"]:
            value = measure["value"] * (10 ** measure["unit"])
            if measure["type"] == 1:
                results.append(f"Poids : {value:.2f} kg")
            elif measure["type"] == 6:
                results.append(f"Masse grasse : {value:.2f} %")

        return f"Mesure du {date_str} | " + " | ".join(results)
    except Exception as exc:
        return f"Erreur technique : {str(exc)}"


def get_weight_measurements(start_date: str, end_date: str, latest_per_day: bool = True) -> str:
    """Récupère les pesées Withings sur une plage de dates au format JSON."""
    try:
        rows = fetch_weight_measurements(
            start_date=start_date,
            end_date=end_date,
            latest_per_day=latest_per_day,
        )
        return json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False)
    except Exception as exc:
        return f"Erreur technique : {str(exc)}"


if mcp:
    get_latest_body_metrics = mcp.tool()(get_latest_body_metrics)
    get_weight_measurements = mcp.tool()(get_weight_measurements)


if __name__ == "__main__":
    if not mcp:
        raise RuntimeError("Le module 'mcp' n'est pas installé dans cet environnement.")
    mcp.run()
