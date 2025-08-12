"""
Doctor Scheduler v8 — single-file Flask app
Two-stage input + smart defaults + 2‑week (biweekly) service patterns + config upload

New in v8:
  • Two-week staffing grid per service (Week 1 & Week 2).
  • Generator uses start date as Week 1; then alternates W1/W2 across the range (week-on/week-off supported).
  • Per-date overrides removed to simplify workflow (can re-add on request).
  • Backward compatible: weekly 'service_days' still accepted; duplicated to both weeks.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from datetime import date, timedelta, datetime
from typing import List, Dict, Any
import os, json
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

WEEKDAY_INITIALS = ["M", "T", "W", "T", "F", "S", "S"]

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
    unavailable: Dict[str, set]                # {doctor: {YYYY-MM-DD}}
    service_days_2wk: Dict[str, Dict[int, set]]  # {svc: {0:{0..4}, 1:{0..4}}}

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
    # normalize unavailable to sets
    unavailable = {d: set(full.get("unavailable", {}).get(d, [])) for d in doctors}

    # Accept either weekly or 2-week dict and normalize
    sd_weekly = full.get("service_days", {}) or {}
    sd2_in = full.get("service_days_2wk", {}) or {}
    sd2: Dict[str, Dict[int, set]] = {}
    for svc in services:
        if svc in sd2_in:
            w1 = set(sd2_in[svc].get(0, sd2_in[svc].get("0", [])))
            w2 = set(sd2_in[svc].get(1, sd2_in[svc].get("1", [])))
        else:
            base_days = set(sd_weekly.get(svc, [0,1,2,3,4]))
            w1 = set(base_days)
            w2 = set(base_days)
        sd2[svc] = {0: w1, 1: w2}
    return ScheduleConfig(start, end, services, doctors, unavailable, sd2)

# -------------------------- Templates -------------------------- #

STAGE1_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Doctor Scheduler v8 — Step 1</title>
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
  <p class="muted">Defaults use first Monday after today → Friday of week 4. Optional: upload a JSON config to prefill. Biweekly patterns are set on the next step.</p>
  <form method="post" action="/stage2" enctype="multipart/form-data">
    <div class="grid">
      <div>
        <label>Start date</label>
        <input type="date" name="start_date" value="{{start_default}}" required />
      </div>
      <div>
        <label>End date</label>
        <input type="date" name="end_date" value="{{end_default}}" required />
      </div>
      <div>
        <label>Services <span class="muted small">(comma-separated)</span></label>
        <input type="text" name="services" value="x, y, z" placeholder="e.g., CT, US, MR" required />
      </div>
      <div>
        <label>Doctors <span class="muted small">(one per line)</span></label>
        <textarea name="doctors" rows="6" placeholder="One name per line" required>A
B
C</textarea>
      </div>
      <div style="grid-column:1/-1">
        <label>Optional config upload (JSON)</label>
        <input type="file" name="config_file" accept="application/json" />
        <p class="small muted">Schema keys (any optional): start_date, end_date, services[], doctors[], <strong>service_days_2wk</strong>{svc:{0:[0..4],1:[0..4]}}, unavailable{doctor:[dates]}. Old <code>service_days</code> also works.</p>
      </div>
    </div>
    <p><button class="btn" type="submit">Continue → Unavailability & 2‑Week Pattern</button></p>
  </form>
</body>
</html>
"""

STAGE2_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Doctor Scheduler v8 — Step 2</title>
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
  <h1>Step 2: Unavailability & 2‑Week Service Pattern</h1>
  <p class="muted">Set a two‑week cadence for each service (Week 1 & Week 2). Week numbering starts from your start date. Use “Copy W1→W2” to make them identical (weekly pattern).</p>

  <form method="post" action="/generate">
    <input type="hidden" name="start_date" value="{{start_date}}" />
    <input type="hidden" name="end_date" value="{{end_date}}" />
    <input type="hidden" name="services" value="{{services_csv}}" />
    <textarea name="doctors" style="display:none">{{doctors_text}}</textarea>

    <div class="row" style="justify-content:space-between;">
      <h2 class="left">Service Pattern (Week 1 & Week 2)</h2>
      <button type="button" class="btn" onclick="copyW1toW2()">Copy W1 → W2</button>
    </div>

    <table>
      <thead>
        <tr>
          <th class="left" rowspan="2">Service</th>
          <th colspan="5">Week 1</th>
          <th colspan="5">Week 2</th>
        </tr>
        <tr>
          <th>M</th><th>T</th><th>W</th><th>T</th><th>F</th>
          <th>M</th><th>T</th><th>W</th><th>T</th><th>F</th>
        </tr>
      </thead>
      <tbody>
        {% for svc in services %}
        <tr>
          <td class="left">{{svc}}</td>
          {% for dow in [0,1,2,3,4] %}
            <td>
              <input type="hidden" name="sdw|{{svc}}|0|{{dow}}" value="0">
              <input type="checkbox" name="sdw|{{svc}}|0|{{dow}}" value="1" {{ 'checked' if dow in sd_w1.get(svc, [0,1,2,3,4]) else '' }}>
            </td>
          {% endfor %}
          {% for dow in [0,1,2,3,4] %}
            <td>
              <input type="hidden" name="sdw|{{svc}}|1|{{dow}}" value="0">
              <input type="checkbox" name="sdw|{{svc}}|1|{{dow}}" value="1" {{ 'checked' if dow in sd_w2.get(svc, [0,1,2,3,4]) else '' }}>
            </td>
          {% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <h2 class="left">Doctor Unavailability</h2>
    <div class="row">
      <button class="btn" type="button" onclick="toggleAll(false)">Clear all</button>
      <button class="btn" type="button" onclick="toggleAll(true)">Select all</button>
    </div>

    <table>
      <thead>
        <tr>
          <th class="left">Date</th>
          {% for doc in doctors %}<th>{{doc}}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in date_rows %}
          <tr>
            <td class="left">{{row.d}} ({{row.initial}})</td>
            {% for doc in doctors %}
              <td><input type="checkbox" name="u|{{doc}}|{{row.d}}" value="1" {{ 'checked' if (doc,row.d) in unavail_prefill else '' }}></td>
            {% endfor %}
          </tr>
        {% endfor %}
      </tbody>
    </table>

    <p style="margin-top:1rem;"><button class="btn" type="submit">Generate schedule</button></p>
  </form>

  <script>
    function toggleAll(state){
      document.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = state);
    }
    function copyW1toW2(){
      const w1 = document.querySelectorAll('input[name^="sdw|"][name*="|0|"]');
      w1.forEach(cb1 => {
        const cb2Name = cb1.name.replace('|0|','|1|');
        const h2 = document.querySelector(`input[type=hidden][name="${cb2Name}"]`);
        const cb2 = document.querySelector(`input[type=checkbox][name="${cb2Name}"]`);
        if (cb2) cb2.checked = cb1.checked;
        if (h2) h2.value = cb1.checked ? '1' : '0';
      });
    }
  </script>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Schedule</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #eee; padding: .5rem; vertical-align: top; }
    th { background: #fafafa; }
    .pill { display:inline-block; padding:.2rem .5rem; border-radius:999px; background:#f0f0f0; margin:.1rem; }
    .unfilled { color:#b00020; font-weight:600; }
    .muted { color:#666; }
    .btn { padding: .6rem 1rem; border:1px solid #ddd; border-radius:10px; background:white; text-decoration:none; }
  </style>
</head>
<body>
  <h1>Schedule</h1>
  <p><a class="btn" href="/">↩︎ Start over</a></p>
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
            <td>{% if val == 'UNFILLED' %}<span class="unfilled">UNFILLED</span>{% elif val == 'OFF' %}<span class="muted">—</span>{% else %}{{val}}{% endif %}</td>\n          {% endfor %}\n          <td>\n            {% for name in row.Flexible %}<span class=\"pill\">{{name}}</span>{% endfor %}\n          </td>\n        </tr>\n      {% endfor %}\n    </tbody>\n  </table>\n</body>\n</html>\n"""

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
        start_in = request.form.get("start_date", "").strip()
        end_in = request.form.get("end_date", "").strip()
        services_in = [s.strip() for s in (request.form.get("services", "").split(",")) if s.strip()]
        doctors_in = [d.strip() for d in request.form.get("doctors", "").splitlines() if d.strip()]

        cfg_file = request.files.get("config_file")
        pre = {}
        if cfg_file and cfg_file.filename:
            try:
                pre = json.load(cfg_file.stream)
            except Exception:
                pre = {}

        start = start_in or pre.get("start_date", "")
        end = end_in or pre.get("end_date", "")
        services = services_in or pre.get("services", [])
        doctors = doctors_in or pre.get("doctors", [])

        # Prefill weekly or 2-week patterns
        sd_weekly = pre.get("service_days", {}) or {}
        sd2 = pre.get("service_days_2wk", {}) or {}
        sd_w1, sd_w2 = {}, {}
        for svc in services:
            if svc in sd2:
                sd_w1[svc] = sd2[svc].get(0, sd2[svc].get("0", [0,1,2,3,4]))
                sd_w2[svc] = sd2[svc].get(1, sd2[svc].get("1", [0,1,2,3,4]))
            else:
                base_days = sd_weekly.get(svc, [0,1,2,3,4])
                sd_w1[svc] = base_days
                sd_w2[svc] = base_days

        # Prefill unavailability
        unavail_prefill_pairs = set()
        for doc, dates in (pre.get("unavailable", {}) or {}).items():
            for d in dates:
                unavail_prefill_pairs.add((doc, d))

        # validate basics
        base = parse_basic({"start_date": start, "end_date": end, "services": services, "doctors": doctors})

        d0 = datetime.strptime(base["start_date"], "%Y-%m-%d").date()
        d1 = datetime.strptime(base["end_date"], "%Y-%m-%d").date()
        date_rows = [{
            "d": d.strftime("%Y-%m-%d"),
            "initial": WEEKDAY_INITIALS[d.weekday()],
            "dow": d.weekday(),
        } for d in iter_workdays(d0, d1)]

        return render_template_string(
            STAGE2_HTML,
            start_date=base["start_date"],
            end_date=base["end_date"],
            services=services,
            services_csv=", ".join(services),
            doctors=doctors,
            doctors_text="\n".join(doctors),
            date_rows=date_rows,
            sd_w1=sd_w1,
            sd_w2=sd_w2,
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

        # Parse 2-week service weekday selections (hidden 0 + checkbox 1)
        service_days_2wk: Dict[str, Dict[int, set]] = {svc: {0:set(), 1:set()} for svc in services}
        for key, values in request.form.lists():
            if key.startswith("sdw|"):
                _, svc, w, dow = key.split("|", 3)
                if svc in service_days_2wk:
                    val = values[-1] if values else "0"
                    if val == "1":
                        service_days_2wk[svc][int(w)].add(int(dow))

        # Parse doctor unavailability
        unavailable: Dict[str, List[str]] = {d: [] for d in doctors}
        for key, val in request.form.items():
            if key.startswith("u|") and val == "1":
                _, doc, datestr = key.split("|", 2)
                if doc in unavailable:
                    unavailable[doc].append(datestr)

        # Build base config
        full = {
            "start_date": start_date,
            "end_date": end_date,
            "services": services,
            "doctors": doctors,
            "unavailable": unavailable,
            "service_days_2wk": service_days_2wk,
        }
        cfg = parse_config(full)

        # Generate using biweekly cadence
        rows: List[Dict[str, Any]] = []
        flex_counts: Dict[str, int] = {d: 0 for d in doctors}
        rotations: Dict[str, deque] = {svc: deque(doctors) for svc in services}
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date()

        for day in iter_workdays(cfg.start_date, cfg.end_date):
            day_str = day.strftime("%Y-%m-%d")
            dow = day.weekday()
            week_ix = ((day - start_d).days // 7) % 2  # 0 = Week 1, 1 = Week 2
            available_today = {d for d in cfg.doctors if day_str not in cfg.unavailable.get(d, set())}
            used_doctors = set()
            assigned_today: Dict[str, str] = {}

            for svc in services:
                on = dow in cfg.service_days_2wk.get(svc, {}).get(week_ix, set([0,1,2,3,4]))
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
