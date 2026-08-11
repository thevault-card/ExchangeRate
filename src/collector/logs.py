# src/collector/logs.py
"""stdout JSON 로그 한 줄. jobs.py 와 sources.py 가 함께 쓴다.

jobs.py 가 sources.py 를 import 하므로, sources.py 가 jobs.py 를 import 하면
순환이 된다. 그래서 로깅만 이 파일로 분리했다.
"""
import json
from datetime import datetime

from .config import KST


def log(**fields) -> None:
    # ts 는 항상 맨 앞에 찍는다. batch_id 가 계정명(vaultuser)으로 바뀌면서
    # "이 줄이 어느 실행인지" 를 알려주는 건 job + ts 뿐이다.
    print(json.dumps({"ts": datetime.now(KST).isoformat(timespec="seconds"), **fields},
                     ensure_ascii=False, default=str), flush=True)
