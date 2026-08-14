#!/usr/bin/env bash
#
# crontab 진입점. 시간대에 따라 무엇을 돌릴지 인자로 정한다.
#
#   run_all.sh morning    index_spx                 (미국 장 마감 직후)
#   run_all.sh evening    index_kospi, fx_daily     (코스피 마감·환율 고시 후)
#
# 실패해도 재시도하지 않는다. 실패한 줄을 logs/errors.log 에 남기고 stderr 로도
# 내보낸 뒤 종료코드 1 로 끝낸다. cron 은 출력이 있으면 MAILTO 주소로 보내므로,
# 그 두 가지가 "에러가 전달되는" 경로다. 같은 실행을 계속 되풀이해봐야 소스가
# 죽어 있으면 결과가 같고, 데이터는 다음 정규 실행이 최근 구간을 다시 훑어 메운다.
#
# 한 배치가 실패해도 나머지는 계속 시도한다 - 환율이 죽었다고 지수까지 안 받을
# 이유가 없다.
#
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
mkdir -p logs

case "${1:-}" in
    morning) JOBS=(index_spx) ;;
    evening) JOBS=(index_kospi fx_daily) ;;
    *) echo "usage: $0 morning|evening" >&2; exit 2 ;;
esac

# cron 은 PATH 가 최소라 uv 를 못 찾는다. 설치 위치를 명시하되 환경변수로 덮을 수 있게.
UV="${UV:-$HOME/.local/bin/uv}"
[ -x "$UV" ] || { echo "uv 를 찾을 수 없다: $UV" >&2; exit 2; }

# 기본은 운영(.env = vaultdb). 로컬에서 흉내내볼 때만 ENV_FILE=.env.local 로 덮는다.
ENV_FILE="${ENV_FILE:-.env}"
[ -f "$ENV_FILE" ] || { echo "환경 파일이 없다: $ENV_FILE" >&2; exit 2; }

# 같은 시간대가 겹쳐 도는 것만 막는다. 아침/저녁은 서로 막지 않는다.
# flock 이 없는 환경(Git Bash 등)에서는 잠금 없이 진행한다 - 잠금이 없다는 이유로
# 수집을 건너뛰면 "이미 실행 중"과 구분이 안 되는 조용한 미수집이 된다.
if command -v flock >/dev/null 2>&1; then
    exec 9>"logs/.lock.$1"
    if ! flock -n 9; then
        echo "{\"event\":\"skip\",\"reason\":\"already_running\",\"slot\":\"$1\"}" >&2
        exit 0
    fi
else
    echo "{\"event\":\"warn\",\"reason\":\"flock_unavailable\",\"slot\":\"$1\"}" >&2
fi

rc=0
for job in "${JOBS[@]}"; do
    if ! "$UV" run --env-file "$ENV_FILE" python -m collector "$job" >> "logs/$job.log" 2>&1; then
        rc=1
        # 마지막 줄이 배치가 남긴 실패 로그(JSON 한 줄)다. 예외로 죽었으면 traceback 꼬리.
        tail -n 1 "logs/$job.log" | tee -a logs/errors.log >&2
    fi
done

exit $rc
