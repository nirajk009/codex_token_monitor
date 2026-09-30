"""Local Codex usage dashboard. Reads only token metadata from Codex session logs."""

import json
import os
import argparse
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data" / "history.json"
CODEX_DIR = Path.home() / ".codex"


def utc_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


class UsageStore:
    def __init__(self, codex_dir=CODEX_DIR, data_file=DATA_FILE, backfill_days=30):
        self.codex_dir = Path(codex_dir)
        self.data_file = Path(data_file)
        self.backfill_days = backfill_days
        self.lock = threading.Lock()
        self.data = {"version": 1, "files": {}, "events": []}
        if self.data_file.exists():
            try:
                saved = json.loads(self.data_file.read_text(encoding="utf-8"))
                if saved.get("version") == 1:
                    self.data = saved
            except (OSError, ValueError):
                print("Could not read history.json; starting a new history.")

    def session_files(self):
        cutoff = (datetime.now().date() - timedelta(days=self.backfill_days)).isoformat()
        for folder in ("sessions", "archived_sessions"):
            root = self.codex_dir / folder
            if not root.exists():
                continue
            for path in root.rglob("*.jsonl"):
                # Rollout filenames contain the session creation date.
                if path.name.startswith("rollout-") and path.name[8:18] >= cutoff:
                    yield path

    def scan(self):
        with self.lock:
            changed = False
            for path in self.session_files():
                if self.scan_file(path):
                    changed = True
            if changed:
                self.data["events"].sort(key=lambda event: event["time"])
                save_json(self.data_file, self.data)
            return changed

    def scan_file(self, path):
        key = str(path)
        state = self.data["files"].setdefault(key, {"offset": 0, "total": 0, "source": "unknown"})
        size = path.stat().st_size
        if size < state["offset"]:
            # A replaced log is unusual. Reset its cursor so new events are visible.
            state.update({"offset": 0, "total": 0, "source": "unknown"})
        if size == state["offset"]:
            return False
        old_offset = state["offset"]
        with path.open("rb") as stream:
            stream.seek(old_offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    stream.seek(start)
                    break
                if b'"session_meta"' not in line and b'"token_count"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                payload = record.get("payload") or {}
                if record.get("type") == "session_meta":
                    state["source"] = payload.get("originator") or "unknown"
                if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                self.add_token_event(record, payload, state, path.name)
            state["offset"] = stream.tell()
        return state["offset"] != old_offset

    def add_token_event(self, record, payload, state, filename):
        time_value = record.get("timestamp")
        if utc_time(time_value) is None:
            return
        info = payload.get("info") or {}
        totals = info.get("total_token_usage") or {}
        last = info.get("last_token_usage") or {}
        total = totals.get("total_tokens")
        if not isinstance(total, int):
            return
        previous = state["total"]
        # Total usage is cumulative within one session. The difference avoids
        # counting repeated snapshots twice. The first event uses its last usage.
        if previous == 0:
            tokens = max(0, int(last.get("total_tokens") or 0))
            input_tokens = max(0, int(last.get("input_tokens") or 0))
            cached_tokens = max(0, int(last.get("cached_input_tokens") or 0))
            output_tokens = max(0, int(last.get("output_tokens") or 0))
        else:
            tokens = max(0, total - previous)
            input_tokens = max(0, int(totals.get("input_tokens") or 0) - int(state.get("input", 0)))
            cached_tokens = max(0, int(totals.get("cached_input_tokens") or 0) - int(state.get("cached", 0)))
            output_tokens = max(0, int(totals.get("output_tokens") or 0) - int(state.get("output", 0)))
        state["total"] = total
        state["input"] = int(totals.get("input_tokens") or 0)
        state["cached"] = int(totals.get("cached_input_tokens") or 0)
        state["output"] = int(totals.get("output_tokens") or 0)
        rate = payload.get("rate_limits") or {}
        primary = rate.get("primary") or {}
        secondary = rate.get("secondary") or {}
        if tokens == 0 and primary.get("used_percent") is None:
            return
        self.data["events"].append({
            "time": time_value,
            "session": filename,
            "source": state["source"],
            "tokens": tokens,
            "input": input_tokens,
            "cached": cached_tokens,
            "output": output_tokens,
            "used_percent": primary.get("used_percent"),
            "reset_at": primary.get("resets_at"),
            "window_minutes": primary.get("window_minutes"),
            "weekly_percent": secondary.get("used_percent"),
            "weekly_reset_at": secondary.get("resets_at"),
        })

    def snapshot(self):
        with self.lock:
            events = self.data["events"]
            now = datetime.now(timezone.utc).timestamp()
            recent_rates = [event for event in events
                            if event.get("window_minutes") == 300
                            and isinstance(event.get("reset_at"), int)]
            latest = recent_rates[-1] if recent_rates else None
            current = latest if latest and latest["reset_at"] > now else None
            windows = {}
            for event in events:
                reset = event.get("reset_at")
                if event.get("window_minutes") != 300 or not isinstance(reset, int):
                    continue
                # Codex can report the same reset a second apart in separate
                # sessions. Group those readings into one display window.
                bucket = round(reset / 60) * 60
                window = windows.setdefault(bucket, {
                    "start_at": bucket - 300 * 60,
                    "reset_at": bucket, "first_seen": event["time"],
                    "last_seen": event["time"], "peak_percent": 0,
                    "tokens": 0, "vscode_tokens": 0, "input": 0,
                    "cached": 0, "output": 0, "events": 0,
                    "used_percent": None, "weekly_percent": None,
                    "weekly_reset_at": None,
                })
                window["last_seen"] = event["time"]
                window["peak_percent"] = max(window["peak_percent"], event.get("used_percent") or 0)
                if event.get("used_percent") is not None:
                    window["used_percent"] = event["used_percent"]
                    window["weekly_percent"] = event.get("weekly_percent")
                    window["weekly_reset_at"] = event.get("weekly_reset_at")
                for field in ("tokens", "input", "cached", "output"):
                    window[field] += event[field]
                if event["source"] == "codex_vscode":
                    window["vscode_tokens"] += event["tokens"]
                if event["tokens"]:
                    window["events"] += 1
            history = sorted(windows.values(), key=lambda window: window["reset_at"], reverse=True)
            active = [window for window in history if window["reset_at"] > now]
            return {
                "now": datetime.now(timezone.utc).isoformat(),
                "current": current,
                "latest": latest,
                "current_window": windows.get(round(current["reset_at"] / 60) * 60) if current else None,
                "active_windows": active,
                "windows": history[:100],
                "tracked_events": len(events),
                "tracked_files": len(self.data["files"]),
                "codex_dir_found": self.codex_dir.exists(),
            }

def main():
    parser = argparse.ArgumentParser(description="Generate a Codex usage HTML report")
    parser.add_argument("--backfill-days", type=int, default=30)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    store = UsageStore(backfill_days=args.backfill_days)
    print("Reading Codex session logs...", flush=True)
    store.scan()
    report = store.snapshot()
    template = (APP_DIR / "dashboard.html").read_text(encoding="utf-8")
    page = template.replace("__REPORT_DATA__", json.dumps(report))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
    output = APP_DIR / f"report-{stamp}.html"
    output.write_text(page, encoding="utf-8")
    latest = APP_DIR / "latest.html"
    latest.write_text(page, encoding="utf-8")
    save_json(APP_DIR / "data" / "events.json", store.data["events"])
    print(f"Timestamped report: {output}")
    print(f"Latest report: {latest}")
    print(f"Five hour windows: {len(report['windows'])}")
    if not args.no_browser:
        webbrowser.open(latest.as_uri())


if __name__ == "__main__":
    main()
