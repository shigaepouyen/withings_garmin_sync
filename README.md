# Withings → Garmin Sync

Synchronise automatiquement les pesées Withings vers Garmin Connect.

Le projet est autonome : tous les fichiers nécessaires sont dans ce dossier.

## Dépendances externes

- **[nicolasvegam/garmin-connect-mcp](https://github.com/nicolasvegam/garmin-connect-mcp)** — serveur MCP Garmin utilisé pour lire et écrire les pesées dans Garmin Connect. Lancé automatiquement via `npx` à chaque sync.

## Fichiers

| Fichier | Rôle |
|---|---|
| `sync_withings_to_garmin.py` | Compare Withings et Garmin, importe les jours manquants |
| `withings_mcp.py` | Récupère les pesées depuis l'API Withings |
| `run_withings_garmin_sync.sh` | Point d'entrée : charge l'env, fixe le PATH, lance la synchro |
| `install_withings_garmin_launchagent.sh` | Installe l'automatisation macOS (LaunchAgent) |
| `uninstall_withings_garmin_launchagent.sh` | Supprime le LaunchAgent |
| `launchagents/com.jc.withings-garmin-sync.plist.template` | Template du LaunchAgent |
| `mcp_config.json` | Config du serveur MCP Garmin |
| `.withings_garmin_sync.env.example` | Modèle pour le fichier de secrets |

Fichiers **non versionnés** (voir `.gitignore`) :
- `.withings_garmin_sync.env` — credentials Garmin
- `last_refresh_token.txt` — refresh token Withings (renouvelé automatiquement)
- `.withings_garmin_sync_state.json` — état du dernier run
- `logs/` — logs d'exécution

## Installation

### 1. Récupérer le token Withings initial

Obtenir un `refresh_token` Withings via OAuth et le placer dans :

```bash
echo "ton_refresh_token_initial" > last_refresh_token.txt
```

Le token est renouvelé automatiquement à chaque sync.

### 2. Créer le fichier de secrets

```bash
cp .withings_garmin_sync.env.example .withings_garmin_sync.env
```

Renseigner les credentials Garmin :

```bash
GARMIN_EMAIL="ton@email.com"
GARMIN_PASSWORD="ton_mot_de_passe"
```

### 3. Vérifier les dépendances

- `python3` disponible dans le PATH
- `npx` disponible (Node.js installé)
- Paquets Python : aucun requis (stdlib uniquement)

### 4. Installer l'automatisation macOS

Installation par défaut à 08h00 :

```bash
chmod +x run_withings_garmin_sync.sh install_withings_garmin_launchagent.sh uninstall_withings_garmin_launchagent.sh
./install_withings_garmin_launchagent.sh
```

Avec un horaire personnalisé :

```bash
./install_withings_garmin_launchagent.sh --hour 7 --minute 30
```

Sans chargement immédiat :

```bash
./install_withings_garmin_launchagent.sh --no-load
```

Suppression :

```bash
./uninstall_withings_garmin_launchagent.sh
```

## Synchro manuelle

Test à blanc :

```bash
python3 sync_withings_to_garmin.py --dry-run
```

Test à blanc avec logs détaillés :

```bash
python3 sync_withings_to_garmin.py --dry-run --verbose
```

Synchro réelle :

```bash
./run_withings_garmin_sync.sh
```

Rattrapage sur une période :

```bash
python3 sync_withings_to_garmin.py --start-date 2026-01-01 --end-date 2026-12-31
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

Pour capturer les logs d'un run manuel :

```bash
./run_withings_garmin_sync.sh --dry-run --verbose > logs/manual_sync.out.log 2> logs/manual_sync.err.log
```

## Note macOS — PATH et LaunchAgent

Les LaunchAgents macOS s'exécutent avec un PATH minimal. Le script `run_withings_garmin_sync.sh` exporte explicitement le chemin vers `node`/`npx` (NVM). Si tu utilises une version de Node différente, mettre à jour la ligne `export PATH=...` dans ce fichier.

Le dossier du projet **ne doit pas être dans `~/Documents`** — macOS restreint l'accès aux dossiers utilisateur (TCC) pour les processus en arrière-plan. Placer le projet dans `~/<nom-du-dossier>` directement.

## Dépannage

| Erreur | Cause probable | Solution |
|---|---|---|
| `can't open input file` | Projet dans `~/Documents`, restriction TCC | Déplacer hors de `~/Documents` |
| `invalid refresh_token` | Token expiré ou fichier absent | Renouveler via OAuth Withings |
| `node: No such file or directory` | PATH NVM absent sous launchd | Mettre à jour `export PATH=...` dans `run_withings_garmin_sync.sh` |
| Erreur auth Garmin | Mauvais credentials | Vérifier `.withings_garmin_sync.env` |
