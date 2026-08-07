import sys

from .jobs import DEFAULT_LOOKBACK_DAYS, run

if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else ""
    days = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LOOKBACK_DAYS
    sys.exit(run(job, days))
