"""Production defaults sized for a 2 vCPU / 2 GB Tencent Cloud instance."""

import multiprocessing
import os


bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:5000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5
preload_app = True
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
capture_output = True

# Prevent accidental over-allocation if this file is reused on a smaller host.
workers = max(1, min(workers, max(2, multiprocessing.cpu_count())))
