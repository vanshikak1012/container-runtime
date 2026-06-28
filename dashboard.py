from flask import Flask, jsonify
from container import containers
from flask import Flask

app = Flask(__name__)

@app.route("/")  # This handles requests to the home page
def home():
    return " Flask is running successfully!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


app = Flask(__name__)

@app.route("/status", methods=["GET"])
def get_status():
    """Get container status."""
    return jsonify({"running_containers": list(containers.keys())})

def start_dashboard():
    """Start the Flask dashboard."""
    app.run(host="0.0.0.0", port=5000)
