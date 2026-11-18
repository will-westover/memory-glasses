"""Start slow work away from the request: in a thread on this machine, or as a Cloud Run job when deployed."""

import json
import os
import threading
import urllib.request

METADATA = "http://metadata.google.internal/computeMetadata/v1/"


def on_cloud_run() -> bool:
    """Return whether slow work should run as a Cloud Run job."""
    return bool(os.environ.get("WORKER_JOB"))


def start(job_id) -> None:
    """Run a job's work: as a Cloud Run job when deployed, else in a background thread."""
    if on_cloud_run():
        run_cloud_job(str(job_id))
        return

    from memory import worker

    threading.Thread(target=worker.run, args=(str(job_id),), daemon=True).start()


def metadata(path: str) -> str:
    """Read one value from the Cloud Run metadata server."""
    request = urllib.request.Request(METADATA + path, headers={"Metadata-Flavor": "Google"})

    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode()


def run_cloud_job(job_id: str) -> None:
    """Start one execution of the worker job for this job id, using the service's own Google identity."""
    token = json.loads(metadata("instance/service-accounts/default/token"))["access_token"]
    project = metadata("project/project-id")
    region = os.environ["WORKER_REGION"]
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{os.environ['WORKER_JOB']}:run"
    body = json.dumps({"overrides": {"containerOverrides": [{"args": [job_id]}]}}).encode()

    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()
