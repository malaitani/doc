
from flask import Flask, render_template, request
import random
from datetime import date, timedelta

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    default_services = "CT, Ultrasound, MRI, X-ray"
    default_doctors = "Dr. Smith, Dr. Lee, Dr. Patel, Dr. Gomez"
    default_days = 5

    services = default_services.split(", ")
    doctors = default_doctors.split(", ")
    num_days = default_days
    schedule_map = {}
    days = []

    if request.method == 'POST':
        num_days = int(request.form.get("days", 5))
        services = [s.strip() for s in request.form.get("services", "").split(",")]
        doctors = [d.strip() for d in request.form.get("doctors", "").split(",")]

        base_date = date.today()
        days = [(base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)]
        schedule_map = {day: {} for day in days}

        for day in days:
            for service in services:
                schedule_map[day][service] = random.choice(doctors)

    return render_template("index.html",
        days=days,
        services=services,
        schedule_map=schedule_map,
        default_services=default_services,
        default_doctors=default_doctors,
        default_days=default_days
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
