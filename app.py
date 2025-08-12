"""
Doctor Scheduler v2 — single-file Flask app

Features implemented (per prior requirements):
- Weekdays only (Mon–Fri).
- Respect per-doctor unavailability (date strings "YYYY-MM-DD").
- Round-robin service assignment without repeating the same doctor for a service until all have been assigned (deques).
- Balance flexible (unassigned) days across doctors: when choosing among eligible assignees, prefer those with *more* prior flexible days.
- Day label includes the first letter of the weekday (e.g., Mon -> "M").
- Simple web UI: paste JSON input, generate schedule, view table, download CSV/JSON.

Input JSON schema (example below):
{
  "start_date": "2025-06-30",           # inclusive
  "end_date": "2025-07-31",             # inclusive
  "services": ["CT", "US", "MR"],     # list of service names (>=1)
  "doctors": ["Alice", "Bob", "Carol", "Dan"],
  "unavailable": {                        # optional
    "Alice": ["2025-07-04"],
    "Bob":   ["2025-07-10", "2025-07-11"]
  }
}

Assumptions:
- Each service requires exactly one doctor per working day.
- A doctor cannot cover more than one service on the same day.
- If there are not enough available doctors for a given day, a service may be left "UNFILLED".

Run locally:
  python app.py

Then open http://127.0.0.1:5000

Deploying to Render: this single file works with a simple Gunicorn start command:
  gunicorn app:app

"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque, defaultdict
from datetime import date, timedelta, datetime
from typing import List, Dict, Any, Tuple
import io
import csv
import json

from flask import Flask, request, render_template_string, send_file, Response

# Helpful error page for quick debugging
@app.errorhandler(Exception)
def handle_exception(e):
    # In debug, Flask shows the stack trace; this ensures a readable message otherwise
    return Response(f"Server error: {e}", status=500)


app = Flask(__name__)

# -------------------------- Core scheduling engine -------------------------- #

WEEKDAY_INITIALS = ["M", "T", "W", "T", "F", "S", "S"]

@dataclass
class ScheduleConfig:
    start_date: date
    end_date: date
    services: List[str]
    doctors: List[str]
    unavailable: Dict[str, set]


def parse_config(payload: Dict[str, Any]) -> ScheduleConfig:
    try:
        start = datetime.strptime(payload["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(payload["end_date"], "%Y-%m-%d").date()
        if end < start:
            raise ValueError("end_date must be on/after start_date")
        services = list(payload["services"])  # keep order
        doctors = list(payload["doctors"])    # keep order
        unavailable = {
            d: set(payload.get("unavailable", {}).get(d, [])) for d in doctors
        }
    except KeyError as e:
        raise ValueError(f"Missing required key: {e}")
    except Exception as e:
        raise ValueError(str(e))
    if not services:
        raise ValueError("At least one service is required")
    if not doctors:
        raise ValueError("At least one doctor is required")
    return ScheduleConfig(start, end, services, doctors, unavailable)


def iter_workdays(start: date, end: date):
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon=0..Fri=4
            yield cur
        cur += timedelta(days=1)


def weekday_initial(d: date) -> str:
    return WEEKDAY_INITIALS[d.weekday()]


def generate_schedule(cfg: ScheduleConfig) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Return (rows, flexible_counts)
    rows: list of {"date_label": "2025-06-30 (M)", service: doctor, ..., "Flexible": [names]}
    flexible_counts: per-doctor flexible day totals across the schedule window
    """
    # Rotation deques: one per service; initial order is cfg.doctors
    rotations: Dict[str, deque] = {svc: deque(cfg.doctors) for svc in cfg.services}

    # Track flexible counts for balancing
    flexible_counts: Dict[str, int] = {d: 0 for d in cfg.doctors}

    rows = []
    for day in iter_workdays(cfg.start_date, cfg.end_date):
        day_str = day.strftime("%Y-%m-%d")
        available_today = {d for d in cfg.doctors if day_str not in cfg.unavailable.get(d, set())}

        assigned_today: Dict[str, str] = {}
        used_doctors = set()

        # For each service, pick the next eligible doctor.
        for svc in cfg.services:
            # Build list of candidates in rotation order for fairness, but when multiple
            # are available, prefer those with *higher* flexible_counts to rebalance.
            # We do this by sorting candidates by (-flexible_count, rotation index).
            rot = rotations[svc]
            candidates = []
            for idx, doc in enumerate(rot):
                if doc in available_today and doc not in used_doctors:
                    candidates.append((doc, idx))
            if not candidates:
                assigned_today[svc] = "UNFILLED"
                continue

            # Choose candidate: highest flexible count, tie-breaker: nearest in rotation
            candidates.sort(key=lambda t: (-flexible_counts[t[0]], t[1]))
            chosen = candidates[0][0]
            assigned_today[svc] = chosen
            used_doctors.add(chosen)

            # Rotate this service's deque so that chosen moves to the end, preserving RR
            while rot[0] != chosen:
                rot.rotate(-1)
            rot.rotate(-1)

        # Flexible doctors are those available but not used
        flexible_today = sorted(list(available_today - used_doctors))
        for doc in flexible_today:
            flexible_counts[doc] += 1

        row = {"date": day.strftime("%Y-%m-%d"),
               "date_label": f"{day.strftime('%Y-%m-%d')} ({weekday_initial(day)})"}
        row.update(assigned_today)
        row["Flexible"] = flexible_today
        rows.append(row)

    return rows, flexible_counts


# ------------------------------ Web UI Layer ------------------------------- #

INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Doctor Scheduler v2</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    textarea { width: 100%; height: 14rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
    .row { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    .btn { padding: 0.6rem 1rem; border-radius: 10px; border: 1px solid #ddd; background: white; cursor: pointer; }
    .btn:hover { background: #f5f5f5; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: 0.5rem; vertical-align: top; }
    th { background: #fafafa; position: sticky; top: 0; }
    .pill { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 999px; background: #f0f0f0; margin: 0.1rem; }
    .unfilled { color: #b00020; font-weight: 600; }
    .summary { margin: 1rem 0; padding: 0.75rem; background: #f8f9fa; border: 1px solid #eee; border-radius: 10px; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h1>Doctor Scheduler v2</h1>
  <p class="muted">Weekdays-only · Unavailability-aware · Balanced flexible days · Round-robin per service</p>

  <form method="post" action="/generate">
    <label for="payload"><strong>Paste input JSON</strong> (see example below):</label>
    <textarea id="payload" name="payload">{{example_json}}</textarea>
    <div class="row">
      <button class="btn" type="submit">Generate schedule</button>
    </div>
  </form>

  <details style="margin-top:1rem;">
    <summary><strong>Example JSON</strong></summary>
    <pre>{{example_json}}</pre>
  </details>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Schedule Results</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: 0.5rem; vertical-align: top; }
    th { background: #fafafa; position: sticky; top: 0; }
    .pill { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 999px; background: #f0f0f0; margin: 0.1rem; }
    .unfilled { color: #b00020; font-weight: 600; }
    .row { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    .btn { padding: 0.6rem 1rem; border-radius: 10px; border: 1px solid #ddd; background: white; cursor: pointer; }
    .btn:hover { background: #f5f5f5; }
    .summary { margin: 1rem 0; padding: 0.75rem; background: #f8f9fa; border: 1px solid #eee; border-radius: 10px; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h1>Schedule</h1>
  <p class="muted">From {{start_date}} to {{end_date}} (weekdays only)</p>

  <div class="row">
    <form method="post" action="/download/csv">
      <input type="hidden" name="payload" value='{{raw_payload}}' />
      <button class="btn" type="submit">Download CSV</button>
    </form>
    <form method="post" action="/download/json">
      <input type="hidden" name="payload" value='{{raw_payload}}' />
      <button class="btn" type="submit">Download JSON</button>
    </form>
    <a class="btn" href="/">↩︎ New schedule</a>
  </div>

  <div class="summary">
    <strong>Flexible day counts</strong> (lower is more assigned, higher is more flexible):
    <ul>
    {% for doc, cnt in flex_summary %}
      <li><strong>{{doc}}</strong>: {{cnt}}</li>
    {% endfor %}
    </ul>
  </div>

  <table>
    <thead>
      <tr>
        <th>Date</th>
        {% for svc in services %}<th>{{svc}}</th>{% endfor %}
        <th>Flexible</th>
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
        <tr>
          <td>{{row.date_label}}</td>
          {% for svc in services %}
            {% set val = row.get(svc, '') %}
            <td>{% if val == 'UNFILLED' %}<span class="unfilled">UNFILLED</span>{% else %}{{val}}{% endif %}</td>
          {% endfor %}
          <td>
            {% for name in row.Flexible %}<span class="pill">{{name}}</span>{% endfor %}
          </td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""


def example_payload() -> str:
    payload = {
        "start_date": "2025-06-30",
        "end_date": "2025-07-11",
        "services": ["CT", "US"],
        "doctors": ["Alice", "Bob", "Carol", "Dan"],
        "unavailable": {
            "Alice": ["2025-07-04"],
            "Bob":   ["2025-07-10", "2025-07-11"]
        }
    }
    return json.dumps(payload, indent=2)


@app.route("/", methods=["GET"]) 
def index():
    return render_template_string(INDEX_HTML, example_json=example_payload())


@app.route("/generate", methods=["POST"]) 
def generate():
    raw = request.form.get("payload", "").strip()
    try:
        data = json.loads(raw)
        cfg = parse_config(data)
        rows, flex_counts = generate_schedule(cfg)
    except Exception as e:
        return Response(f"Error: {e}", status=400)

    # Prepare display
    payload_clean = {
        "start_date": cfg.start_date.strftime("%Y-%m-%d"),
        "end_date": cfg.end_date.strftime("%Y-%m-%d"),
        "services": cfg.services,
        "doctors": cfg.doctors,
        "unavailable": {k: sorted(list(v)) for k, v in cfg.unavailable.items() if v}
    }

    flex_summary = sorted(flex_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    return render_template_string(
        RESULTS_HTML,
        rows=rows,
        services=cfg.services,
        start_date=payload_clean["start_date"],
        end_date=payload_clean["end_date"],
        flex_summary=flex_summary,
        raw_payload=json.dumps(payload_clean),
    )


# --------------------------- Downloads (CSV/JSON) -------------------------- #

@app.route("/download/csv", methods=["POST"]) 
def download_csv():
    raw = request.form.get("payload", "")
    data = json.loads(raw)
    cfg = parse_config(data)
    rows, _ = generate_schedule(cfg)

    output = io.StringIO()
    writer = csv.writer(output)
    header = ["date", *cfg.services, "Flexible"]
    writer.writerow(header)
    for r in rows:
        writer.writerow([r["date"], *[r.get(svc, "") for svc in cfg.services], ", ".join(r["Flexible"])])

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    fn = f"schedule_{cfg.start_date}_to_{cfg.end_date}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=fn)


@app.route("/download/json", methods=["POST"]) 
def download_json():
    raw = request.form.get("payload", "")
    data = json.loads(raw)
    cfg = parse_config(data)
    rows, flex_counts = generate_schedule(cfg)
    blob = {
        "meta": {
            "start_date": cfg.start_date.strftime("%Y-%m-%d"),
            "end_date": cfg.end_date.strftime("%Y-%m-%d"),
            "services": cfg.services,
            "doctors": cfg.doctors,
        },
        "rows": rows,
        "flexible_counts": flex_counts,
    }
    mem = io.BytesIO(json.dumps(blob, indent=2).encode("utf-8"))
    mem.seek(0)
    fn = f"schedule_{cfg.start_date}_to_{cfg.end_date}.json"
    return send_file(mem, mimetype="application/json", as_attachment=True, download_name=fn)


# ------------------------------- Entrypoint -------------------------------- #

if __name__ == "__main__":
    app.run(debug=True)
