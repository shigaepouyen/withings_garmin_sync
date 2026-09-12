![Status: Stable](https://img.shields.io/badge/status-Stable-brightgreen)

# Withings → Garmin Sync

Synchronise automatiquement les pesées Withings vers Garmin Connect.

Le projet est autonome : tous les fichiers nécessaires sont dans ce dossier.

## Architecture

```
run_withings_garmin_sync.sh
        │
        ▼
sync_withings_to_garmin.py
        │
        ├── withings_mcp.py  → API Withings (récupère les pesées via OAuth)
        │
        └── garmin_auth.py   → API Garmin Connect (lit/écrit les pesées)
```

`garmin_auth.py` implémente le flow OAuth1/OAuth2 de Garmin nativement, sans dépendance tierce pour l'auth. Les tokens sont mis en cache dans `~/.garmin-mcp/` (compatible avec `nicolasvegam/garmin-connect-mcp` si utilisé par ailleurs). Le rafraîchissement OAuth2 via OAuth1 ne passe jamais par le SSO Garmin, ce qui évite les blocages Cloudflare/429.

## Dépendances

- **Python 3** — stdlib uniquement pour l'auth Garmin, `requests` pour les appels HTTP
- Accès aux APIs Withings (OAuth refresh token) et Garmin Connect (email/password)

```bash
pip install -r requirements.txt
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `sync_withings_to_garmin.py` | Orchestre la synchro : compare Withings et Garmin, importe les manquants |
| `withings_mcp.py` | Récupère les pesées depuis l'API Withings |
| `garmin_auth.py` | Authentification et appels directs à l'API Garmin Connect |
| `run_withings_garmin_sync.sh` | Point d'entrée : charge l'env et lance la synchro |
| `install_withings_garmin_launchagent.sh` | Installe l'automatisation macOS (LaunchAgent) |
| `uninstall_withings_garmin_launchagent.sh` | Supprime le LaunchAgent |
| `launchagents/com.jc.withings-garmin-sync.plist.template` | Template du LaunchAgent |
| `requirements.txt` | Dépendances Python |
| `.withings_garmin_sync.env.example` | Modèle pour le fichier de secrets |

Fichiers **non versionnés** (voir `.gitignore`) :
- `.withings_garmin_sync.env` — credentials Garmin
- `last_refresh_token.txt` — refresh token Withings (renouvelé automatiquement)
- `.withings_garmin_sync_state.json` — état du dernier run
- `logs/` — logs d'exécution

## Installation

### 1. Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### 2. Récupérer le token Withings initial

Obtenir un `refresh_token` Withings via OAuth et le placer dans :

```bash
echo "ton_refresh_token_initial" > last_refresh_token.txt
```

Le token est renouvelé automatiquement à chaque sync.

### 3. Créer le fichier de secrets

```bash
cp .withings_garmin_sync.env.example .withings_garmin_sync.env
```

Renseigner les credentials Garmin :

```bash
GARMIN_EMAIL="ton@email.com"
GARMIN_PASSWORD="ton_mot_de_passe"
```

### 4. Premier login Garmin

Au premier lancement, le script effectue un login SSO complet et met les tokens OAuth en cache dans `~/.garmin-mcp/`. Les runs suivants rafraîchissent le token OAuth2 sans repasser par le SSO.

```bash
./run_withings_garmin_sync.sh --dry-run --verbose
```

### 5. Installer l'automatisation macOS

Installation par défaut à 08h00 :

```bash
chmod +x run_withings_garmin_sync.sh install_withings_garmin_launchagent.sh uninstall_withings_garmin_launchagent.sh
./install_withings_garmin_launchagent.sh
```

Avec un horaire personnalisé :

```bash
./install_withings_garmin_launchagent.sh --hour 7 --minute 30
```

Suppression :

```bash
./uninstall_withings_garmin_launchagent.sh
```

## Synchro manuelle

Test à blanc :

```bash
./run_withings_garmin_sync.sh --dry-run
```

Test à blanc avec logs détaillés :

```bash
./run_withings_garmin_sync.sh --dry-run --verbose
```

Synchro réelle :

```bash
./run_withings_garmin_sync.sh
```

Rattrapage sur une période :

```bash
./run_withings_garmin_sync.sh --start-date 2026-01-01 --end-date 2026-12-31
```

## Logique de synchro

- Si Withings contient plusieurs pesées dans la même journée, seule la plus récente est retenue.
- Si Garmin a déjà une pesée sur la date, elle est ignorée (utiliser `--force` pour écraser).
- Au premier lancement : relecture des 7 derniers jours.
- Aux lancements suivants : recouvrement de 3 jours pour rattraper d'éventuels retards.

## Logs

```
logs/withings_garmin_sync.out.log
logs/withings_garmin_sync.err.log
```

## Note macOS — LaunchAgent

Le dossier du projet **ne doit pas être dans `~/Documents`** — macOS restreint l'accès aux dossiers utilisateur (TCC) pour les processus en arrière-plan. Placer le projet dans `~/<nom-du-dossier>` directement.

## Dépannage

| Erreur | Cause probable | Solution |
|---|---|---|
| `can't open input file` | Projet dans `~/Documents`, restriction TCC | Déplacer hors de `~/Documents` |
| `invalid refresh_token` | Token Withings expiré ou fichier absent | Renouveler via OAuth Withings |
| `Login SSO échoué` | Mauvais credentials ou MFA activé sur le compte Garmin | Vérifier `.withings_garmin_sync.env` |
| `429 / Cloudflare` | Trop de tentatives de login SSO | Attendre quelques heures ; ne se produit qu'au tout premier login |
