"""
Doctor Scheduler v4 — single-file Flask app
Two-stage input flow:
  1) Dates + services + doctors
  2) Doctor unavailability (checkbox grid by date)
Console & Render ready
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from datetime import date, timedelta, datetime
from typing import List, Dict, Any, Tuple
import json
import os
import html

from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

WEEKDAY_INITIALS = ["M", "T", "W", "T", "F", "S", "S"]

# -------------------------- Models & Parsing -------------------------- #
@dataclass
class ScheduleConfig:
    start_date: date
    end_date: date
    services: List[str]
    doctors: List[str]
    unavailable: Dict[str, set]

def iter_workdays(start: date, end: date):
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)

def weekday_initial(d: date) -> str:
    return WEEKDAY_INITIALS[d.weekday()]

def parse_basic(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse stage-1 payload (start/end, services, doctors)."""
    start = datetime.strptime(payload["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(payload["end_date"], "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on/after start_date")
    services = [s.strip() for s in payload["services"] if s.strip()]
    doctors = [d.strip() for d in payload["doctors"] if d.strip()]
    if not services:
        raise ValueError("Please add at least one service.")
    if not doctors:
        raise ValueError("Please add at least one doctor.")
    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "services": services,
        "doctors": doctors,
    }

def parse_config(full: Dict[str, Any]) -> ScheduleConfig:
    base = parse_basic(full)
    start = datetime.strptime(base["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(base["end_date"], "%Y-%m-%d").date()
    services = base["services"]
    doctors = base["doctors"]
    unavailable = {d: set(full.get("unavailable", {}).get(d, [])) for d in doctors}
    return ScheduleConfig(start, end, services, doctors, unavailable)

# -------------------------- Engine -------------------------- #

def generate_schedule(cfg: ScheduleConfig) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    from collections import defaultdict
    rotations: Dict[str, deque] = {svc: deque(cfg.doctors) for svc in cfg.services}
    flexible_counts: Dict[str, int] = {d: 0 for d in cfg.doctors}
    rows: List[Dict[str, Any]] = []

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
            # Prefer those with more flexible days so far; tie-break by rotation position
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

        row = {"date": day_str, "date_label": f"{day_str} ({weekday_initial(day)})"}
        row.update(assigned_today)
        row["Flexible"] = flexible_today
        rows.append(row)

    return rows, flexible_counts

# -------------------------- Templates -------------------------- #

STAGE1_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Doctor Scheduler v4 — Step 1</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap: 1rem; }
    input, textarea { width: 100%; padding: .6rem; border:1px solid #ddd; border-radius:10px; }
    label { font-weight: 600; }
    .btn { padding: .7rem 1rem; border:1px solid #ddd; border-radius:10px; background:white; cursor:pointer; }
    .btn:hover { background:#f5f5f5; }
    .muted { color:#666; }
    .small { font-size:.9rem; }
  </style>
</head>
<body>
  <h1>Step 1: Dates, Services, Doctors</h1>
  <p class=\"muted\">Next step you'll mark specific *unavailable* dates per doctor.</p>
  <form method=\"post\" action=\"/stage2\">
    <div class=\"grid\">
      <div>
        <label>Start date</label>
        <input type=\"date\" name=\"start_date\" value=\"2025-06-30\" required />
      </div>
      <div>
        <label>End date</label>
        <input type=\"date\" name=\"end_date\" value=\"2025-07-11\" required />
      </div>
      <div>
        <label>Services <span class=\"muted small\">(comma-separated)</span></label>
        <input type=\"text\" name=\"services\" value=\"CT, US\" placeholder=\"e.g., CT, US, MR\" required />
      </div>
      <div>
        <label>Doctors <span class=\"muted small\">(one per line)</span></label>
        <textarea name=\"doctors\" rows=\"6\" placeholder=\"One name per line\" required>Alice\nBob\nCarol\nDan</textarea>
      </div>
    </div>
    <p><button class=\"btn\" type=\"submit\">Continue → Unavailability</button></p>
  </form>
</body>
</html>
"""

STAGE2_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Doctor Scheduler v4 — Step 2</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: .4rem; text-align: center; }
    th { background: #fafafa; position: sticky; top: 0; }
    .btn { padding: .6rem 1rem; border:1px solid #ddd; border-radius:10px; background:white; cursor:pointer; }
    .btn:hover { background:#f5f5f5; }
    .muted { color:#666; }
    .pill { display:inline-block; padding:.2rem .5rem; border-radius:999px; background:#f0f0f0; margin:.1rem; }
    .row { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }
  </style>
</head>
<body>
  <h1>Step 2: Mark Unavailability</h1>
  <p class=\"muted\">Check the boxes where a doctor is <strong>unavailable</strong>. Only weekdays between {{start_date}} and {{end_date}} are shown.</p>

  <form method=\"post\" action=\"/generate\">
    <!-- carry stage-1 data forward -->
    <input type=\"hidden\" name=\"start_date\" value=\"{{start_date}}\" />
    <input type=\"hidden\" name=\"end_date\" value=\"{{end_date}}\" />
    <input type=\"hidden\" name=\"services\" value=\"{{services_csv}}\" />
    <textarea name=\"doctors\" style=\"display:none\">{{doctors_text}}</textarea>

    <div class=\"row\">
      <button class=\"btn\" type=\"button\" onclick=\"toggleAll(false)\">Clear all</button>
      <button class=\"btn\" type=\"button\" onclick=\"toggleAll(true)\">Select all</button>
    </div>

    <table>
      <thead>
        <tr>
          <th style=\"text-align:left\">Date</th>
          {% for doc in doctors %}<th>{{doc}}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for d in dates %}
          <tr>
            <td style=\"text-align:left\">{{d}} ({{weekday_initials[loop.index0]}})</td>
            {% for doc in doctors %}
              {# name pattern: u|{{doc}}|{{d}} -> value '1' when checked #}
              <td><input type=\"checkbox\" name=\"u|{{doc}}|{{d}}\" value=\"1\"></td>
            {% endfor %}
          </tr>
        {% endfor %}
      </tbody>
    </table>

    <p style=\"margin-top:1rem;\"><button class=\"btn\" type=\"submit\">Generate schedule</button></p>
  </form>

  <script>
    function toggleAll(state){
      document.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = state);
    }
  </script>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Schedule</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: .5rem; vertical-align: top; }
    th { background: #fafafa; }
    .pill { display:inline-block; padding:.2rem .5rem; border-radius:999px; background:#f0f0f0; margin:.1rem; }
    .unfilled { color:#b00020; font-weight:600; }
    .btn { padding: .6rem 1rem; border:1px solid #ddd; border-radius:10px; background:white; text-decoration:none; }
  </style>
</head>
<body>
  <h1>Schedule</h1>
  <p><a class=\"btn\" href=\"/\">↩︎ Start over</a></p>
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
            <td>{% if val == 'UNFILLED' %}<span class=\"unfilled\">UNFILLED</span>{% else %}{{val}}{% endif %}</td>
          {% endfor %}
          <td>
            {% for name in row.Flexible %}<span class=\"pill\">{{name}}</span>{% endfor %}
          </td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""

# -------------------------- Routes -------------------------- #

@app.route("/", methods=["GET"])
def stage1():
    return render_template_string(STAGE1_HTML)

@app.route("/stage2", methods=["POST"])
def stage2():
    try:
        services = [s.strip() for s in (request.form.get("services", "").split(",")) if s.strip()]
        doctors = [d.strip() for d in request.form.get("doctors", "").splitlines() if d.strip()]
        payload = {
            "start_date": request.form.get("start_date", "").strip(),
            "end_date": request.form.get("end_date", "").strip(),
            "services": services,
            "doctors": doctors,
        }
        base = parse_basic(payload)
        start = base["start_date"]
        end = base["end_date"]
        # build weekday list for display initials
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        dates = [d.strftime("%Y-%m-%d") for d in iter_workdays(d0, d1)]
        weekday_initials = [WEEKDAY_INITIALS[datetime.strptime(s, "%Y-%m-%d").date().weekday()] for s in dates]

        return render_template_string(
            STAGE2_HTML,
            start_date=start,
            end_date=end,
            services_csv=", ".join(services),
            doctors=doctors,
            doctors_text="\n".join(doctors),
            dates=dates,
            weekday_initials=weekday_initials,
        )
    except Exception as e:
        return Response(f"Error: {e}", status=400)

@app.route("/generate", methods=["POST"])
def generate():
    try:
        # reconstruct full payload from stage2 form
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        services = [s.strip() for s in request.form.get("services", "").split(",") if s.strip()]
        doctors = [d.strip() for d in request.form.get("doctors", "").splitlines() if d.strip()]
        unavailable: Dict[str, List[str]] = {d: [] for d in doctors}
        for key, val in request.form.items():
            if not key.startswith("u|"):
                continue
            # key pattern: u|Doctor|YYYY-MM-DD ; value '1' if checked
            _, doc, datestr = key.split("|", 2)
            if val == "1" and doc in unavailable:
                unavailable[doc].append(datestr)
        full = {
            "start_date": start_date,
            "end_date": end_date,
            "services": services,
            "doctors": doctors,
            "unavailable": unavailable,
        }
        cfg = parse_config(full)
        rows, _ = generate_schedule(cfg)
        return render_template_string(RESULTS_HTML, rows=rows, services=cfg.services)
    except Exception as e:
        return Response(f"Error: {e}", status=400)

# -------------------------- Entrypoint -------------------------- #

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
