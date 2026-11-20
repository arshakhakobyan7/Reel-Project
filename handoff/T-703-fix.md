# T-703 fix — Slack transport and channel IDs (review by Claude)

Follow `handoff/README.md`. T-703 was reviewed and is accepted except for two changes.
Your files: `src/reel/publish/slack.py`, `tests/unit/test_slack.py` (yours from T-703).

1. **Transport (you were right; the T-703 prompt was wrong).** Keep JSON bodies for
   `chat.postMessage` and `chat.update`. Send `chat.getPermalink` and `conversations.history`
   as `application/x-www-form-urlencoded` POST bodies (booleans as `true`/`false`, numbers as
   text), still with the bearer token in the header. Update the fake/loopback servers in the
   tests to decode both body types and keep every existing assertion meaningful; add one test
   that checks the content type used per method.
2. **Channel IDs.** `groups.toml` must hold the Slack channel **ID**. Before any call, reject a
   channel that is not an ID (`^[CGD][A-Z0-9]{8,}$`, e.g. `C0123456789`) with a permanent
   `SlackError("invalid_channel", ...)` whose message says to use the channel ID, not the name
   (e.g. `#python-12`). Test it, including that no HTTP call is made.

Run the checks from `handoff/README.md` and append a short "Fix" section to
`handoff/T-703.report.md` (what changed, test counts, real command output).
