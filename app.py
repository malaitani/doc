
from flask import Flask, render_template, request
from collections import deque, defaultdict
import random
from datetime import date, timedelta

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    default_services = "CT, Ultrasound, MRI, X-ray"
    default_doctors = "Dr. Smith, Dr. Lee, Dr. Patel, Dr. Gomez"
    default_days = 5
    default_unavailable = "\n".join(["" for _ in range(default_days)])

    services = default_services.split(", ")
    doctors = default_doctors.split(", ")
    num_days = default_days
    schedule_map = {}
    flexible_map = {}
    unavailable_map = {}
    flex_count = defaultdict(int)
    days = []

    if request.method == 'POST':
        num_days = int(request.form.get("days", 5))
        services = [s.strip() for s in request.form.get("services", "").split(",")]
        doctors = [d.strip() for d in request.form.get("doctors", "").split(",")]

        base_date = date.today()
        days = []
        d = base_date
        while len(days) < num_days:
            if d.weekday() < 5:
                label = f"{d.strftime('%Y-%m-%d')} ({d.strftime('%a')[0]})"
                days.append(label)
            d += timedelta(days=1)

        schedule_map = {day: {} for day in days}
        flexible_map = {day: [] for day in days}
        unavailable_map = {day: [] for day in days}

        unavailable_input = request.form.get("unavailable", "")
        unavailable_lines = unavailable_input.strip().split("\n")
        unavailable_by_day = []
        for line in unavailable_lines:
            if line.strip():
                unavailable_by_day.append([d.strip() for d in line.split(",")])
            else:
                unavailable_by_day.append([])

        while len(unavailable_by_day) < num_days:
            unavailable_by_day.append([])

        for i, day in enumerate(days):
            unavailable_map[day] = unavailable_by_day[i]

        doctor_queue = deque(doctors)

        for i, day in enumerate(days):
            assigned = []
            unavailable_today = set(unavailable_by_day[i])
            available_today = [d for d in doctors if d not in unavailable_today]

            if not available_today:
                for service in services:
                    schedule_map[day][service] = "N/A"
                flexible_map[day] = []
                continue

            # Rotate doctor queue until we find a valid cycle
            doctor_cycle = deque([d for d in doctor_queue if d in available_today])
            if not doctor_cycle:
                for service in services:
                    schedule_map[day][service] = "N/A"
                flexible_map[day] = []
                continue

            # Assign doctors in round-robin
            for service in services:
                while doctor_cycle:
                    staff = doctor_cycle.popleft()
                    if staff not in assigned:
                        assigned.append(staff)
                        schedule_map[day][service] = staff
                        break
                else:
                    schedule_map[day][service] = "N/A"

            # Track who is flexible, balance across days
            unassigned = [d for d in available_today if d not in assigned]
            if unassigned:
                min_flex = min(flex_count[d] for d in unassigned)
                flexible_today = [d for d in unassigned if flex_count[d] == min_flex]
                flexible_map[day] = flexible_today
                for d in flexible_today:
                    flex_count[d] += 1
            else:
                flexible_map[day] = []

            # Rotate main queue
            doctor_queue.rotate(-1)

    return render_template("index.html",
        days=days,
        services=services,
        schedule_map=schedule_map,
        flexible_map=flexible_map,
        unavailable_map=unavailable_map,
        default_services=default_services,
        default_doctors=default_doctors,
        default_days=num_days,
        default_unavailable=request.form.get("unavailable", default_unavailable)
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
