# Gecko

Gecko is a local-first work productivity agent. Its browser UI and command-line
controls use one canonical store at `data/gecko.json`; `today.md`, `backlog.md`,
the archive, and `this-week.md` are generated mirrors.

## Share And Start

Gecko is designed to be shared as a private source repository or ZIP file.
Each person gets a separate local data store; no tasks, calendar events, AFK
records, logs, or screenshots are included in the project.

### macOS quick start

1. Download or clone the project.
2. Double-click `setup.command` once.
3. Double-click `start.command` whenever you want to use Gecko.

Gecko opens at `http://127.0.0.1:4173`. Closing the Terminal window that
launched it stops the local service. Python 3 is the only prerequisite.

The supported recipient path is macOS. The included launchers and optional AFK
collection use macOS tools; Windows/Linux support would require a small port.

## Manual Run

For a manual run:

```bash
cd /Users/amalrj/frog-focus
python3 gecko_server.py
```

Gecko binds only to `localhost`; its stored tasks, calendar details, AFK records,
archives, logs, and screenshots remain under `data/` and are excluded from
source control.

## Agent Commands

```bash
python3 gecko_agent.py morning
python3 gecko_agent.py add "Draft decision memo" --type strategic --minutes 60
python3 gecko_agent.py email "Respond to review request" --type admin --from "Name"
python3 gecko_agent.py calendar-import verified-events.json
python3 gecko_agent.py weekly-reset
```

## Data Rules

- Gecko creates no seed tasks, history, meetings, or productivity metrics.
- Open actions roll into the next day; completed actions stay with their day.
- Calendar JSON is read-only input. Gecko does not write to Outlook.
- Email capture stores subject/source metadata locally. It does not access or
  send mail.
- AFK is the union of macOS lock intervals and system idle intervals over two
  minutes, clipped to the configured 9 AM–5 PM workday.

## Calendar JSON

```json
[
  {
    "id": "outlook-event-id",
    "title": "Meeting title",
    "start": "2026-09-01T13:00:00-07:00",
    "end": "2026-09-01T13:30:00-07:00",
    "status": "busy",
    "source": "outlook"
  }
]
```

## Test

```bash
python3 -m unittest discover -s tests -v
```
