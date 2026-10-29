# Dynamic Coach

Autoregulated workout generator, Sweat Deck, Tabata timelines and jiu jitsu logging for wger.
API under `/api/v2/coach/` (session, token or JWT auth as in the rest of wger).

| Endpoint | |
|---|---|
| `GET muscle-freshness/` | per-muscle freshness from the 36 h half-life model |
| `POST generate-workout/` | builds a strength session, persists Routine/Day/Slot/SlotEntry + Sets/Repetitions/Weight configs |
| `POST deck/new/`, `deck/<id>/draw/`, `deck/<id>/complete/` | Sweat Deck, completion feeds the fatigue model (20 reps = 1 set) |
| `POST tabata/generate/` | interval timeline |
| `GET/POST/DELETE bjj/` | jiu jitsu sessions; each one adds muscle load (grips, back, core, hips) |
| `equipment-profiles/`, `preferences/` | generator inputs |

## Access (fit.bobsabayesian.io)

Two independent checks, fail closed (`access.py`, middleware inert unless enabled):

1. Cloudflare Access JWT (`Cf-Access-Jwt-Assertion`) verified against the team JWKS, audience and issuer.
2. Identity must be in `access.json` (emails, domains, service-token common names); re-read when the file changes.

```
CF_ACCESS_ENABLED=true
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
CF_ACCESS_AUD=<application AUD tag>
```

The origin must only be reachable through the tunnel/Access; the JWT check is the backstop if it is not.
Map the Access identity to a wger user with the existing `AUTH_PROXY_*` settings.

## Tests

    python manage.py test wger.dynamic_coach --settings=settings.ci
