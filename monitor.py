import psutil
import time
from container import create_container, containers

def monitor_and_autoheal():
    while True:
        with open("logs.txt", "a") as f:  # Append mode to keep historical data
            for name, config in container_configs.items():
                pid = config["pid"]
                cpu = 0
                mem = 0

                if pid and psutil.pid_exists(pid):
                    try:
                        proc = psutil.Process(pid)
                        cpu = proc.cpu_percent(interval=0.5)
                        mem = proc.memory_info().rss / (1024 * 1024)  # Convert to MB

                        if cpu > 80 or mem > 200:
                            status = "High usage"
                            restart_container(name)
                        else:
                            status = "Healthy"
                    except Exception as e:
                        status = "Error"
                        print(f"[!] Error monitoring {name}: {e}")
                        cpu, mem = 0, 0
                else:
                    status = "Crashed"
                    cpu, mem = 0, 0
                    restart_container(name)

                log_entry = {
                    "container": name,
                    "cpu": round(cpu, 2),
                    "memory": round(mem, 2),
                    "status": status,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }

                # Save to in-memory log
                health_log.append(log_entry)
                if len(health_log) > 100:
                    health_log.pop(0)  # Keep log size reasonable in memory

                # Write to file
                log_line = f"{log_entry['timestamp']} | {name.upper()} | CPU: {log_entry['cpu']}% | MEM: {log_entry['memory']}MB | Status: {status}\n"
                f.write(log_line)

        time.sleep(5)
