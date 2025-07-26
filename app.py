
from flask import Flask, render_template
import random
from datetime import date, timedelta

app = Flask(__name__)

# Configuration
doctors = ["Dr. Smith", "Dr. Lee", "Dr. Patel", "Dr. Gomez"]
services = ["CT", "Ultrasound", "MRI", "X-ray"]
num_days = 5

# Generate random schedule
def generate_schedule():
    base_date = date.today()
    schedule = []
    for i in range(num_days):
        current_date = base_date + timedelta(days=i)
        for service in services:
            staff = random.choice(doctors)
            schedule.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "service": service,
                "staff": staff
            })
    return schedule

@app.route('/')
def index():
    schedule = generate_schedule()
    return render_template("index.html", schedule=schedule)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
