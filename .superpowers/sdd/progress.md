# 진행 기록 — 2026-08-06-수집-파이프라인-구현

계획: `docs/plans/2026-08-06-수집-파이프라인-구현.md`

| Task | 상태 | 커밋 |
|---|---|---|
| 1 스키마 확정 | 완료 (전용 DB `exchangerate_dev` 로 전환해 재작성·적용) | 663d9be → 7adc691 |
| 2 프로젝트 뼈대·DB 접속 | 완료 (4 passed) | ddaceec |
| 3 UPSERT | 완료 (7 passed, 덮어쓰기 방향 관문 통과) | c3f41b3 |
| 4 yfinance 소스 | 완료 (7 passed, `^KS11` 실호출 확인) | 213ebea |
| 5 수출입은행 환율 소스 | 완료 (15 passed, 실호출은 키 대기) | 529318b |
| 6 실패 감지 | 완료 (11 passed) | 903993c |
| 7 배치 배선·백필 | 완료 (37 passed, 3년 백필 SPX 752행·KOSPI 729행) | 8038aa6 |
| 린트·타임존 수정 | 완료 (ruff 통과) | 611df66 |

| 전체 브랜치 리뷰 | 완료 (Critical 1 · Important 8 · Minor 다수) | — |
| 리뷰 1차 수정 | 완료 (C-1·I-3·I-4·I-5 + Minor 3 + 문서 2, 39 passed) | 1f61c02 |

## 미처리 리뷰 지적 (2차)

- **I-1** 토요일 SPX 0건이 "휴장(정상)" 으로 exit 0 — 주말 무음 구간
- **I-2** 공휴일에 `fx_daily`·`index_kospi` 가 거짓 실패(exit 1). 연휴 3일째 `check_staleness` 가 정확히 걸림
- **I-6** `jobs.py` 테스트 0건 · gold 뷰 테스트 0건 · 환율 경로 end-to-end 미실행
- **I-7** NaN 스킵이 무음 (설계 §9-1 은 "경고" 로 분류)
- **I-8** `fetch_fx` 재시도 없음 (스펙 §4-3)
- Minor: `previous` 를 UPSERT 직전에 잡아 같은 날 2회차 이상치 판정 무력화 · 이상치 검사가 `newest` 1건만 · uncaught 예외가 JSON 아닌 traceback · `latest_date` 의 f-string SQL · `schema.sql` 이 구조 다른 기존 테이블을 조용히 통과

## 남은 것

- `EXIM_API_KEY` 발급 후 `fx_daily` 실호출 검증 (코드·테스트는 완료)
- 코스피 15:40 수집 시각 실측 조정 (설계 B-3)
- cron 등록
- 전체 브랜치 리뷰 미실시
