# 청약 커트라인 팩트북

한국부동산원 청약홈 공개데이터를 집계해 **단지·평형별 당첨가점(커트라인)과 경쟁률**을 정적 표·JSON으로 제공하는 사이트입니다.
청약홈 원본은 동적 화면과 PDF라 검색·AI 인용이 어렵기 때문에, 같은 값을 기계가 읽을 수 있는 형태로 다시 정리했습니다.

- 사이트: https://cheongyak-factbook.pages.dev (Cloudflare Pages, 무료)
- GA4: `G-8NR0KWXMWM` (속성 ID 564957122)
- 원본: 공공데이터포털 파일데이터 4종 (무료·이용허락범위 제한 없음)
  - 15101046 APT 분양정보 · 15101047 APT 주택형별 분양정보 · 15101048 APT 경쟁률 · 15126242 APT 경쟁률 가점정보
- LLM 호출 0원: 집계·생성·검증 모두 순수 파이썬

## 구조

```
data/raw/            원본 CSV 스냅샷(fetch.py가 갱신) + sources.json(해시·수집시각)
data/cache/          포털에 아직 없는 최근 공고의 단지명·소재지 캐시
scripts/fetch.py     원본 4종 다운로드(인증키 불필요 경로)
scripts/fetch_details.py  최근 공고 상세페이지에서 단지명·주소 보완(캐시)
scripts/pipeline.py  정규화·집계 (지역/평형/가점·경쟁률)
scripts/build.py     사이트 생성 (HTML·JSON·llms.txt·sitemap)
scripts/check.py     산출물 자동 검증(링크·금지문구·값 정합성)
tests/               단위 테스트
site/                빌드 산출물 (이 디렉터리가 그대로 배포됨)
config/ga_id.txt     GA4 측정 ID (없으면 태그 미삽입)
```

## 갱신 흐름

`python3 scripts/fetch.py` → `python3 scripts/fetch_details.py` → `python3 scripts/build.py` → `python3 scripts/check.py`

`.github/workflows/update.yml`이 매일 05:00(KST) 위 순서를 돌리고, 원본·캐시가 바뀌면 그 변경분을 커밋합니다.
Cloudflare Pages가 `main` 푸시를 감지해 `python3 scripts/build.py`로 `site/`를 만들고 자동 재배포합니다.
(빌드 산출물은 저장소에 커미트하지 않습니다.) 원본 수집이 실패하면 커밋된 스냅샷으로 빌드해
사이트가 이전 상태로 유지됩니다(방어 코드).

## 값 정의

| 항목 | 정의 |
|---|---|
| 최저당첨가점(커트라인) | 공고·주택형별 당첨자 중 가장 낮은 가점. 0(미달·가점제 미적용) 제외 |
| 지역 우선 | 해당지역 값이 있으면 해당지역, 없으면 기타지역 |
| 경쟁률 | 1순위·해당지역(없으면 기타지역) 접수건수 ÷ 공급세대수 |
| 평형 구간 | 전용면적 60㎡ 이하 / 60~85㎡ / 85~102㎡ / 102㎡ 초과 |
| 최근 2년 | 데이터 최근 공고일 기준 730일 |

## 원칙

- 무과금: Cloudflare Pages 무료, GA4 무료, 공공데이터 무료. 유료 API·LLM 호출 없음
- 실명·군 관련 정보 비노출 (사이트·코드·문서 어디에도 넣지 않음)
- 원본 값을 그대로 집계하며 추정·보정하지 않음. 판단 근거는 청약홈 공고문
