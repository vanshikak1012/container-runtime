import os
import subprocess

containers = {}

def create_container(name):
    """Create an isolated container with namespaces, cgroups, and networking."""
    pid = os.fork()
    if pid == 0:  # Child process
        os.unshare(os.CLONE_NEWUTS | os.CLONE_NEWPID | os.CLONE_NEWNS | os.CLONE_NEWNET)

        # Set network
        subprocess.run(["ip", "link", "add", "veth0", "type", "veth"])
        subprocess.run(["ip", "addr", "add", "192.168.1.100/24", "dev", "veth0"])
        subprocess.run(["ip", "link", "set", "veth0", "up"])

        # Set resource limits (cgroups)
        cgroup_path = f"/sys/fs/cgroup/memory/{name}"
        os.makedirs(cgroup_path, exist_ok=True)
        with open(f"{cgroup_path}/memory.limit_in_bytes", "w") as f:
            f.write("100000000")  # 100MB limit
        with open(f"{cgroup_path}/cgroup.procs", "w") as f:
            f.write(str(os.getpid()))

        # Launch a restricted Bash shell inside container
        os.chroot("/var/lib/container_root")  
        os.execvp("bash", ["bash"])
    else:
        containers[name] = pid
        print(f"✅ Container '{name}' started with PID {pid}")

def stop_container(name):
    """Stop a running container."""
    if name in containers:
        os.kill(containers[name], 9)
        del containers[name]
        print(f"🛑 Container '{name}' stopped.")
