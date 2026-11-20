# Academy Library API — proposal (T-705)

Proposed for agreement with the Library team (D-008). All JSON is UTF-8. The real service
uses HTTPS. Reel makes direct requests, rejects redirects and never puts tokens in URLs.

## Idempotent publication

`PUT {library.http_base_url}/entries/{external_key}`

`external_key = reel-{database.instance_id as 32 lowercase UUID hex digits}-job-{job_id}`.
The database generates instance_id once at initialisation; it is never regenerated on a
retry or process restart. Each key denotes exactly one entry. The service must atomically
create-or-replace by key, preserve its entry id on updates, and retain the key mapping.
Duplicate/repeated requests are updates, including retry after a committed request whose
response was lost. Different database UUIDs isolate identical job numbers.

```http
Authorization: Bearer <LIBRARY_API_TOKEN>
Content-Type: application/json; charset=utf-8
Accept: application/json
```

```json
{
  "title": "Python 12 · Դաս 7",
  "video_url": "https://youtu.be/example7",
  "group": "Python 12",
  "lesson_number": 7,
  "recording_date": "2026-11-19",
  "chapters": [{"start_seconds": 0.0, "title": "Ներածություն"}]
}
```

All six keys are required; no extra keys. Nonempty title/group/chapter titles are at most
200 characters; video_url is an HTTP(S) URL of at most 1024 characters, with no credentials
or control/space characters. lesson_number is a nonnegative integer or null; recording_date
is a YYYY-MM-DD string or null. chapters is an array of exactly start_seconds/title objects,
with finite nonnegative, strictly increasing numeric starts; an empty array is allowed.
Reel sends publication metadata only, no lesson media, transcript or student records.

## Response

201 creates; 200 updates. The body must have exactly these five fields, and item must
echo the validated request. The canonical resource URL belongs to the Library service.

```json
{
  "external_key": "reel-0123456789abcdef0123456789abcdef-job-7",
  "id": "entry-17",
  "url": "https://library.example/entries/entry-17",
  "created": true,
  "item": {
    "title": "Python 12 · Դաս 7",
    "video_url": "https://youtu.be/example7",
    "group": "Python 12",
    "lesson_number": 7,
    "recording_date": "2026-11-19",
    "chapters": [{"start_seconds": 0.0, "title": "Ներածություն"}]
  }
}
```

id is a nonempty, trimmed string up to 200 characters. url is an HTTP(S) URL. external_key
must match the requested key. created must be boolean true for 201, false for 200. JSON
bodies are capped at 1 MiB; duplicate keys, nonfinite numbers, wrong types/keys/identity,
unexpected success status and mismatched echoed metadata are invalid responses.

| Status/failure | Reel behavior |
|---|---|
| 200 / 201 valid response | Return the entry id, URL, created/updated |
| 408, 429, 5xx, connection or socket timeout | RetryableLibraryError; same key next time |
| Other 4xx, authentication 401/403, rejected redirects | PermanentLibraryError |
| Malformed/unsupported response | RetryableLibraryError; no fake success |

Error JSON may use `{"error":"invalid_item"}` (400), `unauthorized` (401), `forbidden` (403),
`ratelimited` (429), or `temporarily_unavailable` (503). Reel never exposes error bodies or
raw exceptions. Retry-After, when supplied as finite nonnegative seconds, is returned on
the exception. The configured timeout is the urllib socket timeout. The stage owns retry
scheduling; this client makes one attempt per publish call. Credentials come from the
existing cfg.secrets LIBRARY_API_TOKEN, never from item metadata.

## Interim storage and offline simulation

The local adapter upserts LibraryEntry by job_id, preserving row id/created_at and moving
updated_at on every re-run. It maps group to group_name and stores chapters with
start_seconds. It returns url=null: Reel's library dashboard/API is a later task, and this
adapter does not invent a working URL. Use upsert_library_entry(session, job_id, item)
inside a stage transaction; it flushes but never commits, rolls back or closes the session.
LocalLibraryPublisher(db).publish(...) is the separate-transaction convenience.

make_library_publisher(cfg, db, dry_run=True) previews either adapter with payload and
created=false, updated=false, entry_id=null, url=null, and no writes/HTTP. The HTTP preview
still supplies the stable external_key. LibrarySettings currently has no mode field, so
dry_run is explicit; the default provider is local and has no external side effects.

For offline tests/demos use `with LibraryMockServer(token="synthetic-token") as server`
from reel.library.mock_server. It binds only 127.0.0.1:0, implements the PUT contract, keeps
one in-memory entry per key and can inject timeouts/status/body faults or drop a response
after commit. It stores no Authorization headers. It is not the production Library API.
