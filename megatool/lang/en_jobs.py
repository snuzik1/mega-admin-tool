"""English texts of the job log (megatool/jobs.py)."""

DATA = {
    "jobs": {
        "cwd": "# cwd: {cwd}",
        "programNotFound": "program not found: {name}",
        "finished": "# finished: {status} (code {code}), {duration}",
        "cancelRequested": "# stop requested",
        "notFound": "job not found",
        "notRunning": "job is not running (status {status})",
        "tooMany": (
            "Too many concurrent jobs (limit {limit}). Wait for the current "
            "ones to finish or raise jobs.max_active in the settings."
        ),
    }
}
