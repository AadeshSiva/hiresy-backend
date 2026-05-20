import subprocess

services = [
    ("agent_orchestrator", 8000),
    ("agent_evaluator", 8001),
    ("agent_test", 8002),
    ("agent_communication", 8003),
    ("agent_coding", 8004),
    ("agent_live", 8005),
    ("agent_bgv", 8006),
    ("agent_offer", 8007),
]

processes = []

for service, port in services:
    cmd = [
        "uvicorn",
        f"{service}.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--reload",
    ]

    print(f"Starting {service} on port {port}")

    p = subprocess.Popen(cmd)
    processes.append(p)

for p in processes:
    p.wait()