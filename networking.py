import subprocess

def setup_network():
    """Create a virtual network for containers."""
    subprocess.run(["ip", "link", "add", "veth1", "type", "veth"])
    subprocess.run(["ip", "addr", "add", "192.168.1.101/24", "dev", "veth1"])
    subprocess.run(["ip", "link", "set", "veth1", "up"])
