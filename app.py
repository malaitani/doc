
from flask import Flask, render_template, request
import pandas as pd
from datetime import datetime, timedelta
from collections import deque

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    schedule = None
    dates = None
    unavailable_input = ""
    doctor_input = ""

    if request.method == 'POST':
        doctor_input = request.form['doctors']
        num_days = int(request.form['num_days'])
        unavailable_input = request.form['unavailable']

        doctor_list = [doc.strip() for doc in doctor_input.splitlines() if doc.strip()]
        unavailable_by_date = {}

        if unavailable_input:
            for line in unavailable_input.splitlines():
                parts = line.strip().split(":")
                if len(parts) == 2:
                    date_str, names = parts
                    date_str = date_str.strip()
                    try:
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                        unavailable_doctors = [name.strip() for name in names.split(",") if name.strip()]
                        unavailable_by_date[date_obj] = unavailable_doctors
                    except ValueError:
                        pass  # skip invalid date format

        # Generate list of weekdays only
        start_date = datetime.today().date()
        dates = []
        while len(dates) < num_days:
            if start_date.weekday() < 5:  # Weekday
                dates.append(start_date)
            start_date += timedelta(days=1)

        schedule = []
        doctor_queue = deque(doctor_list)
        doctor_flexible_days = {doc: 0 for doc in doctor_list}
        last_assigned = {}

        for date in dates:
            day_schedule = {'date': f"{date.strftime('%-m/%-d')} ({date.strftime('%a')[0]})"}
            unavailable_today = unavailable_by_date.get(date, [])
            assigned_doctor = None

            while doctor_queue:
                candidate = doctor_queue[0]
                if candidate in unavailable_today:
                    break  # keep them in place, try again tomorrow
                else:
                    assigned_doctor = doctor_queue.popleft()
                    doctor_queue.append(assigned_doctor)
                    break

            for doc in doctor_list:
                if doc == assigned_doctor:
                    day_schedule[doc] = "X"
                elif doc in unavailable_today:
                    day_schedule[doc] = "unavailable"
                else:
                    day_schedule[doc] = "flexible"
                    doctor_flexible_days[doc] += 1

            schedule.append(day_schedule)

        # Add row to display unavailable doctors
        unavailable_row = {'date': 'Unavailable'}
        for doc in doctor_list:
            days_unavailable = sum(1 for d in dates if doc in unavailable_by_date.get(d, []))
            unavailable_row[doc] = f"{days_unavailable} day(s)"
        schedule.append(unavailable_row)

        schedule = pd.DataFrame(schedule)

    return render_template('index.html', schedule=schedule, dates=dates,
                           doctor_input=doctor_input, unavailable_input=unavailable_input)

if __name__ == '__main__':
    app.run(debug=True)
