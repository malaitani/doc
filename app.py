"""
Doctor Scheduler v7 — single-file Flask app
Two-stage input + smart defaults + service day controls + per-date overrides + config upload

New in v7:
  • Step 2 adds a per-date × per-service ON/OFF grid to override weekday rules.
  • Step 1 accepts an optional Config JSON upload to prefill dates/services/doctors and rules.
  • Services default to Mon–Fri; you can uncheck days or override specific dates.
  • Defaults: services → "x, y, z"; doctors → "A, B, C".
Console & Render ready.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from datetime import date, timedelta, datetime
from typing import List, Dict, Any, Tuple
import os
import json
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

WEEKDAY_INITIALS = ["M", "T", "W", "T", "F", "S", "S"]
WEEKDAYS_MON_FRI = [0,1,2,3,4]

# -------------------------- Helpers & Defaults -------------------------- #

def first_monday_after(today: date) -> date:
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def week4_friday_from(start_monday: date) -> date:
    return start_monday + timedelta(days=25)


def iter_workdays(start: date, end: date):
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)


def weekday_initial(d: date) -> str:
    return WEEKDAY_INITIALS[d.weekday()]

# -------------------------- Models & Parsing -------------------------- #

@dataclass
class ScheduleConfig:
    start_date: date
    end_date: date
    services: List[str]
    doctors: List[str]
    unavailable: Dict[str, set]
    service_days: Dict[str, set]  # {svc: {0..4}}


def parse_basic(payload: Dict[str, Any]) -> Dict[str, Any]:
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
    sd_in = full.get("service_days", {}) or {}
    service_days: Dict[str, set] = {svc: set(sd_in.get(svc, WEEKDAYS_MON_FRI)) for svc in services}
    return ScheduleConfig(start, end, services, doctors, unavailable, service_days)

# -------------------------- Templates -------------------------- #

STAGE1_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Doctor Scheduler v7 — Step 1</title>
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
  <p class=\"muted\">Defaults use first Monday after today → Friday of week 4. You can also upload a JSON config to prefill.</p>
  <form method=\"post\" action=\"/stage2\" enctype=\"multipart/form-data\">
    <div class=\"grid\">
      <div>
        <label>Start date</label>
        <input type=\"date\" name=\"start_date\" value=\"{{start_default}}\" required />
      </div>
      <div>
        <label>End date</label>
        <input type=\"date\" name=\"end_date\" value=\"{{end_default}}\" required />
      </div>
      <div>
        <label>Services <span class=\"muted small\">(comma-separated)</span></label>
        <input type=\"text\" name=\"services\" value=\"x, y, z\" placeholder=\"e.g., CT, US, MR\" required />
      </div>
      <div>
        <label>Doctors <span class=\"muted small\">(one per line)</span></label>
        <textarea name=\"doctors\" rows=\"6\" placeholder=\"One name per line\" required>A
B
C</textarea>
      </div>
      <div style=\"grid-column:1/-1\">
        <label>Optional config upload (JSON)</label>
        <input type=\"file\" name=\"config_file\" accept=\"application/json\" />
        <p class=\"small muted\">Schema (any keys optional): { start_date, end_date, services[], doctors[], service_days{svc:[0-4]}, date_overrides{YYYY-MM-DD:{svc:boolean}}, unavailable{doctor:[dates]} }</p>
      </div>
    </div>
    <p><button class=\"btn\" type=\"submit\">Continue → Unavailability, Service Days & Per-Date Overrides</button></p>
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
  <title>Doctor Scheduler v7 — Step 2</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: .4rem; text-align: center; }
    th { background: #fafafa; position: sticky; top: 0; }
    .btn { padding: .6rem 1rem; border:1px solid #ddd; border-radius:10px; background:white; cursor:pointer; }
    .btn:hover { background:#f5f5f5; }
    .muted { color:#666; }
    .row { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }
    .left { text-align:left; }
    .small { font-size:.9rem; }
  </style>
</head>
<body>
  <h1>Step 2: Unavailability, Service Days & Per-Date Overrides</h1>
  <p class=\"muted\">1) Pick which <strong>weekdays</strong> each service is staffed. 2) Mark <strong>doctor unavailability</strong>. 3) Optional per-date <strong>service overrides</strong>.</p>

  <form method=\"post\" action=\"/generate\">
    <input type=\"hidden\" name=\"start_date\" value=\"{{start_date}}\" />
    <input type=\"hidden\" name=\"end_date\" value=\"{{end_date}}\" />
    <input type=\"hidden\" name=\"services\" value=\"{{services_csv}}\" />
    <textarea name=\"doctors\" style=\"display:none\">{{doctors_text}}</textarea>

    <h2 class=\"left\">Service Days</h2>
    <table>
      <thead>
        <tr>
          <th class=\"left\">Service</th>
          <th>M</th><th>T</th><th>W</th><th>T</th><th>F</th>
        </tr>
      </thead>
      <tbody>
        {% for svc in services %}
        <tr>
          <td class=\"left\">{{svc}}</td>
          {% for dow in [0,1,2,3,4] %}
            <td><input type=\"checkbox\" name=\"sd|{{svc}}|{{dow}}\" value=\"1\" {{ 'checked' if svc not in service_days_prefill or dow in service_days_prefill.get(svc, []) else '' }}></td>
          {% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <h2 class=\"left\">Doctor Unavailability</h2>
    <div class=\"row\">
      <button class=\"btn\" type=\"button\" onclick=\"toggleAll(false)\">Clear all</button>
      <button class=\"btn\" type=\"button\" onclick=\"toggleAll(true)\">Select all</button>
    </div>

    <table>
      <thead>
        <tr>
          <th class=\"left\">Date</th>
          {% for doc in doctors %}<th>{{doc}}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for d in dates %}
          <tr>
            <td class=\"left\">{{d}} ({{weekday_initials[loop.index0]}})</td>
            {% for doc in doctors %}
              <td><input type=\"checkbox\" name=\"u|{{doc}}|{{d}}\" value=\"1\" {{ 'checked' if (doc,d) in unavail_prefill else '' }}></td>
            {% endfor %}
          </tr>
        {% endfor %}
      </tbody>
    </table>

    <h2 class=\"left\">Per-Date Service Overrides</h2>
    <p class=\"small muted\">Checked = service is ON that date. Default follows weekday rules unless overridden here.</p>
    <table>
      <thead>
        <tr>
          <th class=\"left\">Date</th>
          {% for svc in services %}<th>{{svc}}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for d in dates %}
          <tr>
            <td class=\"left\">{{d}} ({{weekday_initials[loop.index0]}})</td>
            {% for svc in services %}
              {% set dow = weekday_index_map[loop.parent.loop.index0] %}
              {% set default_on = (dow in service_days_prefill.get(svc, [0,1,2,3,4])) %}
              {% set is_on = date_overrides_prefill.get(d, {}).get(svc, default_on) %}
              <td><input type=\"checkbox\" name=\"so|{{d}}|{{svc}}\" value=\"1\" {{ 'checked' if is_on else '' }}></td>
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
            <td>{% if val == 'UNFILLED' %}<span class=\"unfilled\">UNFILLED</span>{% elif val == 'OFF' %}<span class=\"muted\">—</span>{% else %}{{val}}{% endif %}</td>
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
    today = date.today()
    start_def = first_monday_after(today)
    end_def = week4_friday_from(start_def)
    return render_template_string(
        STAGE1_HTML,
        start_default=start_def.strftime("%Y-%m-%d"),
        end_default=end_def.strftime("%Y-%m-%d"),
    )

@app.route("/stage2", methods=["POST"])
def stage2():
    try:
        # Pull basic fields
        start_in = request.form.get("start_date", "").strip()
        end_in = request.form.get("end_date", "").strip()
        services_in = [s.strip() for s in (request.form.get("services", "").split(",")) if s.strip()]
        doctors_in = [d.strip() for d in request.form.get("doctors", "").splitlines() if d.strip()]

        # Optional config upload (JSON)
        cfg_file = request.files.get("config_file")
        pre = {}
        if cfg_file and cfg_file.filename:
            try:
                pre = json.load(cfg_file.stream)
            except Exception:
                pre = {}

        # Merge uploaded config with form (form wins if both provided)
        start = start_in or pre.get("start_date", "")
        end = end_in or pre.get("end_date", "")
        services = services_in or pre.get("services", [])
        doctors = doctors_in or pre.get("doctors", [])
        service_days_prefill = pre.get("service_days", {})  # {svc: [0..4]}
        date_overrides_prefill = pre.get("date_overrides", {})  # {"YYYY-MM-DD": {svc: bool}}
        unavail_prefill_pairs = set()
        for doc, dates in (pre.get("unavailable", {}) or {}).items():
            for d in dates:
                unavail_prefill_pairs.add((doc, d))

        payload = {
            "start_date": start,
            "end_date": end,
            "services": services,
            "doctors": doctors,
        }
        base = parse_basic(payload)
        start = base["start_date"]
        end = base["end_date"]
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        dates = [d.strftime("%Y-%m-%d") for d in iter_workdays(d0, d1)]
        weekday_initials = [WEEKDAY_INITIALS[datetime.strptime(s, "%Y-%m-%d").date().weekday()] for s in dates]
        weekday_index_map = [datetime.strptime(s, "%Y-%m-%d").date().weekday() for s in dates]

        return render_template_string(
            STAGE2_HTML,
            start_date=start,
            end_date=end,
            services=services,
            services_csv=", ".join(services),
            doctors=doctors,
            doctors_text="
".join(doctors),
            dates=dates,
            weekday_initials=weekday_initials,
            weekday_index_map=weekday_index_map,
            service_days_prefill=service_days_prefill,
            date_overrides_prefill=date_overrides_prefill,
            unavail_prefill=unavail_prefill_pairs,
        )
    except Exception as e:
        return Response(f"Error: {e}", status=400)

@app.route("/generate", methods=["POST"])
def generate():
    try:
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        services = [s.strip() for s in request.form.get("services", "").split(",") if s.strip()]
        doctors = [d.strip() for d in request.form.get("doctors", "").splitlines() if d.strip()]

        # Parse service weekday selections
        service_days: Dict[str, List[int]] = {svc: [] for svc in services}
        for key, val in request.form.items():
            if key.startswith("sd|") and val == "1":
                _, svc, dow = key.split("|", 2)
                if svc in service_days:
                    try:
                        service_days[svc].append(int(dow))
                    except ValueError:
                        pass
        service_days = {svc: (set(v) if v else set(WEEKDAYS_MON_FRI)) for svc, v in service_days.items()}

        # Parse doctor unavailability
        unavailable: Dict[str, List[str]] = {d: [] for d in doctors}
        for key, val in request.form.items():
            if key.startswith("u|") and val == "1":
                _, doc, datestr = key.split("|", 2)
                if doc in unavailable:
                    unavailable[doc].append(datestr)

        # Parse per-date service overrides (so|DATE|SVC) checked = ON
        date_overrides: Dict[str, Dict[str, bool]] = {}
        for key, val in request.form.items():
            if key.startswith("so|"):
                _, d, svc = key.split("|", 2)
                date_overrides.setdefault(d, {})[svc] = (val == "1")

        # Build base config
        full = {
            "start_date": start_date,
            "end_date": end_date,
            "services": services,
            "doctors": doctors,
            "unavailable": unavailable,
            "service_days": service_days,
        }
        cfg = parse_config(full)

        # Generate with overrides
        rows: List[Dict[str, Any]] = []
        flex_counts: Dict[str, int] = {d: 0 for d in doctors}
        rotations: Dict[str, deque] = {svc: deque(doctors) for svc in services}

        for day in iter_workdays(cfg.start_date, cfg.end_date):
            day_str = day.strftime("%Y-%m-%d")
            dow = day.weekday()
            available_today = {d for d in cfg.doctors if day_str not in cfg.unavailable.get(d, set())}
            used_doctors = set()
            assigned_today: Dict[str, str] = {}

            for svc in services:
                default_on = dow in cfg.service_days.get(svc, WEEKDAYS_MON_FRI)
                on = date_overrides.get(day_str, {}).get(svc, default_on)
                if not on:
                    assigned_today[svc] = "OFF"
                    continue
                rot = rotations[svc]
                candidates = [(doc, idx) for idx, doc in enumerate(rot) if doc in available_today and doc not in used_doctors]
                if not candidates:
                    assigned_today[svc] = "UNFILLED"
                    continue
                candidates.sort(key=lambda t: (-flex_counts[t[0]], t[1]))
                chosen = candidates[0][0]
                assigned_today[svc] = chosen
                used_doctors.add(chosen)
                while rot[0] != chosen:
                    rot.rotate(-1)
                rot.rotate(-1)

            flexible_today = sorted(list(available_today - used_doctors))
            for doc in flexible_today:
                flex_counts[doc] += 1

            row = {"date": day_str, "date_label": f"{day_str} ({weekday_initial(day)})"}
            row.update(assigned_today)
            row["Flexible"] = flexible_today
            rows.append(row)

        return render_template_string(RESULTS_HTML, rows=rows, services=services)
    except Exception as e:
        return Response(f"Error: {e}", status=400)

# -------------------------- Entrypoint -------------------------- #

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
