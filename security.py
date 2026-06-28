import os

def secure_container(name):
    """Apply security policies to container."""
    seccomp_policy = f"/var/lib/containers/{name}/seccomp.json"
    os.system(f"seccomp-bpf --policy {seccomp_policy}")
    print(f"Security policies applied to {name}")
