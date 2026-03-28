import os
import csv
import time
from datetime import datetime
from collections import deque
from flask import Flask, request, Response, render_template_string

app = Flask(__name__)

# File for saving results
CSV_FILE = 'biathlon_results.csv'

# Memory initialization for 30 shooting lanes (according to your XML)
# flaps: list of 5 elements (default '0' - target is open/black)
# Added 'arrival_time' to track when athlete arrives at the mat (Code 2)
lanes_data = {
    i: {
        'time': '', 'number': '', 'flaps': ['0', '0', '0', '0', '0'], 
        'last': '', 'shots': 0, 'arrival_time': 0.0, 
        'last_shot_time': 0.0, 'last_shot_type': ''
    }
    for i in range(1, 31)
}

# Store the last 50 raw messages for the monitor page
raw_messages_log = deque(maxlen=50)

def init_csv():
    """Creates a CSV file with headers if it doesn't exist yet."""
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Added 'Range Time (s)' to headers
            writer.writerow(['Date', 'Time', 'Athlete Number', 'Lane', 'Shots Fired', 'Flaps Result', 'Range Time (s)'])

init_csv()

def save_result_to_csv(lane, athlete_number, shots, flaps_str, range_time):
    """Saves the result to CSV after receiving command 5."""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([date_str, time_str, athlete_number, lane, shots, flaps_str, range_time])

def process_kes_message(msg):
    """Main processor of KES protocol logic."""
    msg = msg.strip()
    if not msg:
        return

    msg_type = msg[0]
    try:
        lane = int(msg[1:3])
    except ValueError:
        return # Ignore incorrect data

    if lane not in lanes_data:
        return

    if msg_type == '2':
        # 2[Lane##][Number###]
        athlete_num = msg[3:6].strip()
        lanes_data[lane]['number'] = athlete_num
        # Record the exact time the athlete arrived at the shooting lane
        lanes_data[lane]['arrival_time'] = time.time()
        # Reset debounce variables for the new athlete
        lanes_data[lane]['last_shot_time'] = 0.0
        lanes_data[lane]['last_shot_type'] = ''

    elif msg_type in ['4', '9']:
        # 4[Lane##][Flaps#####] (Hit) or 9[Lane##][Flaps#####] (Miss)
        flaps_str = msg[3:8]
        current_time = time.time()

        # Calculate time difference since the last processed shot for this lane
        time_since_last = current_time - lanes_data[lane].get('last_shot_time', 0.0)

        if time_since_last < 0.5:
            # Simultaneous shots (less than 1 second apart)
            if msg_type == '4' and lanes_data[lane].get('last_shot_type') == '9':
                # Upgrade a false miss to a confirmed hit
                # Do NOT increment shots, just update flaps and type
                lanes_data[lane]['last_shot_type'] = '4'
                if len(flaps_str) == 5:
                    lanes_data[lane]['flaps'] = list(flaps_str)
            else:
                # Ignore duplicated hits (4 after 4), duplicated misses (9 after 9), 
                # or a false miss that arrives after a hit (9 after 4)
                pass
        else:
            # This is a completely new, valid shot
            lanes_data[lane]['shots'] += 1
            lanes_data[lane]['last_shot_time'] = current_time
            lanes_data[lane]['last_shot_type'] = msg_type

            if len(flaps_str) == 5:
                lanes_data[lane]['flaps'] = list(flaps_str)

    elif msg_type == '3':
        # 3[Lane##] (Acoustic shot)
        # Explicitly ignore acoustic triggers for counting shots to avoid double counting
        pass

    elif msg_type == '5':
        # 5[Lane##][Time][Flaps#####] - Completion
        athlete_number = lanes_data[lane]['number']
        shots = lanes_data[lane]['shots']

        # Calculate total time spent on the range (Difference between Code 5 and Code 2)
        arrival = lanes_data[lane].get('arrival_time', 0.0)
        range_time = 0.0
        if arrival > 0.0:
            # Calculate and round to 1 decimal place (e.g., 28.5 seconds)
            range_time = round(time.time() - arrival, 1)

        # If code 5 contains target status at the end of the string:
        flaps_str = msg[9:14] if len(msg) >= 14 else "".join(lanes_data[lane]['flaps'])

        if athlete_number: # Save only if there was an athlete
            save_result_to_csv(lane, athlete_number, shots, flaps_str, range_time)

        # Clear the shooting lane, including arrival time and debounce variables
        lanes_data[lane] = {
            'time': '', 'number': '', 'flaps': ['0', '0', '0', '0', '0'], 
            'last': '', 'shots': 0, 'arrival_time': 0.0, 
            'last_shot_time': 0.0, 'last_shot_type': ''
        }

# --- ENDPOINTS ---

@app.route('/reset', methods=['POST'])
def reset_data():
    """Resets all lanes to their initial empty state."""
    for i in range(1, 31):
        lanes_data[i] = {
            'time': '', 'number': '', 'flaps': ['0', '0', '0', '0', '0'], 
            'last': '', 'shots': 0, 'arrival_time': 0.0, 
            'last_shot_time': 0.0, 'last_shot_type': ''
        }
    return "OK", 200

@app.route('/reset_csv', methods=['POST'])
def reset_csv():
    """Clears all historical data from the CSV file."""
    # Overwrite the file with just the headers to clear all data
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Date', 'Time', 'Athlete Number', 'Lane', 'Shots Fired', 'Flaps Result', 'Range Time (s)'])
    return "OK", 200

@app.route('/feed', methods=['GET'])
def feed_data():
    """Endpoint for receiving data (you can send GET requests here from ESP32)"""
    msg = request.args.get('msg', '')

    if msg:
        # Get current time
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

        # Add the raw message to our log
        raw_messages_log.appendleft({'time': timestamp, 'msg': msg})

        process_kes_message(msg)

    return "OK", 200

@app.route('/xml', methods=['GET'])
def get_xml():
    """Returns current status in XML format."""
    xml_template = """<?xml version="1.0" encoding="UTF-8"?>
<data>
{% for lane_id, data in lanes.items() %}    <lane>
        <time>{{ data.time }}</time>
        <number>{{ data.number }}</number>
        <flap1>{{ data.flaps[0] if data.flaps[0] != '0' else '' }}</flap1>
        <flap2>{{ data.flaps[1] if data.flaps[1] != '0' else '' }}</flap2>
        <flap3>{{ data.flaps[2] if data.flaps[2] != '0' else '' }}</flap3>
        <flap4>{{ data.flaps[3] if data.flaps[3] != '0' else '' }}</flap4>
        <flap5>{{ data.flaps[4] if data.flaps[4] != '0' else '' }}</flap5>
        <last>{{ data.last }}</last>
        <shots>{{ data.shots if data.shots > 0 else '' }}</shots>
    </lane>
{% endfor %}</data>"""
    xml_data = render_template_string(xml_template, lanes=lanes_data)
    return Response(xml_data, mimetype='text/xml')

@app.route('/', methods=['GET'])
def index():
    """Main page with target visualization and reset button."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Biathlon Live Status</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px; }
            .header-container { display: flex; justify-content: space-between; align-items: center; }
            table { width: 100%; border-collapse: collapse; background: white; text-align: center; margin-top: 15px; }
            th, td { border: 1px solid #ddd; padding: 12px; }
            th { background-color: #333; color: white; }
            .target-box { display: flex; justify-content: center; gap: 5px; }
            .target { width: 24px; height: 24px; border-radius: 50%; display: inline-block; border: 2px solid #333; }
            .miss { background-color: #222; } /* Black circle - target is open (miss) */
            .hit { background-color: white; border: 2px solid #222; } /* White circle - target is closed (hit) */

            /* Styles for the Reset button */
            .reset-btn {
                background-color: #d9534f;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 16px;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
            }
            .reset-btn:hover { background-color: #c9302c; }
        </style>
        <meta http-equiv="refresh" content="2">
    </head>
    <body>
        <div class="header-container">
            <h2>Live Shooting Status</h2>
            <button class="reset-btn" onclick="resetAllLanes()">RESET ALL DATA</button>
        </div>

        <table>
            <tr><th>Lane</th><th>Athlete Number</th><th>Shots</th><th>Flaps Status</th></tr>
            {% for lane_id, data in lanes.items() %}
                {# Show only lanes where there is an athlete or shots were fired #}
                {% if data.number or data.shots > 0 %}
                <tr>
                    <td><b>{{ lane_id }}</b></td>
                    <td>{{ data.number }}</td>
                    <td>{{ data.shots }}</td>
                    <td>
                        <div class="target-box">
                            {% for flap in data.flaps %}
                                <div class="target {% if flap == '1' %}hit{% else %}miss{% endif %}"></div>
                            {% endfor %}
                        </div>
                    </td>
                </tr>
                {% endif %}
            {% endfor %}
        </table>

        <script>
            // Function to handle the reset action
            function resetAllLanes() {
                // Ask for confirmation to prevent accidental resets
                if (confirm("Are you sure you want to clear all active lanes? This cannot be undone.")) {
                    fetch('/reset', { method: 'POST' })
                    .then(response => {
                        if (response.ok) {
                            // Force reload the page immediately to show empty state
                            window.location.reload();
                        } else {
                            alert("Error resetting data.");
                        }
                    });
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, lanes=lanes_data)

@app.route('/results', methods=['GET'])
def results():
    """Results page with filtering and clear history button."""
    results_data = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results_data = list(reader)

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Biathlon Results</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 20px; }
            .header-container { display: flex; justify-content: space-between; align-items: center; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; text-align: center; }
            th, td { border: 1px solid #aaa; padding: 8px; }
            th { background-color: #eee; }
            .filters { margin-bottom: 20px; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; display: flex; align-items: center; gap: 10px; }
            input { padding: 5px; }

            /* Styles for the clear history button */
            .clear-btn {
                background-color: #d9534f;
                color: white;
                border: none;
                padding: 8px 15px;
                font-size: 14px;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
                margin-left: auto; /* Push to the right edge of the filter box */
            }
            .clear-btn:hover { background-color: #c9302c; }
        </style>
    </head>
    <body>
        <div class="header-container">
            <h2>Shooting Results History</h2>
        </div>

        <div class="filters">
            <b>Filters:</b>
            <input type="text" id="dateFilter" placeholder="Date (YYYY-MM-DD)" onkeyup="filterTable()">
            <input type="text" id="athleteFilter" placeholder="Athlete Number" onkeyup="filterTable()">
            <input type="text" id="laneFilter" placeholder="Lane Number" onkeyup="filterTable()">

            <button class="clear-btn" onclick="clearHistory()">CLEAR HISTORY</button>
        </div>

        <table id="resultsTable">
            <tr class="header">
                <th>Date</th><th>Time</th><th>Athlete Number</th><th>Lane</th><th>Shots Fired</th><th>Flaps Result</th><th>Range Time (s)</th>
            </tr>
            {% for row in results %}
            <tr>
                <td>{{ row.get('Date', '') }}</td>
                <td>{{ row.get('Time', '') }}</td>
                <td>{{ row.get('Athlete Number', '') }}</td>
                <td>{{ row.get('Lane', '') }}</td>
                <td>{{ row.get('Shots Fired', '') }}</td>
                <td>{{ row.get('Flaps Result', '') }}</td>
                <td><b>{{ row.get('Range Time (s)', '') }}</b></td>
            </tr>
            {% endfor %}
        </table>

        <script>
            function filterTable() {
                // Get filter values and trim whitespace
                var dateF = document.getElementById("dateFilter").value.toUpperCase().trim();
                var athleteF = document.getElementById("athleteFilter").value.toUpperCase().trim();
                var laneF = document.getElementById("laneFilter").value.toUpperCase().trim();

                var table = document.getElementById("resultsTable");
                var tr = table.getElementsByTagName("tr");

                for (var i = 1; i < tr.length; i++) {
                    var tdDate = tr[i].getElementsByTagName("td")[0];
                    var tdAthlete = tr[i].getElementsByTagName("td")[2];
                    var tdLane = tr[i].getElementsByTagName("td")[3];

                    if (tdDate && tdAthlete && tdLane) {
                        var dateVal = tdDate.textContent || tdDate.innerText;
                        var athleteVal = tdAthlete.textContent || tdAthlete.innerText;
                        var laneVal = tdLane.textContent || tdLane.innerText;

                        // Partial match for date and athlete
                        var dateMatch = dateVal.toUpperCase().indexOf(dateF) > -1;
                        var athleteMatch = athleteVal.toUpperCase().indexOf(athleteF) > -1;

                        // Exact match for lane
                        var laneMatch = (laneF === "") || (laneVal.trim() === laneF);

                        // Row is visible only if all three conditions are met
                        if (dateMatch && athleteMatch && laneMatch) {
                            tr[i].style.display = "";
                        } else {
                            tr[i].style.display = "none";
                        }
                    }
                }
            }

            // Function to handle the CSV reset action
            function clearHistory() {
                if (confirm("Are you sure you want to delete ALL results history? This will erase the CSV file and cannot be undone.")) {
                    fetch('/reset_csv', { method: 'POST' })
                    .then(response => {
                        if (response.ok) {
                            // Force reload the page immediately to show empty table
                            window.location.reload();
                        } else {
                            alert("Error clearing history.");
                        }
                    });
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, results=results_data)

@app.route('/monitor', methods=['GET'])
def monitor():
    """Page to monitor raw incoming COM port data in real-time."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>COM Port Monitor</title>
        <style>
            /* Terminal-like styling for the monitor */
            body { font-family: 'Courier New', Courier, monospace; background-color: #1e1e1e; color: #00ff00; padding: 20px; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; background: #2d2d2d; }
            th, td { border: 1px solid #444; padding: 8px; text-align: left; }
            th { background-color: #111; color: #fff; font-family: Arial, sans-serif; }
            .header-container { display: flex; justify-content: space-between; align-items: center; font-family: Arial, sans-serif; color: white;}
            .back-link { color: #00ff00; text-decoration: none; padding: 5px 10px; border: 1px solid #00ff00; border-radius: 4px;}
            .back-link:hover { background-color: #00ff00; color: #1e1e1e; }
        </style>
        <meta http-equiv="refresh" content="1">
    </head>
    <body>
        <div class="header-container">
            <h2>Raw COM Port Data Monitor</h2>
            <a href="/" class="back-link">&larr; Back to Live Status</a>
        </div>

        <table>
            <tr>
                <th style="width: 150px;">Timestamp</th>
                <th>Raw Message (KES Protocol)</th>
            </tr>
            {% for row in logs %}
            <tr>
                <td>{{ row.time }}</td>
                <td>{{ row.msg }}</td>
            </tr>
            {% endfor %}
            {% if not logs %}
            <tr>
                <td colspan="2" style="text-align: center; color: #aaa;">No data received yet...</td>
            </tr>
            {% endif %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html, logs=raw_messages_log)

@app.route('/xmlresults', methods=['GET'])
def get_xml_results():
    """Returns the complete history of results from the CSV file in XML format."""
    results_data = []

    # Read data from CSV if it exists
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results_data = list(reader)

    # Template for generating the XML structure
    xml_template = """<?xml version="1.0" encoding="UTF-8"?>
<results>
{% for row in results %}    <result>
        <date>{{ row.get('Date', '') }}</date>
        <time>{{ row.get('Time', '') }}</time>
        <athlete>{{ row.get('Athlete Number', '') }}</athlete>
        <lane>{{ row.get('Lane', '') }}</lane>
        <shots>{{ row.get('Shots Fired', '') }}</shots>
        <flaps>{{ row.get('Flaps Result', '') }}</flaps>
        <range_time>{{ row.get('Range Time (s)', '') }}</range_time>
    </result>
{% endfor %}</results>"""

    # Render the template with the CSV data
    xml_data = render_template_string(xml_template, results=results_data)

    # Return the response with the correct XML MIME type
    return Response(xml_data, mimetype='text/xml')

if __name__ == '__main__':
    # Start the server. Accessible from the network via your IP (0.0.0.0) on port 80
    app.run(host='0.0.0.0', port=80, debug=True)
