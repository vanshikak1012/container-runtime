from flask import Flask, jsonify, request, session, render_template
from flask_cors import CORS
import psutil, subprocess, threading, time, sys, os, sqlite3, signal, smtplib
from datetime import datetime
from collections import defaultdict, deque
from statistics import mean, stdev, StatisticsError
from email.mime.text import MIMEText

# === ML imports ===
try:
    from sklearn.ensemble import IsolationForest
    SKLEARN_OK = True
except Exception:
    SKLEARN_OK = False

# ---------------- Constants & Config ----------------
PYTHON = sys.executable
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "supersecretkey123")
CORS(app, supports_credentials=True)

USERNAME = os.environ.get("APP_USER", "admin")
PASSWORD = os.environ.get("APP_PASS", "pass123")

# Email settings
EMAIL_ENABLED = True
EMAIL_TO = os.environ.get("EMAIL_TO", "vanshikak1012@gmail.com")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "vanshikak1012@gmail.com")
EMAIL_SERVER = os.environ.get("EMAIL_SMTP", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USER = os.environ.get("EMAIL_USER", EMAIL_FROM)
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")  # Supports spaces
LAST_EMAIL_SENT = defaultdict(lambda: 0.0)
EMAIL_COOLDOWN = 300  # seconds (5 min)

# Containers (simulated services)
container_configs = {
    "nginx": {"command": [PYTHON, "-m", "http.server", "8000"], "pid": None},
    "mysql": {"command": [PYTHON, "-c", "while True: pass"], "pid": None}
}

history = {name: {"cpu": deque(maxlen=300), "mem": deque(maxlen=300)} for name in container_configs}
last_restart_at = defaultdict(lambda: 0.0)
history_lock = threading.Lock()

last_snapshot = {name: {"container": name, "cpu": 0.0, "memory": 0.0, "status": "Stopped", "reason": ""} 
                 for name in container_configs}

models = {name: None for name in container_configs}
train_buffers = {name: deque(maxlen=500) for name in container_configs}
MIN_TRAIN_SAMPLES = 60
RETRAIN_EVERY = 120
last_trained_at = defaultdict(lambda: 0.0)

DB_FILE = "monitor.db"

# ---------------- DB Setup ----------------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            container TEXT,
            cpu REAL,
            memory REAL,
            status TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            container TEXT,
            cpu REAL,
            memory REAL,
            reason TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            container TEXT,
            event TEXT
        )""")
        conn.commit()
init_db()

# ---------------- Helpers ----------------
def db_insert(table, **kwargs):
    with sqlite3.connect(DB_FILE) as conn:
        cols = ", ".join(kwargs.keys())
        vals = tuple(kwargs.values())
        placeholders = ", ".join("?" * len(kwargs))
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", vals)
        conn.commit()

def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def process_exists(pid):
    return bool(pid) and psutil.pid_exists(pid)

def start_container(name):
    config = container_configs.get(name)
    if config and config["pid"] is None:
        process = subprocess.Popen(config["command"])
        config["pid"] = process.pid
        db_insert("logs", timestamp=now_ts(), container=name, event=f"STARTED (PID {process.pid})")

def stop_container(name):
    config = container_configs.get(name)
    pid = config["pid"]
    if process_exists(pid):
        try:
            psutil.Process(pid).terminate()
        except Exception as e:
            db_insert("logs", timestamp=now_ts(), container=name, event=f"STOP ERROR: {e}")
        config["pid"] = None
        db_insert("logs", timestamp=now_ts(), container=name, event=f"STOPPED (PID {pid})")

def restart_container(name):
    stop_container(name)
    time.sleep(1)
    start_container(name)
    db_insert("logs", timestamp=now_ts(), container=name, event="RESTARTED")

# --- Simple anomaly rules ---
def simple_anomaly(name, cpu, mem):
    with history_lock:
        cpu_hist = list(history[name]["cpu"])
        mem_hist = list(history[name]["mem"])
    if len(cpu_hist) < 10 or len(mem_hist) < 10:
        return False, ""
    def zscore(val, series):
        try:
            s = stdev(series)
            return 0.0 if s == 0 else (val - mean(series)) / s
        except StatisticsError:
            return 0.0
    cpu_z, mem_z = zscore(cpu, cpu_hist), zscore(mem, mem_hist)
    cpu_spike = cpu > 2.0 * max(1e-6, mean(cpu_hist))
    mem_spike = mem > 2.0 * max(1e-6, mean(mem_hist))
    if abs(cpu_z) >= 3: return True, f"CPU z={cpu_z:.2f}"
    if abs(mem_z) >= 3: return True, f"MEM z={mem_z:.2f}"
    if cpu_spike: return True, "CPU > 2x moving avg"
    if mem_spike: return True, "MEM > 2x moving avg"
    return False, ""

def build_feature(name, cpu, mem):
    with history_lock:
        cpu_hist = history[name]["cpu"]
        mem_hist = history[name]["mem"]
        prev_cpu = cpu_hist[-1] if len(cpu_hist) else 0.0
        prev_mem = mem_hist[-1] if len(mem_hist) else 0.0
    dcpu = cpu - prev_cpu
    dmem = mem - prev_mem
    return [cpu, mem, dcpu, dmem]

def maybe_train_model(name):
    if not SKLEARN_OK: return
    now = time.time()
    if len(train_buffers[name]) < MIN_TRAIN_SAMPLES: return
    if now - last_trained_at[name] < RETRAIN_EVERY: return
    model = IsolationForest(n_estimators=200, max_samples="auto", contamination="auto", random_state=42)
    X = list(train_buffers[name])
    model.fit(X)
    models[name] = model
    last_trained_at[name] = now
    db_insert("logs", timestamp=now_ts(), container=name, event="ML MODEL TRAINED")

def ml_anomaly(name, cpu, mem):
    if not SKLEARN_OK: return None, ""
    model = models.get(name)
    if model is None: return None, ""
    feat = [build_feature(name, cpu, mem)]
    pred = model.predict(feat)[0]
    score = model.decision_function(feat)[0]
    if pred == -1: return True, f"IForest outlier (score={score:.3f})"
    return False, f"IForest normal (score={score:.3f})"

# --- Send Email Alert (Updated for spaces in password) ---
def send_email_alert(container, cpu, mem, reason):
    if not EMAIL_ENABLED:
        print("⚠️ Email alerts are disabled.")
        return

    now = time.time()
    if now - LAST_EMAIL_SENT[container] < EMAIL_COOLDOWN:
        print(f"⚠️ Email cooldown active for {container}, skipping alert.")
        return

    subject = f"⚠️ Container Anomaly: {container}"
    body = (
        f"Anomaly detected for container {container}.\n\n"
        f"CPU: {cpu:.2f}%\n"
        f"Memory: {mem:.2f}MB\n"
        f"Reason: {reason}\n"
        f"Timestamp: {now_ts()}"
    )

    msg = MIMEText(body)
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject

    # Debug (optional)
    print(f"🔑 Using EMAIL_PASS: '{EMAIL_PASS}'")

    try:
        print(f"📨 Sending email from {EMAIL_FROM} to {EMAIL_TO} via {EMAIL_SERVER}:{EMAIL_PORT}")
        server = smtplib.SMTP(EMAIL_SERVER, EMAIL_PORT, timeout=10)
        server.set_debuglevel(1)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        server.quit()
        LAST_EMAIL_SENT[container] = now
        print(f"✅ Email successfully sent for anomaly: {container}")
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ SMTP Authentication Error: {e}")
    except smtplib.SMTPConnectError as e:
        print(f"❌ SMTP Connection Error: {e}")
    except smtplib.SMTPRecipientsRefused as e:
        print(f"❌ Recipient Refused: {e}")
    except smtplib.SMTPException as e:
        print(f"❌ SMTP Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error sending email: {e}")

# ---------------- Monitoring Loop ----------------
def monitor_and_autoheal():
    while True:
        for name, config in container_configs.items():
            pid = config["pid"]
            cpu, mem = 0.0, 0.0
            status, reason = "Stopped", ""
            if process_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    cpu = proc.cpu_percent(interval=0.2)
                    mem = proc.memory_info().rss / (1024*1024)
                    status = "Healthy" if cpu <= 0.1 and mem <= 0.1 else "High usage"
                except Exception as e:
                    status, reason = "Crashed", f"psutil error: {e}"
            else:
                restart_container(name)
                status, reason = "Crashed", "Process not running"

            with history_lock:
                history[name]["cpu"].append(cpu)
                history[name]["mem"].append(mem)

            if status == "Healthy":
                feat = build_feature(name, cpu, mem)
                train_buffers[name].append(feat)
                maybe_train_model(name)

            ml_flag, ml_reason = ml_anomaly(name, cpu, mem)
            if ml_flag is True:
                status, reason = "Anomaly", ml_reason
            elif ml_flag is None:
                s_flag, s_reason = simple_anomaly(name, cpu, mem)
                if s_flag:
                    status, reason = "Anomaly", s_reason

            last_snapshot[name] = {"container": name, "cpu": round(cpu,2), "memory": round(mem,2),
                                   "status": status, "reason": reason}

            db_insert("metrics", timestamp=now_ts(), container=name,
                      cpu=round(cpu,2), memory=round(mem,2), status=status)

            if status == "Anomaly":
                db_insert("anomalies", timestamp=now_ts(), container=name,
                          cpu=round(cpu,2), memory=round(mem,2), reason=reason)
                db_insert("logs", timestamp=now_ts(), container=name,
                          event=f"ANOMALY: {reason} (CPU {cpu:.2f}%, MEM {mem:.2f}MB)")
                send_email_alert(name, cpu, mem, reason)
                if time.time() - last_restart_at[name] >= 30:
                    restart_container(name)
                    last_restart_at[name] = time.time()
                    db_insert("logs", timestamp=now_ts(), container=name, event="AUTO-HEAL RESTART")
        time.sleep(5)

# ---------------- Startup ----------------
for name in container_configs:
    start_container(name)

monitor_thread = threading.Thread(target=monitor_and_autoheal, daemon=True)
monitor_thread.start()

def shutdown_handler(sig, frame):
    db_insert("logs", timestamp=now_ts(), container="SYSTEM", event="SHUTDOWN SIGNAL RECEIVED")
    for name in container_configs:
        stop_container(name)
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("frontend.html")

@app.route("/autoheal/status")
def autoheal_status():
    return jsonify([last_snapshot[name] for name in container_configs])

@app.route("/send-email-anomaly", methods=["GET", "POST"])
def test_email():
    """
    Test route to send an anomaly email.
    Works via GET (browser) and POST (API/cURL).
    """
    send_email_alert("nginx", 90, 150, "TEST ANOMALY")
    return jsonify({"success": True, "message": "Test email triggered"})

@app.route("/metrics")
def metrics():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT * FROM metrics ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(zip(["id","timestamp","container","cpu","memory","status"], row)) for row in rows])

@app.route("/anomalies")
def get_anomalies():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT * FROM anomalies ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(zip(["id","timestamp","container","cpu","memory","reason"], row)) for row in rows])

@app.route("/logs")
def get_logs():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(zip(["id","timestamp","container","event"], row)) for row in rows])

@app.route("/control/<name>/<action>", methods=["POST"])
def control_container_route(name, action):
    if name not in container_configs: return "Invalid container name", 404
    if action=="start": start_container(name)
    elif action=="stop": stop_container(name)
    elif action=="restart": restart_container(name)
    else: return "Invalid action", 400
    return "OK", 200

@app.route("/stats")
def stats_alias(): return autoheal_status()

@app.route("/predict")
def predict_alias(): return predictions()

@app.route("/predictions")
def predictions():
    data=[]
    for name in container_configs:
        snap = last_snapshot[name]
        predicted_cpu = snap["cpu"]*0.9 + 5
        predicted_mem = snap["memory"]*0.95 + 10
        data.append({
            "container": name,
            "actual_cpu": snap["cpu"],
            "actual_mem": snap["memory"],
            "predicted_cpu": round(predicted_cpu,2),
            "predicted_mem": round(predicted_mem,2)
        })
    return jsonify(data)

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if data and data.get("username")==USERNAME and data.get("password")==PASSWORD:
        session["logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

@app.before_request
def require_login():
    public_endpoints = ["login","static","index","metrics","get_anomalies","get_logs",
                        "control_container_route","autoheal_status","stats_alias","predict_alias","predictions","test_email"]
    if request.endpoint not in public_endpoints and not session.get("logged_in"):
        return jsonify({"error":"Unauthorized"}),401

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
