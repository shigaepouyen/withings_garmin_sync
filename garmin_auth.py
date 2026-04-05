"""
Authentification Garmin Connect via OAuth1/OAuth2.

Même flow que nicolasvegam/garmin-connect-mcp :
- Tokens mis en cache dans ~/.garmin-mcp/
- Rafraîchissement OAuth2 via OAuth1 sans repasser par le SSO
- Login SSO complet uniquement si le token OAuth1 est absent
"""
import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
import uuid
from pathlib import Path

import requests

GARMIN_CONNECT_API = "https://connectapi.garmin.com"
OAUTH_CONSUMER_URL = "https://thegarth.s3.amazonaws.com/oauth_consumer.json"
SSO_EMBED = "https://sso.garmin.com/sso/embed"
SSO_SIGNIN = "https://sso.garmin.com/sso/signin"
SSO_ORIGIN = "https://sso.garmin.com"
OAUTH_PREAUTHORIZED = f"{GARMIN_CONNECT_API}/oauth-service/oauth/preauthorized"
OAUTH_EXCHANGE = f"{GARMIN_CONNECT_API}/oauth-service/oauth/exchange/user/2.0"
USER_AGENT_MOBILE = "com.garmin.android.apps.connectmobile"
USER_AGENT_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

TOKEN_DIR = Path.home() / ".garmin-mcp"
TOKEN_EXPIRY_BUFFER = 60


def _sign_oauth1(method, url, params, consumer_secret, token_secret=""):
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items())
    )
    base = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(sorted_params, safe=""),
    ])
    key = f"{urllib.parse.quote(consumer_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    return base64.b64encode(
        hmac.new(key.encode(), base.encode(), digestmod=hashlib.sha1).digest()
    ).decode()


def _oauth1_query(method, url, consumer, oauth_token=None, oauth_token_secret=None, extra_params=None):
    """Retourne les paramètres OAuth1 signés à passer en query string."""
    params = {
        "oauth_consumer_key": consumer["consumer_key"],
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if oauth_token:
        params["oauth_token"] = oauth_token
    all_params = {**params, **(extra_params or {})}
    params["oauth_signature"] = _sign_oauth1(
        method, url, all_params, consumer["consumer_secret"], oauth_token_secret or ""
    )
    return params


class GarminAuth:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.consumer = None
        self.oauth1_token = None
        self.oauth2_token = None
        self._load_tokens()

    def _load_tokens(self):
        p1 = TOKEN_DIR / "oauth1_token.json"
        p2 = TOKEN_DIR / "oauth2_token.json"
        if p1.exists():
            self.oauth1_token = json.loads(p1.read_text())
        if p2.exists():
            self.oauth2_token = json.loads(p2.read_text())

    def _save_tokens(self):
        TOKEN_DIR.mkdir(exist_ok=True)
        if self.oauth1_token:
            (TOKEN_DIR / "oauth1_token.json").write_text(json.dumps(self.oauth1_token))
        if self.oauth2_token:
            (TOKEN_DIR / "oauth2_token.json").write_text(json.dumps(self.oauth2_token))

    def _is_expired(self):
        if not self.oauth2_token:
            return True
        return time.time() >= self.oauth2_token.get("expires_at", 0) - TOKEN_EXPIRY_BUFFER

    def _fetch_consumer(self):
        if self.consumer:
            return
        r = requests.get(OAUTH_CONSUMER_URL)
        r.raise_for_status()
        self.consumer = r.json()

    def _exchange_oauth1_for_oauth2(self):
        self._fetch_consumer()
        signed = _oauth1_query(
            "POST", OAUTH_EXCHANGE, self.consumer,
            oauth_token=self.oauth1_token["oauth_token"],
            oauth_token_secret=self.oauth1_token["oauth_token_secret"],
        )
        r = requests.post(
            f"{OAUTH_EXCHANGE}?{urllib.parse.urlencode(signed)}",
            headers={
                "User-Agent": USER_AGENT_MOBILE,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r.raise_for_status()
        data = r.json()
        self.oauth2_token = {**data, "expires_at": int(time.time()) + data.get("expires_in", 3600)}
        self._save_tokens()

    def _login_sso(self):
        """Login complet via SSO. Uniquement si le token OAuth1 est absent."""
        self._fetch_consumer()
        session = requests.Session()

        session.get(SSO_EMBED, params={"clientId": "GarminConnect", "locale": "en", "service": SSO_EMBED},
                    headers={"User-Agent": USER_AGENT_BROWSER})

        signin_params = {"id": "gauth-widget", "embedWidget": "true", "locale": "en", "gauthHost": SSO_EMBED}
        r = session.get(SSO_SIGNIN, params=signin_params, headers={"User-Agent": USER_AGENT_BROWSER})
        csrf_match = re.search(r'name="_csrf"\s+value="(.+?)"', r.text)
        if not csrf_match:
            raise RuntimeError("Token CSRF introuvable dans la page SSO Garmin")

        r = session.post(
            SSO_SIGNIN,
            data=urllib.parse.urlencode({
                "username": self.email, "password": self.password,
                "embed": "true", "_csrf": csrf_match.group(1),
            }),
            params={**signin_params, "clientId": "GarminConnect", "service": SSO_EMBED,
                    "source": SSO_EMBED, "redirectAfterAccountLoginUrl": SSO_EMBED,
                    "redirectAfterAccountCreationUrl": SSO_EMBED},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT_BROWSER,
                "Origin": SSO_ORIGIN, "Referer": SSO_SIGNIN, "Dnt": "1",
            },
        )
        ticket_match = re.search(r'ticket=([^"]+)"', r.text)
        if not ticket_match:
            raise RuntimeError("Login SSO échoué : credentials invalides ou MFA requis")
        ticket = ticket_match.group(1)

        extra = {"ticket": ticket, "login-url": SSO_EMBED, "accepts-mfa-tokens": "true"}
        signed = _oauth1_query("GET", OAUTH_PREAUTHORIZED, self.consumer, extra_params=extra)
        url = f"{OAUTH_PREAUTHORIZED}?{urllib.parse.urlencode({**extra})}"
        r = requests.get(
            url,
            headers={
                "Authorization": "OAuth " + ", ".join(f'{k}="{urllib.parse.quote(str(v), safe="")}"' for k, v in signed.items()),
                "User-Agent": USER_AGENT_MOBILE,
            },
        )
        params = dict(urllib.parse.parse_qsl(r.text))
        if not params.get("oauth_token"):
            raise RuntimeError("Impossible d'obtenir le token OAuth1 depuis Garmin")
        self.oauth1_token = {
            "oauth_token": params["oauth_token"],
            "oauth_token_secret": params["oauth_token_secret"],
        }
        self._save_tokens()
        self._exchange_oauth1_for_oauth2()

    def ensure_authenticated(self):
        if not self._is_expired():
            return
        if self.oauth1_token:
            try:
                self._exchange_oauth1_for_oauth2()
                return
            except Exception:
                pass
        self._login_sso()

    def request(self, method, endpoint, **kwargs):
        self.ensure_authenticated()
        url = endpoint if endpoint.startswith("http") else f"{GARMIN_CONNECT_API}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.oauth2_token['access_token']}",
            "User-Agent": USER_AGENT_MOBILE,
            **kwargs.pop("headers", {}),
        }
        r = requests.request(method, url, headers=headers, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}

    def get_weigh_ins(self, start_date, end_date):
        return self.request("GET", "/weight-service/weight/dateRange",
                            params={"startDate": start_date, "endDate": end_date})

    def add_weigh_in(self, weight_kg, unit_key, date_str):
        self.request("POST", "/weight-service/user-weight", json={
            "dateTimestamp": f"{date_str}T00:00:00.0",
            "gmtTimestamp": f"{date_str}T00:00:00.0",
            "unitKey": unit_key,
            "value": weight_kg,
        })
