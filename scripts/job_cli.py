from __future__ import annotations

import argparse
from pathlib import Path

from caal.job_queue import JobQueue


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect CAAL job queue")
    parser.add_argument("--list", action="store_true", help="List all jobs in descending order")
    parser.add_argument("--job", type=str, help="Show detail for job ID")
    parser.add_argument("--status", nargs=2, metavar=("JOB_ID", "STATUS"), help="Update a job status")
    return parser.parse_args()


def main():
    args = parse_args()
    queue = JobQueue(path=Path("data/jobs.db"))

    if args.list:
        for job in queue.list_jobs():
            print(f"{job.job_id}: {job.topic} [{job.status}]")
        return

    if args.job:
        job = queue.get_job(args.job)
        if not job:
            print("Job not found.")
            return
        print(f"Job {job.job_id}:")
        print(f"  Topic: {job.topic}")
        print(f"  Query: {job.query}")
        print(f"  Status: {job.status}")
        print(f"  Result: {job.result or 'n/a'}")
        return

    if args.status:
        job_id, status = args.status
        queue.update_job(job_id, status)
        print(f"Updated job {job_id} to {status}.")
        return

    print("No action specified. Use --help.")


if __name__ == "__main__":
    main()
