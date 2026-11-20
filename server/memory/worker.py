"""Do one job's slow work: process an upload or a glasses recording, reprocess a moment, or rebuild facts."""

import sys

from memory import db, jobs


def run(job_id: str) -> None:
    """Do the work a job describes and record how it went."""
    with db.connect() as conn:
        task = db.job_task(conn, job_id)

    if task is None:
        raise SystemExit(f"job {job_id} has nothing to do")

    if task["kind"] == "upload":
        jobs.process_upload(job_id, task)
    elif task["kind"] == "reprocess":
        jobs.run_reprocess(job_id, task["moment_id"], task["frames_per_minute"])
    elif task["kind"] == "glasses":
        jobs.process_glasses(job_id, task)
    elif task["kind"] == "facts":
        jobs.rebuild_facts(job_id, task["moment_ids"])


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m memory.worker <job id>")
        return 2

    run(sys.argv[1])

    return 0


if __name__ == "__main__":
    sys.exit(main())
