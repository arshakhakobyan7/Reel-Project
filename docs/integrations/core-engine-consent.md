# Core Engine consent API — proposal (T-601)

Status: proposed for agreement with the Core Engine team (D-008). JSON is UTF-8;
responses use `Content-Type: application/json`. Deploy the real service over HTTPS.
The bundled mock binds only to `127.0.0.1` on a free port.

## Request and authentication

`GET {consent.http_base_url}/v1/consent?group=<URL-encoded-reference>[&lesson_date=YYYY-MM-DD]`

A configured base-path prefix is preserved. `group` is a nonempty group name or opaque
Core Engine group id, at most 200 characters. The caller chooses the reference, using
`groups.<name>.core_engine_group_id` when available. `lesson_date` is optional and asks
for consent applicable to that lesson; without it, return current consent. Do not silently
substitute current consent if historical evidence is unavailable: return an unknown
student status or a service error.

```http
GET /v1/consent?group=demo-group-1&lesson_date=2026-11-19
Accept: application/json
Authorization: Bearer <CORE_ENGINE_API_TOKEN>
```

The token is supplied through `cfg.secrets.get("CORE_ENGINE_API_TOKEN")`, populated from
the existing environment loader. No token appears in query strings, logs, decisions or
error messages. Missing/invalid tokens prevent the client from making a request.
The client makes direct requests (environment proxies disabled), verifies TLS with the
standard library's defaults, and rejects redirects so credentials are not forwarded.

## Success response (HTTP 200)

```json
{
  "group": "demo-group-1",
  "group_ref": "demo-group-1",
  "students": [
    {"id": "demo-student-1", "status": "granted"},
    {"id": "demo-student-2", "status": "denied"}
  ]
}
```

`group` must exactly echo the requested reference; `group_ref` is the canonical id.
All three top-level keys are required and no additional keys are accepted. `students`
is an array of objects containing only `id` and optional `status`. Identifiers are
nonempty, trimmed strings of at most 200 characters without control characters;
student ids must be unique within a roster. Never return names, emails or profiles.
Status values are `granted`, `denied`, `unknown`. Missing/null/unrecognised string status
normalises to `unknown`; other status types are malformed. No server approval flag is
accepted: Reel calculates the overall result from every student entry.

| Evidence / response | Reel result |
|---|---|
| Nonempty roster, every student `granted` | `passed` |
| Any `denied`, `unknown`, missing/null/unrecognised status | `opted_out` (D-019) |
| Empty roster | `unknown`; there is no affirmative student evidence |
| HTTP 404 with exactly `{"error":"group_not_found"}` | `unknown` |
| Timeout, connection/TLS failure, HTTP 5xx | `unreachable` |
| HTTP 401/403 | `unreachable`; authentication must be repaired, never approval |
| Other HTTP errors, redirects, malformed JSON/shape, wrong group, non-JSON | `unreachable` |

Other service errors may use `{"error":"invalid_request"}` (400),
`{"error":"unauthorized"}` (401), `{"error":"forbidden"}` (403), or
`{"error":"temporarily_unavailable"}` (503). Reel does not expose their response bodies.
Bodies are limited to 1 MiB; duplicate JSON keys and nonfinite numbers are rejected.
The configured `consent.timeout_seconds` is passed to urllib; retries/holds belong to
T-602. Providers perform no retries, DB writes or publishing themselves.

## Interim file registry

`config/consent.example.json` contains synthetic data only. Copy/adapt it to the local
registry when deploying; T-601 does not create or read the real private registry.
The root object is keyed by group name (Unicode case-insensitive lookup). Each value
contains `students` in the same format and optional `group_ref` (defaults to the group
name). Duplicate/case-colliding groups, duplicate ids, extra fields, invalid JSON or
unreadable/missing files yield `unreachable`. Missing groups yield `unknown`.

The entire file is read and validated on each check, so staff edits take effect immediately.
`lesson_date` is accepted but the interim registry contains current statuses only; use
HTTP when date-specific evidence is needed. Write staff edits atomically to avoid a
partially written registry; such a partial read safely yields `unreachable`.

## Storage, dry-run and mock

T-602 will store provider name, `group_ref`, only student `id`/normalised `status`, overall
`ConsentResult`, a locally generated message, and the DB's UTC `checked_at`. The response
echo and token are not stored. Opaque ids remain sensitive: staff-only access and existing
retention rules apply. No lesson media, transcript, names or emails are sent to this API.

`make_consent_provider(cfg, dry_run=True)` suppresses HTTP, records the intended GET path
in `reel.consent` logging and the decision message, and returns `unreachable` with no
students: a simulation never establishes consent. File checks remain local real checks.
There is no `consent.mode` setting yet; selecting the default `file` provider is offline,
and selecting `http` explicitly enables live checks unless `dry_run=True` is passed.

For offline demos/tests, use `with ConsentMockServer(groups, token="synthetic-token") as server`
from `reel.consent.mock_server`, then `HttpConsentProvider(server.url, token="synthetic-token")`.
The mock supports injected status, delay, raw body, content type and response headers;
it retains only request group/date fields and never logs or stores Authorization headers.
It must not be used as a production consent service.
