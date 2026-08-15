# tests/test_logs.py
"""로그가 어디로 나가고 어떤 모양인지. (설계 §9-2)

이 구분이 곧 알림 경로다 — cron 은 명령이 stderr 에 뭔가 쓰면 MAILTO 로 보낸다.
성공까지 stderr 로 새면 매 실행마다 메일이 와서 진짜 실패가 묻힌다.
"""
import json

from collector.logs import log


def _split(capsys):
    captured = capsys.readouterr()
    return captured.out.strip().splitlines(), captured.err.strip().splitlines()


def test_normal_log_goes_to_stdout_only(capsys):
    log(job="index_spx", status="up_to_date", latest_date="2026-08-13")
    out, err = _split(capsys)

    assert len(out) == 1
    assert err == [], "성공 로그가 stderr 로 새면 cron 이 매번 메일을 보낸다"

    line = json.loads(out[0])
    assert line["job"] == "index_spx"
    assert line["status"] == "up_to_date"
    assert line["ts"].startswith("20"), "ts 가 맨 앞에 있어야 실행을 식별할 수 있다"


def test_failure_goes_to_stderr_only(capsys):
    log(job="fx_daily", status="failure", error="수출입은행 호출 실패: SSLError")
    out, err = _split(capsys)

    assert out == [], "같은 줄이 양쪽에 찍히면 로그가 두 배로 쌓인다"
    assert len(err) == 1
    assert json.loads(err[0])["status"] == "failure"


def test_error_field_alone_is_enough_to_be_stderr(capsys):
    """status 없이 error 만 있는 경로도 있다 (알 수 없는 배치명)."""
    log(error="알 수 없는 배치: nope", available=["fx_daily"])
    out, err = _split(capsys)

    assert out == []
    assert json.loads(err[0])["error"].startswith("알 수 없는 배치")


def test_output_is_one_json_line_per_call(capsys):
    """줄바꿈이 섞이면 로그 수집기가 한 줄씩 파싱하지 못한다."""
    log(job="a", status="success", note="여러 줄\n처럼 보이는 값")
    out, _ = _split(capsys)

    assert len(out) == 1
    assert json.loads(out[0])["note"] == "여러 줄\n처럼 보이는 값"
