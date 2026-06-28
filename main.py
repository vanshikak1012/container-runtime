from container import create_container
from monitor import monitor_containers
from dashboard import start_dashboard
import threading
import multiprocessing

def container_process(name):
    print(f"Container {name} running in process {multiprocessing.current_process().pid}")

def create_container(name):
    process = multiprocessing.Process(target=container_process, args=(name,))
    process.start()
    print(f"Started container '{name}' with PID {process.pid}")


if __name__ == "__main__":
    print("Starting AI-powered Minimalist Container Runtime...")
    
    # Start a sample container
    create_container("test_container")

    # Start monitoring in a separate thread
    monitor_thread = threading.Thread(target=monitor_containers, daemon=True)
    monitor_thread.start()

    # Start Flask monitoring dashboard
    start_dashboard()
