"""
Doctor Scheduler v2 — single-file Flask app (Render-ready)
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from datetime import date, timedelta, datetime
from typing import List, Dict, Any, Tuple
import io
import csv
import json

from flask import Flask, request, render_template_string, send_file, Response

# Create Flask app first
app = Flask(__name__)

# Helpful error page for quick debugging
@app.errorhandler(Exception)
def handle_exception(e):
    return Response(f"Server error: {e}", status=500)

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
    start = datetime.strptime(payload["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(payload["end_date"], "%Y-%m-%d").date()
    services = list(payload["services"])
    doctors = list(payload["doctors"])
    unavailable = {
        d: set(payload.get("unavailable", {}).get(d, [])) for d in doctors
    }
    return ScheduleConfig(start, end, services, doctors, unavailable)

def iter_workdays(start: date, end: date):
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)

def weekday_initial(d: date) -> str:
    return WEEKDAY_INITIALS[d.weekday()]

def generate_schedule(cfg: ScheduleConfig) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    rotations: Dict[str, deque] = {svc: deque(cfg.doctors) for svc in cfg.services}
    flexible_counts: Dict[str, int] = {d: 0 for d in cfg.doctors}
    rows = []
    for day in iter_workdays(cfg.start_date, cfg.end_date):
        day_str = day.strftime("%Y-%m-%d")
        available_today = {d for d in cfg.doctors if day_str not in cfg.unavailable.get(d, set())}
        assigned_today: Dict[str, str] = {}
        used_doctors = set()
        for svc in cfg.services:
            rot = rotations[svc]
            candidates = [(doc, idx) for idx, doc in enumerate(rot) if doc in available_today and doc not in used_doctors]
            if not candidates:
                assigned_today[svc] = "UNFILLED"
                continue
            candidates.sort(key=lambda t: (-flexible_counts[t[0]], t[1]))
            chosen = candidates[0][0]
            assigned_today[svc] = chosen
            used_doctors.add(chosen)
            while rot[0] != chosen:
                rot.rotate(-1)
            rot.rotate(-1)
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
<body>
  <h1>Doctor Scheduler v2</h1>
  <form method="post" action="/generate">
    <textarea name="payload" rows="12" cols="60">{{example_json}}</textarea>
    <br><button type="submit">Generate schedule</button>
  </form>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html>
<body>
  <h1>Schedule</h1>
  <a href="/">New schedule</a>
  <table border="1">
    <tr>
      <th>Date</th>
      {% for svc in services %}<th>{{svc}}</th>{% endfor %}
      <th>Flexible</th>
    </tr>
    {% for row in rows %}
      <tr>
        <td>{{row.date_label}}</td>
        {% for svc in services %}
          {% set val = row.get(svc, '') %}
          <td>{% if val == 'UNFILLED' %}<b style="color:red;">UNFILLED</b>{% else %}{{val}}{% endif %}</td>
        {% endfor %}
        <td>{{row.Flexible | join(', ')}}</td>
      </tr>
    {% endfor %}
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
        "unavailable": {"Alice": ["2025-07-04"], "Bob": ["2025-07-10", "2025-07-11"]}
    }
    return json.dumps(payload, indent=2)

@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML, example_json=example_payload())

@app.route("/generate", methods=["POST"])
def generate():
    raw = request.form.get("payload", "").strip()
    data = json.loads(raw)
    cfg = parse_config(data)
    rows, flex_counts = generate_schedule(cfg)
    return render_template_string(RESULTS_HTML, rows=rows, services=cfg.services)

# ------------------------------- Entrypoint -------------------------------- #

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
