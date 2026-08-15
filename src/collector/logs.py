# src/collector/logs.py
"""JSON 한 줄 로그. jobs.py 와 sources.py 가 함께 쓴다. (설계 §9-2)

표준 `logging` 위에 얇게 얹었다. 부르는 쪽은 `log(**fields)` 하나만 알면 되고,
어디로 나갈지·어떤 모양으로 찍힐지는 여기서만 정한다.

**실패는 stderr, 나머지는 stdout 으로 간다.** cron 은 명령이 stderr 에 뭔가를 쓰면
그걸 MAILTO 주소로 보내므로, 이 구분이 곧 알림 경로다. 성공 로그까지 stderr 로
나가면 매 실행마다 메일이 와서 진짜 실패가 묻힌다.

jobs.py 가 sources.py 를 import 하므로, sources.py 가 jobs.py 를 import 하면
순환이 된다. 그래서 로깅만 이 파일로 분리했다.
"""
import json
import logging
import sys
from datetime import datetime

from .config import KST

# jobs.JobStatus.FAILURE 와 같은 값. jobs 를 import 하면 순환이라 문자열로 둔다.
_FAILURE = "failure"


class _LazyStream:
    """sys.stdout/stderr 를 호출 시점에 찾아 쓴다.

    핸들러를 만들 때 스트림 객체를 붙잡아두면, 나중에 그 자리를 바꿔치기한 쪽
    (pytest 의 capsys 가 그렇게 한다)이 출력을 못 잡는다.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def write(self, text: str) -> None:
        getattr(sys, self._name).write(text)

    def flush(self) -> None:
        getattr(sys, self._name).flush()


class _JsonLine(logging.Formatter):
    """레코드에 실린 필드를 JSON 한 줄로. 시각은 로그가 찍힌 순간을 쓴다."""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {"message": record.getMessage()})
        stamp = datetime.fromtimestamp(record.created, KST).isoformat(timespec="seconds")
        line = json.dumps({"ts": stamp, **fields}, ensure_ascii=False, default=str)
        if record.exc_info:
            # 예상 못 한 예외. JSON 한 줄 뒤에 traceback 을 붙여 원인을 남긴다.
            line += "\n" + self.formatException(record.exc_info)
        return line


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("collector")
    if logger.handlers:  # 여러 번 import 돼도 핸들러가 겹치지 않게
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False  # 루트 로거로 새어 나가 두 번 찍히는 것을 막는다

    out = logging.StreamHandler(_LazyStream("stdout"))
    out.addFilter(lambda record: record.levelno < logging.ERROR)
    out.setFormatter(_JsonLine())

    err = logging.StreamHandler(_LazyStream("stderr"))
    err.setLevel(logging.ERROR)
    err.setFormatter(_JsonLine())

    logger.addHandler(out)
    logger.addHandler(err)
    return logger


_logger = _build_logger()


def log(**fields) -> None:
    """JSON 한 줄을 남긴다. 실패면 stderr(ERROR), 나머지는 stdout(INFO).

    ts 는 항상 맨 앞에 찍는다. batch_id 가 계정명(vaultuser)으로 바뀌면서
    "이 줄이 어느 실행인지" 를 알려주는 건 job + ts 뿐이다.
    """
    failed = fields.get("status") == _FAILURE or "error" in fields
    _logger.log(logging.ERROR if failed else logging.INFO, "", extra={"fields": fields})
