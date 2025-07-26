
from flask import Flask, render_template
import random
from datetime import date, timedelta

app = Flask(__name__)

# Configuration
doctors = ["Dr. Smith", "Dr. Lee", "Dr. Patel", "Dr. Gomez"]
services = ["CT", "Ultrasound", "MRI", "X-ray"]
num_days = 5

def generate_schedule_map():
    base_date = date.today()
    days = [(base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)]
    schedule_map = {day: {} for day in days}
    for day in days:
        for service in services:
            schedule_map[day][service] = random.choice(doctors)
    return days, services, schedule_map

@app.route('/')
def index():
    days, service_list, schedule_map = generate_schedule_map()
    return render_template("index.html", days=days, services=service_list, schedule_map=schedule_map)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
