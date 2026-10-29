"""
Zero trust gate for fit.bobsabayesian.io.

Two independent checks, both must pass (fail closed):

1. The Cloudflare Access JWT (Cf-Access-Jwt-Assertion) must be validly signed for our
   team domain and application audience, so a request that bypasses the tunnel and
   hits the origin directly is rejected.
2. The identity in that JWT must be listed in access.json (re-read when the file changes).

Enable with CF_ACCESS_ENABLED=true, CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com and
CF_ACCESS_AUD=<application AUD tag>. Optionally CF_ACCESS_ACCESS_FILE and CF_ACCESS_EXEMPT_PATHS.
"""

# Standard Library
import json
import logging
import os
import threading

# Django
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse


logger = logging.getLogger(__name__)

HEADER = 'HTTP_CF_ACCESS_JWT_ASSERTION'

_jwks_clients: dict = {}
_file_cache = {'path': None, 'mtime': None, 'data': None}
_lock = threading.Lock()


def _jwks_client(team_domain: str):
    # Third Party
    import jwt

    if team_domain not in _jwks_clients:
        _jwks_clients[team_domain] = jwt.PyJWKClient(
            f'https://{team_domain}/cdn-cgi/access/certs', cache_keys=True
        )
    return _jwks_clients[team_domain]


def decode_token(token: str, team_domain: str, audience: str) -> dict:
    """Verifies signature, expiry, audience and issuer. Raises jwt.PyJWTError."""
    # Third Party
    import jwt

    key = _jwks_client(team_domain).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        key.key,
        algorithms=['RS256'],
        audience=audience,
        issuer=f'https://{team_domain}',
        options={'require': ['exp', 'iat', 'aud', 'iss']},
    )


def load_access_file(path: str) -> dict:
    """Raises on a missing or malformed file; the caller denies in that case"""
    with _lock:
        mtime = os.stat(path).st_mtime
        if _file_cache['path'] != path or _file_cache['mtime'] != mtime:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('access.json must be an object')
            _file_cache.update(path=path, mtime=mtime, data=data)
        return _file_cache['data']


def identity_allowed(claims: dict, rules: dict) -> bool:
    emails = {e.lower() for e in rules.get('allowed_emails', [])}
    domains = {d.lower().lstrip('@') for d in rules.get('allowed_domains', [])}
    service_tokens = set(rules.get('allowed_service_tokens', []))

    email = (claims.get('email') or '').lower()
    if email:
        return email in emails or email.rpartition('@')[2] in domains
    # Cloudflare service tokens carry no email, only a common_name
    return bool(claims.get('common_name')) and claims['common_name'] in service_tokens


class CloudflareAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, 'CF_ACCESS_ENABLED', False)
        if not self.enabled:
            return
        self.team_domain = getattr(settings, 'CF_ACCESS_TEAM_DOMAIN', '')
        self.audience = getattr(settings, 'CF_ACCESS_AUD', '')
        self.access_file = getattr(settings, 'CF_ACCESS_ACCESS_FILE', '')
        if not (self.team_domain and self.audience and self.access_file):
            raise ImproperlyConfigured(
                'CF_ACCESS_ENABLED requires CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD '
                'and CF_ACCESS_ACCESS_FILE'
            )
        self.exempt = tuple(getattr(settings, 'CF_ACCESS_EXEMPT_PATHS', ()))

    def __call__(self, request):
        if self.enabled and not request.path.startswith(self.exempt or ('\0',)):
            denial = self.check(request)
            if denial:
                return denial
        return self.get_response(request)

    def check(self, request):
        token = request.META.get(HEADER)
        if not token:
            return self.deny('missing Cloudflare Access token')
        try:
            claims = decode_token(token, self.team_domain, self.audience)
        except Exception as e:  # any verification problem is a denial
            return self.deny(f'invalid token: {e.__class__.__name__}')
        try:
            rules = load_access_file(self.access_file)
        except Exception as e:
            logger.error('access file unusable: %s', e)
            return self.deny('access list unavailable')
        if not identity_allowed(claims, rules):
            return self.deny('identity not in access list')
        request.cf_access_claims = claims
        return None

    @staticmethod
    def deny(reason: str):
        logger.warning('Access denied: %s', reason)
        return JsonResponse({'detail': 'Forbidden'}, status=403)
