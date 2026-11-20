# Reel integration API (v1)

This is a read-only JSON API for the Core Engine and Academy Library teams. With it you can
read Reel's recordings and its interim library without access to Reel's database
(T-806, D-008, D-053).

## Access

- **Base address:** the dashboard's address, for example `http://127.0.0.1:8080`. Use HTTPS
  when Reel is reached over a network.
- **Authentication:** every request needs the header `Authorization: Bearer <token>`.
  - The academy's Reel administrator creates the tokens and puts them, comma-separated, in
    `REEL_API_TOKENS` in Reel's `.env`.
  - A token must have at least 24 characters. Generate one with
    `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **Errors:** every error is JSON: `{"error": "<what is wrong>"}`.

| Status | When |
|---|---|
| 400 | A query value is invalid. |
| 401 | The token is missing or wrong. The answer has `WWW-Authenticate: Bearer`. |
| 404 | The recording does not exist. |
| 503 | The API is off: no token is configured. |

- **Privacy:** the API never returns transcripts, consent details about students, or files.
  It returns publication links only for real publications. A dry-run preview shows
  `"dry_run": true` and `"url": null`.
- **Times** are ISO 8601 in UTC. **Durations** are in seconds.

## Paging

The list endpoints return up to `limit` items (default 50, at most 200), sorted by `id`
ascending, together with `next_after`:

```json
{"items": [...], "next_after": 120}
```

- If `next_after` is a number, ask again with `after=<next_after>` to get the next page.
- If it is `null`, there are no more items.
- To sync regularly, keep the time of your last sync and pass it as `updated_since`.

## `GET /api/v1/recordings`

Query (all optional):

| Name | Meaning |
|---|---|
| `status` | Only this status: `queued`, `running`, `on_hold`, `failed`, `done` or `cancelled`. |
| `group` | Only this group, by its name in Reel's `groups.toml`. |
| `updated_since` | Only recordings changed at or after this time. The time must include a time zone. |
| `after`, `limit` | Paging (see above). |

Every item:

```json
{
  "id": 12,
  "title": "Python 12 · Դաս 14 · Բառարաններ",
  "group": "Python 12",
  "lesson_number": 14,
  "recording_date": "2026-11-19",
  "status": "done",
  "stage": "post",
  "original_duration_seconds": 9672.0,
  "final_duration_seconds": 6723.0,
  "youtube_url": "https://www.youtube.com/watch?v=…",
  "flags": [],
  "created_at": "2026-11-19T15:02:11+00:00",
  "updated_at": "2026-11-19T17:40:03+00:00",
  "finished_at": "2026-11-19T17:40:03+00:00"
}
```

- `id` is the recording's id in Reel (its job id).
- `stage` is the pipeline stage the recording is at: `import`, `transcribe`, `clean`,
  `chapters`, `consent`, `upload` or `post`.
- `title` is `null` until the title is made, and `youtube_url` is `null` until the video is
  really uploaded.

## `GET /api/v1/recordings/{id}`

This returns the list item plus:

```json
{
  "summary": "Բառարաններ, live coding",
  "description": "…the YouTube description…",
  "chapters": [{"start_seconds": 0.0, "title": "Կրկնություն"}],
  "removed_seconds": 2949.0,
  "consent": {"result": "passed", "checked_at": "…", "released_by_staff": false},
  "publications": {
    "youtube":   {"status": "published", "dry_run": false, "url": "…", "published_at": "…"},
    "slack":     {"status": "published", "dry_run": false, "url": "…", "published_at": "…"},
    "classroom": {"status": "skipped",   "dry_run": false, "url": null, "published_at": null},
    "library":   {"status": "published", "dry_run": false, "url": null, "published_at": "…"}
  }
}
```

- `chapters` start on the timeline of the cleaned video, which is the published one.
- `consent.result` is one of `passed`, `opted_out`, `unknown` or `unreachable`. It is `null`
  before the consent check.
- `released_by_staff` is `true` when a staff member decided to publish despite the result.
- A publication `status` is one of `pending`, `in_progress`, `published`, `failed` or
  `skipped`. `skipped` means the group has no Slack channel or Classroom course. A target
  that has not been tried yet is missing from `publications`.

## `GET /api/v1/library`

This lists Reel's interim library catalogue: one entry per published lesson (FR-28). When
the Academy Library product is ready, Reel writes to it instead
(`docs/integrations/academy-library.md`).

Query: `group`, `updated_since`, `after`, `limit`.

```json
{
  "id": 3,
  "recording_id": 12,
  "title": "Python 12 · Դաս 14 · Բառարաններ",
  "video_url": "https://www.youtube.com/watch?v=…",
  "group": "Python 12",
  "lesson_number": 14,
  "recording_date": "2026-11-19",
  "chapters": [{"start_seconds": 0.0, "title": "Կրկնություն"}],
  "updated_at": "2026-11-19T17:40:03+00:00"
}
```

## Changes

- New fields can be added to v1 at any time, so clients must ignore fields they do not know.
- Removing or renaming a field needs a new version, `/api/v2`.
