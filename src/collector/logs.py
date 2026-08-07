# src/collector/logs.py
"""stdout JSON 로그 한 줄. jobs.py 와 sources.py 가 함께 쓴다.

jobs.py 가 sources.py 를 import 하므로, sources.py 가 jobs.py 를 import 하면
순환이 된다. 그래서 로깅만 이 파일로 분리했다.
"""
import json


def log(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)
