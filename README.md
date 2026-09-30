# Codex Token Monitor

Double-click **`generate_report.bat`**. It reads the latest Codex usage logs,
creates a timestamped HTML file in this folder, and opens the matching
`latest.html` report in your browser. Nothing needs to run in the background.
Python 3.10 or newer is needed.

The report shows Codex's own five hour and weekly usage percentages, each
window's start and reset time, VS Code token counts, and a table of past five
hour windows. The table also shows cached input; Codex's local token records do
not provide a cached output count, so that part shows `N/A`. Use **K / M** to
switch token numbers between exact counts and
short forms such as `82.2K` or `12.93M`. Each click
updates `data/history.json` with compact usage metadata and generates a new
report. The first run imports the past 30 days and may take a little while if
your logs are large. To import more, run `python app.py --backfill-days 90`.

If you use two accounts, use the Account dropdown in the report to tag each
five hour window. Codex's local log does not include a reliable account ID.
The tags are saved in the browser's local storage for `latest.html` when
available; they do not change the JSON history. Untagged windows stay clearly
marked as unassigned. Keep using `latest.html` for the saved tags; each
timestamped file is a separate frozen snapshot.

There is no fixed number of tokens per five hour window. Model, context, tools,
caching, and usage on other devices affect the allowance. The percentage is
Codex's account-level reading. Token counts cover the local sessions this tool
has observed, so they can be lower than your total account usage. At 100%, the
corresponding row shows the tokens observed locally in that window.

The app reads only usage metadata from `%USERPROFILE%\.codex\sessions` and
`archived_sessions`. It does not store prompts or answers, make network calls,
or need an API key.
