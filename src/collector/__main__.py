import sys

from .jobs import run

if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else ""
    # 인자를 안 주면 None 이다 — 평소 실행이라 스킵 판정을 거친다.
    # 일수를 명시하면 복구 실행으로 보고 그 범위를 다시 받는다. (jobs.run 참조)
    days = int(sys.argv[2]) if len(sys.argv) > 2 else None
    sys.exit(run(job, days))
