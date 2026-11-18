#!/usr/bin/env python3
"""청약 커트라인 팩트북 정적 사이트 생성기.

입력: data/raw/*.csv (공공데이터포털 파일데이터), data/cache/detail_meta.json
출력: site/ (정적 HTML + JSON + llms.txt + sitemap.xml)
"""
import html
import json
import os
import re
import shutil
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pipeline  # noqa: E402

OUT = os.path.join(ROOT, "site")
BASE = os.environ.get("SITE_BASE", "https://cheongyak-factbook.pages.dev").rstrip("/")
GA_ID = os.environ.get("GA_ID", "").strip()
if not GA_ID:
    _ga_file = os.path.join(ROOT, "config", "ga_id.txt")
    if os.path.exists(_ga_file):
        GA_ID = open(_ga_file, encoding="utf-8").read().strip()
KST = timezone(timedelta(hours=9))
BUILD_DATE = datetime.now(KST).strftime("%Y-%m-%d")

SIDO_EN = {
    "서울": "seoul", "경기": "gyeonggi", "인천": "incheon", "부산": "busan", "대구": "daegu",
    "광주": "gwangju", "대전": "daejeon", "울산": "ulsan", "세종": "sejong", "강원": "gangwon",
    "충북": "chungbuk", "충남": "chungnam", "전북": "jeonbuk", "전남": "jeonnam",
    "경북": "gyeongbuk", "경남": "gyeongnam", "제주": "jeju",
}
CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
JONG = ["", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb", "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t", "k", "t", "p", "h"]


def romanize(text):
    out = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            s = code - 0xAC00
            out.append(CHO[s // 588] + JUNG[(s % 588) // 28] + JONG[s % 28])
        elif ch.isalnum():
            out.append(ch.lower())
        elif ch in " -_":
            out.append("-")
    return re.sub(r"-+", "-", "".join(out)).strip("-")


def region_slug(sido, sgg):
    stem = sgg[:-1] if sgg and sgg[-1] in "시군구" else (sgg or "")
    suffix = {"시": "-si", "군": "-gun", "구": "-gu"}.get(sgg[-1] if sgg else "", "")
    if sido == "세종" or not sgg:
        return SIDO_EN.get(sido, "etc")
    return f"{SIDO_EN.get(sido, 'etc')}-{romanize(stem)}{suffix}"


def e(text):
    return html.escape(str(text if text is not None else ""), quote=True)


def n1(v, unit=""):
    if v is None:
        return "–"
    if abs(v - round(v)) < 0.05:
        s = f"{int(round(v)):,}"
    else:
        s = f"{v:,.1f}"
    return s + unit


def rng(lo, hi):
    if lo is None and hi is None:
        return "–"
    return f"{n1(lo)}~{n1(hi)}"


def rate_fmt(v):
    return "–" if v is None else f"{v:,.1f} : 1"


MIN_SAMPLE = 5  # 표본이 이보다 적으면 평균·중앙값을 단정하지 않는다(지침서 §6)


def low_sample(n):
    return n is not None and n < MIN_SAMPLE


def stat_cell(s, key, fmt=n1):
    """표·문장에 쓰는 통계값. 표본 5건 미만이면 값 대신 ‘표본 부족 (n=N)’을 적는다."""
    if not s:
        return "–"
    if low_sample(s.get("n")):
        return f"표본 부족 (n={s['n']})"
    return fmt(s.get(key))


def region_stat(r, prefer_recent=True):
    rec = r.get("recent_12m") or {}
    if prefer_recent and (rec.get("n") or 0) > 0:
        return rec
    return r.get("totals") or rec


def score_text(s, unit="점"):
    """문장·목록에 쓰는 ‘최저가점 평균 X점’. 표본 5건 미만이면 표본 부족으로 적는다."""
    if not s:
        return "–"
    if low_sample(s.get("n")):
        return f"표본 부족 (n={s['n']})"
    v = s.get("min_score_avg")
    return "–" if v is None else f"{n1(v)}{unit}"


def area_row_html(s, label, with_notice=True):
    """평형 구간 표의 한 행. 표본 5건 미만이면 평균·중앙값·범위 대신 ‘표본 부족’을 한 번 적는다."""
    tail = f'<td class="num">{e(s["last_notice"] or "–")}</td>' if with_notice else ""
    if low_sample(s["n"]):
        cells = "".join('<td class="num">–</td>' for _ in range(4))
        return (f'<tr><td>{e(label)}</td><td class="num">{s["n"]:,}</td>'
                f'<td class="num">표본 부족 (n={s["n"]})</td>{cells}{tail}</tr>')
    return (
        f'<tr><td>{e(label)}</td><td class="num">{s["n"]:,}</td>'
        f'<td class="num">{n1(s["min_score_avg"])}</td><td class="num">{n1(s["min_score_median"])}</td>'
        f'<td class="num">{rng(s["min_score_min"], s["min_score_max"])}</td>'
        f'<td class="num">{n1(s["avg_score_mean"])}</td>'
        f'<td class="num">{rate_fmt(s["rate_median"])}</td>{tail}</tr>'
    )


def price_fmt(v):
    if not v:
        return "–"
    return f"{int(round(v)):,}만원"


def houses(v):
    if v is None:
        return "–"
    return f"{int(round(v)):,}"


# --------------------------------------------------------------- 공통 레이아웃
def ga_snippet():
    if not GA_ID:
        return ""
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>\n'
        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
        f"gtag('js',new Date());gtag('config','{GA_ID}',{{anonymize_ip:true}});</script>"
    )


NAV = [
    ("/", "홈"),
    ("/sido/", "시·도별"),
    ("/score/58/", "가점 58점"),
    ("/about/", "데이터·방법"),
]


def page(title, desc, path, body, jsonld=None, extra_head=""):
    canonical = BASE + path
    ld = ""
    if jsonld:
        for obj in jsonld:
            ld += '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False) + "</script>\n"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="청약 커트라인 팩트북">
<meta name="twitter:card" content="summary">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/style.css">
{ga_snippet()}
{extra_head}{ld}</head>
<body>
<header class="site-head">
  <a class="brand" href="/"><span class="dot"></span>청약 커트라인 팩트북</a>
  <nav class="nav">
    <a href="/">홈</a><a href="/sido/">시·도별</a><a href="/score/58/">가점 58점</a><a href="/about/">데이터·방법</a>
  </nav>
</header>
<main>
{body}
</main>
<footer class="site-foot">
  <p><strong>출처</strong> 공공데이터포털 · 한국부동산원 청약홈 공개데이터(APT 분양정보·주택형별 분양정보·APT 경쟁률·APT 경쟁률 가점정보)를 자체 집계했습니다. 원본 갱신에 따라 값이 달라질 수 있습니다.</p>
  <p><strong>기준</strong> 데이터 기준일 {e(LAST_NOTICE)} · <a href="/about/">산출 방법과 한계</a> · <a href="/privacy/">개인정보·쿠키</a></p>
  <p class="fine">이 사이트는 청약 당첨을 보장하지 않으며, 청약 판단의 최종 근거는 청약홈 입주자모집공고문입니다. 투자 권유가 아닙니다.</p>
</footer>
</body>
</html>
"""


def breadcrumb(items):
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": BASE + url}
            for i, (name, url) in enumerate(items)
        ],
    }


def write(path, content):
    full = os.path.join(OUT, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def write_json(path, obj, compact=True):
    full = os.path.join(OUT, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------- 데이터 준비
def load():
    data = pipeline.build_all()
    rows = data["rows"]
    complexes = {cid: c for cid, c in data["complexes"].items() if any(r["complex_id"] == cid for r in rows)}
    by_complex = defaultdict(list)
    for r in rows:
        by_complex[r["complex_id"]].append(r)
    for cid, rws in by_complex.items():
        rws.sort(key=lambda x: (x["area"] or 0, x["model"]))
    return data, rows, complexes, by_complex


def slug_map(regions):
    used = {}
    out = {}
    for r in regions:
        slug = region_slug(r["sido"], r["sgg"])
        if slug in used:
            used[slug] += 1
            slug = f"{slug}-{used[slug]}"
        else:
            used[slug] = 1
        out[(r["sido"], r["sgg"])] = slug
    return out


# --------------------------------------------------------------- 홈
def build_index(rows, regions, by_complex, complexes, slugs):
    n_units = len(rows)
    n_complexes = len(by_complex)
    n_scored = sum(1 for r in rows if r["score_min"])
    n_rated = sum(1 for r in rows if r["rate_primary"])
    years = sorted({r["notice_date"][:4] for r in rows if r["notice_date"]})
    first = min((r["notice_date"] for r in rows if r["notice_date"]), default="")

    # 시·도 요약 (60~85㎡, 최근 2년 우선)
    sido_rows = []
    for sido in pipeline.SIDO_ORDER:
        sregs = [r for r in regions if r["sido"] == sido]
        if not sregs:
            continue
        units = [r for r in rows if r["sido"] == sido]
        rec = pipeline.summarize([r for r in units if r["bucket"] == "60~85㎡"]) or pipeline.summarize(units)
        last = max((r["notice_date"] for r in units if r["notice_date"]), default="")
        sido_rows.append((sido, len(sregs), rec, last))

    top_regions = sorted(
        [r for r in regions if r["areas"] and not low_sample(region_stat(r)["n"])],
        key=lambda r: region_stat(r)["min_score_avg"] or 0,
        reverse=True,
    )[:12]
    low_regions = sorted(
        [r for r in regions if r["areas"] and not low_sample(region_stat(r)["n"]) and region_stat(r)["min_score_avg"]],
        key=lambda r: region_stat(r)["min_score_avg"] or 999,
    )[:12]

    def region_table(items, caption):
        rows_html = "".join(
            f'<tr><td><a href="/region/{slugs[(r["sido"], r["sgg"])]}/">{e(r["sido"])} {e(r["sgg"])}</a></td>'
            f'<td class="num">{stat_cell(region_stat(r), "min_score_avg")}</td>'
            f'<td class="num">{stat_cell(region_stat(r), "min_score_min")}</td>'
            f'<td class="num">{stat_cell(region_stat(r), "avg_score_mean")}</td>'
            f'<td class="num">{stat_cell(region_stat(r), "rate_median", rate_fmt)}</td>'
            f'<td class="num">{n1(region_stat(r)["complexes"])}</td>'
            f'<td class="num">{e(region_stat(r)["last_notice"] or "–")}</td></tr>'
            for r in items
        )
        return f"""<div class="table-wrap"><table>
<caption>{e(caption)}</caption>
<thead><tr><th>지역</th><th class="num">최저가점 평균</th><th class="num">최저가점 최소</th><th class="num">평균가점</th><th class="num">1순위 경쟁률 중앙값</th><th class="num">단지</th><th class="num">최근 공고</th></tr></thead>
<tbody>{rows_html}</tbody></table></div>"""

    sido_html = "".join(
        f'<tr><td><a href="/sido/{SIDO_EN.get(sido, "etc")}/">{e(sido)}</a></td>'
        f'<td class="num">{nregions}</td>'
        f'<td class="num">{stat_cell(rec, "min_score_avg")}</td>'
        f'<td class="num">{stat_cell(rec, "min_score_min")}</td>'
        f'<td class="num">{stat_cell(rec, "rate_median", rate_fmt)}</td>'
        f'<td class="num">{e(last or "–")}</td></tr>'
        for sido, nregions, rec, last in sido_rows
    )

    faq = [
        ("청약 당첨 가점 커트라인은 어떻게 계산했나요?",
         "청약홈이 공개한 공고별·주택형별 최저당첨가점을 그대로 씁니다. 가점제가 적용되지 않았거나 신청자가 미달해 값이 0인 행은 제외했고, 해당지역 신청자 기준을 우선하며 해당지역 값이 없으면 기타지역 값을 씁니다."),
        ("가점 58점이면 어디까지 당첨됐나요?",
         "최근 2년 공고 중 최저당첨가점이 58점 이하인 단지·평형을 모아 두었습니다. 상단의 ‘가점 58점’ 메뉴에서 40~75점 구간을 각각 볼 수 있습니다."),
        ("경쟁률은 어떤 값인가요?",
         "청약홈 공개 경쟁률(접수건수 ÷ 공급세대수) 중 1순위·해당지역 값을 우선 사용했고, 없으면 기타지역 1순위 값을 씁니다. 표의 ‘1순위 경쟁률’은 이 기준입니다."),
        ("몇 년도 자료까지 있나요?",
         f"{first} 모집공고부터 {LAST_NOTICE} 공고까지 들어 있습니다. 원본 파일데이터의 갱신 주기는 연간이며, 이 사이트는 원본이 갱신되면 자동으로 다시 만들어집니다."),
        ("특별공급(생애최초·신혼부부 등) 커트라인도 있나요?",
         "아니요. 여기 값은 일반공급 가점제 당첨자의 가점입니다. 특별공급은 별도 배점·추첨이라 같은 표로 비교할 수 없습니다."),
        ("표본이 적은 지역은 어떻게 봐야 하나요?",
         "평형 구간별 표본(주택형) 수를 함께 표시합니다. 표본이 한 자리 수면 그 지역의 대표값으로 보기 어렵고, 참고용으로만 쓰는 것이 좋습니다."),
    ]
    faq_html = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in faq)

    body = f"""
<section class="hero">
  <p class="eyebrow">청약홈 공개데이터 · 단지·평형별 커트라인</p>
  <h1>청약 커트라인 팩트북</h1>
  <p class="lead">“수성구 84㎡는 몇 점이면 됐을까?”에 숫자로 답하는 표입니다. 청약홈 입주자모집공고 <strong>{n_complexes:,}건</strong>·주택형 <strong>{n_units:,}건</strong>을 집계해 평형별 <strong>최저·평균·최고 당첨가점</strong>과 <strong>경쟁률</strong>을 정리했습니다. 모두 정적 표와 JSON이라 사람과 AI 모두 바로 읽을 수 있습니다.</p>
  <ul class="kpis">
    <li><span>모집공고</span><strong>{n_complexes:,}</strong><em>건</em></li>
    <li><span>주택형 표본</span><strong>{n_units:,}</strong><em>건</em></li>
    <li><span>가점 값</span><strong>{n_scored:,}</strong><em>건</em></li>
    <li><span>경쟁률 값</span><strong>{n_rated:,}</strong><em>건</em></li>
  </ul>
  <p class="meta">기간 {e(first)} ~ {e(LAST_NOTICE)} · 지역 {len(regions)}곳 · 원본 공공데이터포털(한국부동산원 청약홈)</p>
</section>

<section>
  <h2>가점으로 찾기</h2>
  <p>내 가점에서 “어디까지 됐는지”를 구간별로 봅니다. 최근 2년 공고의 최저당첨가점이 기준 이하인 단지·평형만 남깁니다.</p>
  <div class="chips">
    {''.join(f'<a class="chip" href="/score/{t}/">{t}점</a>' for t in range(40, 76))}
  </div>
</section>

<section>
  <h2>지역으로 찾기</h2>
  <p>시·도 → 시·군·구 순으로 들어가면 평형 구간별 커트라인과 최근 단지 목록이 나옵니다.</p>
  <div class="table-wrap"><table>
    <caption>시·도별 요약 (60~85㎡ 기준, 최근 2년이 있으면 최근 2년 값)</caption>
    <thead><tr><th>시·도</th><th class="num">지역 수</th><th class="num">최저가점 평균</th><th class="num">최저가점 최소</th><th class="num">1순위 경쟁률 중앙값</th><th class="num">최근 공고</th></tr></thead>
    <tbody>{sido_html}</tbody>
  </table></div>
  <p class="note">표본이 5건 미만인 지역·구간은 평균을 단정하지 않고 ‘표본 부족 (n=N)’으로 표기합니다. 상·하위 순위표에서는 표본 5건 미만 지역을 제외했습니다.</p>
</section>

<section class="cols">
  <div>
    <h2>최저가점이 높은 지역</h2>
    {region_table(top_regions, "최근 2년 최저가점 평균 상위 12곳")}
  </div>
  <div>
    <h2>최저가점이 낮은 지역</h2>
    {region_table(low_regions, "최근 2년 최저가점 평균 하위 12곳")}
  </div>
</section>

<section>
  <h2>무엇을 볼 수 있나</h2>
  <ul class="plain">
    <li><strong>단지·평형별 커트라인</strong> — 공고마다 주택형별 최저·평균·최고 당첨가점</li>
    <li><strong>경쟁률</strong> — 1순위 해당지역/기타지역, 2순위</li>
    <li><strong>지역 요약</strong> — 시·군·구 × 평형 구간(60㎡ 이하 / 60~85㎡ / 85~102㎡ / 102㎡ 초과)</li>
    <li><strong>기계가 읽는 형태</strong> — <a href="/api/regions.json">/api/regions.json</a>, <a href="/api/complexes.json">/api/complexes.json</a>, <a href="/llms.txt">/llms.txt</a></li>
  </ul>
</section>

<section>
  <h2>자주 묻는 질문</h2>
  <div class="faq">{faq_html}</div>
</section>
"""
    ld = [
        {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "청약 커트라인 팩트북 — 단지·평형별 당첨가점·경쟁률 집계",
            "description": f"청약홈 입주자모집공고 {n_complexes:,}건·주택형 {n_units:,}건을 집계한 단지·평형별 최저/평균/최고 당첨가점과 경쟁률 데이터. 기간 {first}~{LAST_NOTICE}.",
            "url": BASE + "/",
            "keywords": ["청약 가점", "당첨가점 커트라인", "청약 경쟁률", "청약홈", "주택청약", "아파트 청약"],
            "temporalCoverage": f"{first}/{LAST_NOTICE}",
            "spatialCoverage": "대한민국",
            "isAccessibleForFree": True,
            "creator": {"@type": "Organization", "name": "청약 커트라인 팩트북"},
            "isBasedOn": "https://www.data.go.kr/data/15126242/fileData.do",
            "distribution": [
                {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": BASE + "/api/regions.json"},
                {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": BASE + "/api/complexes.json"},
            ],
            "dateModified": BUILD_DATE,
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq
            ],
        },
        {"@context": "https://schema.org", "@type": "WebSite", "name": "청약 커트라인 팩트북", "url": BASE + "/"},
    ]
    write(
        "/index.html",
        page(
            "청약 커트라인 팩트북 — 단지·평형별 당첨가점·경쟁률",
            f"청약홈 공개데이터로 만든 단지·평형별 청약 커트라인. 모집공고 {n_complexes:,}건·주택형 {n_units:,}건의 최저/평균/최고 당첨가점과 경쟁률을 지역·평형·가점 구간별 정적 표로 제공합니다.",
            "/",
            body,
            ld,
        ),
    )
    return faq


# --------------------------------------------------------------- 시·도 페이지
def build_sido_pages(rows, regions, slugs):
    by_sido = defaultdict(list)
    for r in regions:
        by_sido[r["sido"]].append(r)
    listing = ""
    for sido in pipeline.SIDO_ORDER:
        regs = sorted(by_sido.get(sido, []), key=lambda r: r["sgg"])
        if not regs:
            continue
        items = "".join(
            f'<li><a href="/region/{slugs[(r["sido"], r["sgg"])]}/">{e(r["sgg"])}</a>'
            f'<span class="muted">{score_text(region_stat(r))} · 단지 {n1(region_stat(r)["complexes"])}</span></li>'
            for r in regs
        )
        listing += f'<div class="sido-block"><h3>{e(sido)}</h3><ul class="region-list">{items}</ul></div>'
        # 시·도 페이지
        units = [r for r in rows if r["sido"] == sido]
        area_rows = ""
        for label, _, _ in pipeline.AREA_BUCKETS:
            s = pipeline.summarize([r for r in units if r["bucket"] == label])
            if not s:
                continue
            area_rows += area_row_html(s, label, with_notice=False)
        reg_rows = "".join(
            f'<tr><td><a href="/region/{slugs[(r["sido"], r["sgg"])]}/">{e(r["sgg"])}</a></td>'
            f'<td class="num">{stat_cell(region_stat(r), "min_score_avg")}</td>'
            f'<td class="num">{stat_cell(region_stat(r), "min_score_min")}</td>'
            f'<td class="num">{stat_cell(region_stat(r), "rate_median", rate_fmt)}</td>'
            f'<td class="num">{n1(region_stat(r)["complexes"])}</td></tr>'
            for r in regs
        )
        body = f"""
<nav class="crumbs"><a href="/">홈</a> › <span>{e(sido)}</span></nav>
<h1>{e(sido)} 청약 당첨가점·경쟁률</h1>
<p class="lead">{e(sido)}에서 공고된 단지 {len({r['complex_id'] for r in units}):,}곳의 주택형 {len(units):,}건을 집계했습니다. 아래 표는 평형 구간별 커트라인이고, 시·군·구를 누르면 그 지역만 볼 수 있습니다.</p>
<div class="table-wrap"><table>
<caption>{e(sido)} 평형 구간별 (전체 기간)</caption>
<thead><tr><th>평형(전용)</th><th class="num">표본</th><th class="num">최저가점 평균</th><th class="num">최저가점 중앙값</th><th class="num">최저가점 범위</th><th class="num">평균가점</th><th class="num">1순위 경쟁률 중앙값</th></tr></thead>
<tbody>{area_rows}</tbody></table></div>
<h2>시·군·구</h2>
<div class="table-wrap"><table>
<thead><tr><th>지역</th><th class="num">최저가점 평균</th><th class="num">최저가점 최소</th><th class="num">1순위 경쟁률 중앙값</th><th class="num">단지</th></tr></thead>
<tbody>{reg_rows}</tbody></table></div>
"""
        write(f"/sido/{SIDO_EN.get(sido, "etc")}/index.html", page(
            f"{sido} 청약 당첨가점·경쟁률 (평형별 커트라인)",
            f"{sido} 시·군·구별 청약 당첨 최저가점과 경쟁률. 평형 구간별 커트라인과 단지 목록을 정적 표로 제공합니다.",
            f"/sido/{SIDO_EN.get(sido, "etc")}/", body,
            [breadcrumb([("홈", "/"), (sido, f"/sido/{SIDO_EN.get(sido, "etc")}/")])],
        ))

    body = f"""
<nav class="crumbs"><a href="/">홈</a> › <span>시·도별</span></nav>
<h1>시·도별 청약 커트라인</h1>
<p class="lead">17개 시·도 → 시·군·구 순으로 들어갑니다. 지역 옆 숫자는 그 지역 60~85㎡ 평균 최저가점(최근 2년 표본이 있으면 최근 값)이고, 표본이 5건 미만이면 값을 대신 적지 않습니다.</p>
{listing}
"""
    write("/sido/index.html", page("시·도별 청약 커트라인 — 지역 목록", "전국 17개 시·도, 시·군·구별 청약 당첨가점·경쟁률 페이지 목록.", "/sido/", body))


# --------------------------------------------------------------- 지역 페이지
def build_region_pages(rows, regions, by_complex, complexes, slugs):
    api_regions = []
    for r in regions:
        slug = slugs[(r["sido"], r["sgg"])]
        rws = [x for x in rows if x["sido"] == r["sido"] and x["sgg"] == r["sgg"]]
        rws.sort(key=lambda x: (x["notice_date"], x["name"]), reverse=True)
        recent = rws[:40]
        newest = rws[0]["notice_date"] if rws else ""

        def area_table(use_recent):
            out = ""
            for label, _, _ in pipeline.AREA_BUCKETS:
                sub = [x for x in rws if x["bucket"] == label]
                s = pipeline.summarize(sub, CUTOFF_RECENT) if use_recent else pipeline.summarize(sub)
                if not s:
                    continue
                out += area_row_html(s, label)
            return out or '<tr><td colspan="8">표본이 없습니다.</td></tr>'

        area_rows = area_table(True)
        area_rows_all = area_table(False)

        def unit_cell(x):
            parts = []
            if x["score_min"]:
                parts.append(f'<strong>{n1(x["score_min"])}</strong>')
            else:
                parts.append("–")
            return parts[0]

        recent_rows = "".join(
            f'<tr><td class="num">{e(x["notice_date"])}</td>'
            f'<td><a href="/complex/{x["complex_id"]}/">{e(x["name"])}</a></td>'
            f'<td class="num">{x["area"]:.0f}㎡</td>'
            f'<td class="num">{unit_cell(x)}</td>'
            f'<td class="num">{n1(x["score_avg"])}</td>'
            f'<td class="num">{rate_fmt(x["rate_primary"])}</td></tr>'
            for x in recent
        )

        main = pipeline.summarize([x for x in rws if x["bucket"] == "60~85㎡"]) or pipeline.summarize(rws)
        recent_only = pipeline.summarize([x for x in rws if x["bucket"] == "60~85㎡"], CUTOFF_RECENT)
        if recent_only and not low_sample(recent_only["n"]):
            headline = (
                f'최근 2년({WINDOW_START}~{LAST_NOTICE}) {e(r["sgg"])} 60~85㎡형 표본 {recent_only["n"]}건의 최저당첨가점은 '
                f'평균 <strong>{n1(recent_only["min_score_avg"])}점</strong>'
                f'(중앙값 {n1(recent_only["min_score_median"])}점, 최저 {n1(recent_only["min_score_min"])}점)이었고, '
                f'1순위 경쟁률 중앙값은 <strong>{rate_fmt(recent_only["rate_median"])}</strong>입니다.'
            )
        elif recent_only and recent_only["min_score_avg"] is not None:
            headline = (
                f'최근 2년({WINDOW_START}~{LAST_NOTICE}) {e(r["sgg"])} 60~85㎡ 표본이 {recent_only["n"]}건뿐이라 '
                f'평균을 단정하기 어렵습니다. 전체 기간(표본 {main["n"]}건) 최저가점 평균은 '
                f'<strong>{n1(main["min_score_avg"])}점</strong>입니다.'
            )
        elif main["min_score_avg"] is not None and not low_sample(main["n"]):
            headline = (
                f'{e(r["sgg"])}의 최근 2년 60~85㎡ 가점 표본이 없습니다. 전체 기간(표본 {main["n"]}건) '
                f'최저가점 평균은 <strong>{n1(main["min_score_avg"])}점</strong>입니다. 아래 평형 구간 표를 참고하세요.'
            )
        else:
            headline = f'{e(r["sgg"])}의 60~85㎡ 가점 표본이 아직 적습니다. 아래 평형 구간 표를 참고하세요.'

        top_complexes = sorted(
            {(x["complex_id"], complexes[x["complex_id"]]["name"], x["notice_date"]) for x in rws},
            key=lambda t: t[2], reverse=True,
        )[:60]
        comp_list = "".join(
            f'<li><a href="/complex/{cid}/">{e(name)}</a> <span class="muted">{e(date)}</span></li>'
            for cid, name, date in top_complexes
        )

        body = f"""
<nav class="crumbs"><a href="/">홈</a> › <a href="/sido/{SIDO_EN.get(r['sido'], "etc")}/">{e(r['sido'])}</a> › <span>{e(r['sgg'])}</span></nav>
<h1>{e(r['sido'])} {e(r['sgg'])} 청약 당첨가점 커트라인</h1>
<p class="lead">{headline}</p>
<p class="meta">표본 {len(rws):,}건(단지 {len({x['complex_id'] for x in rws}):,}곳) · 기간 {e(min(x['notice_date'] for x in rws if x['notice_date']))} ~ {e(max(x['notice_date'] for x in rws if x['notice_date']))}</p>

<h2>평형 구간별 커트라인 — 최근 2년</h2>
<p class="note">최근 2년 창은 데이터 최근 공고일({e(LAST_NOTICE)}) 기준 730일, 즉 {e(WINDOW_START)} 이후 공고입니다. 표본은 가점 또는 경쟁률 값이 있는 주택형 수입니다.</p>
<p class="note">표본이 5건 미만인 구간은 평균·중앙값을 단정하지 않고 ‘표본 부족 (n=N)’으로 적었습니다. “최근 공고” 열은 그 구간에서 가장 늦은 공고일입니다.</p>
<div class="table-wrap"><table>
<caption>{e(r['sido'])} {e(r['sgg'])} 평형별 당첨가점·경쟁률 (최근 2년)</caption>
<thead><tr><th>평형(전용면적)</th><th class="num">표본</th><th class="num">최저가점 평균</th><th class="num">최저가점 중앙값</th><th class="num">최저가점 범위</th><th class="num">평균가점</th><th class="num">1순위 경쟁률 중앙값</th><th class="num">최근 공고</th></tr></thead>
<tbody>{area_rows}</tbody></table></div>

<h2>평형 구간별 — 전체 기간</h2>
<p class="note">2020년 이후 전체 공고를 합친 값입니다. 오래된 공고가 섞여 있어 최근 흐름과 다를 수 있습니다.</p>
<div class="table-wrap"><table>
<caption>{e(r['sido'])} {e(r['sgg'])} 평형별 당첨가점·경쟁률 (전체 기간)</caption>
<thead><tr><th>평형(전용면적)</th><th class="num">표본</th><th class="num">최저가점 평균</th><th class="num">최저가점 중앙값</th><th class="num">최저가점 범위</th><th class="num">평균가점</th><th class="num">1순위 경쟁률 중앙값</th><th class="num">최근 공고</th></tr></thead>
<tbody>{area_rows_all}</tbody></table></div>

<h2>최근 공고 단지</h2>
<div class="table-wrap"><table>
<thead><tr><th class="num">모집공고일</th><th>단지</th><th class="num">전용</th><th class="num">최저가점</th><th class="num">평균가점</th><th class="num">1순위 경쟁률</th></tr></thead>
<tbody>{recent_rows}</tbody></table></div>
<p class="note">최근 40건만 표시했습니다. 단지별 전체 주택형은 단지 페이지에서 볼 수 있습니다.</p>

<h2>이 지역 단지</h2>
<ul class="linklist">{comp_list}</ul>
"""
        if recent_only and not low_sample(recent_only["n"]):
            page_desc = (
                f"{r['sido']} {r['sgg']} 청약 커트라인. 최근 2년({WINDOW_START}~{LAST_NOTICE}, 표본 {recent_only['n']}건) "
                f"60~85㎡ 최저가점 평균 {n1(recent_only['min_score_avg'])}점, 1순위 경쟁률 중앙값 {rate_fmt(recent_only['rate_median'])}. 평형별 표와 최근 단지 목록."
            )
        else:
            page_desc = (
                f"{r['sido']} {r['sgg']} 청약 커트라인. 평형별 최저 당첨가점과 경쟁률, 최근 단지 목록. "
                f"최근 2년 표본이 적어 평균을 단정하지 않고 표본 수를 함께 적습니다."
            )
        write(f"/region/{slug}/index.html", page(
            f"{r['sido']} {r['sgg']} 청약 당첨가점·경쟁률 — 평형별 커트라인",
            page_desc,
            f"/region/{slug}/", body,
            [breadcrumb([("홈", "/"), (r["sido"], f"/sido/{SIDO_EN.get(r['sido'], "etc")}/"), (r["sgg"], f"/region/{slug}/")])],
        ))

        api_regions.append({
            "slug": slug,
            "sido": r["sido"],
            "sgg": r["sgg"],
            "url": f"{BASE}/region/{slug}/",
            "complexes": len({x["complex_id"] for x in rws}),
            "units": len(rws),
            "last_notice": r["last_notice"],
            "areas": [
                {"bucket": a["bucket"], "all": a["all"], "recent_12m": a["recent_12m"], "has_recent": a["has_recent"]}
                for a in r["areas"]
            ],
        })

        # 지역별 JSON
        units_json = [
            {
                "complex_id": x["complex_id"],
                "complex": x["name"],
                "date": x["notice_date"],
                "model": x["model"],
                "area": x["area"],
                "bucket": x["bucket"],
                "score_min": x["score_min"],
                "score_avg": x["score_avg"],
                "score_max": x["score_max"],
                "rate_1st": x["rate_primary"],
            }
            for x in rws
        ]
        write_json(f"/api/region/{slug}.json", {
            "region": {"sido": r["sido"], "sgg": r["sgg"], "slug": slug},
            "data_through": LAST_NOTICE,
            "window_start": WINDOW_START,
            "summary": {
                "all": r["totals"],
                "recent_12m": r["recent_12m"],
                "has_recent": r["has_recent"],
                # 구 키 이름 ‘recent’는 `recent_12m`으로 통일했다(major 변경, 지침서 §5).
                "areas": [
                    {"bucket": a["bucket"], "all": a["all"], "recent_12m": a["recent_12m"], "has_recent": a["has_recent"]}
                    for a in r["areas"]
                ],
            },
            "units": units_json,
        })
    return api_regions


# --------------------------------------------------------------- 단지 페이지
def build_complex_pages(complexes, by_complex, slugs):
    index = []
    for cid, rws in by_complex.items():
        c = complexes.get(cid)
        if not c:
            continue
        slug = slugs.get((c["sido"], c["sgg"]))
        first = rws[0]
        unit_rows = ""
        for x in rws:
            unit_rows += (
                f'<tr><td class="mono">{e(x["area_label"])}</td>'
                f'<td class="num">{x["area"]:.1f}㎡</td>'
                f'<td class="num">{houses(x["general"])} / {houses(x["special"])}</td>'
                f'<td class="num">{price_fmt(x["price_max"])}</td>'
                f'<td class="num"><strong>{n1(x["score_min"])}</strong></td>'
                f'<td class="num">{n1(x["score_avg"])}</td>'
                f'<td class="num">{n1(x["score_max"])}</td>'
                f'<td class="num">{rate_fmt(x["rate1_here"])}</td>'
                f'<td class="num">{rate_fmt(x["rate1_other"])}</td>'
                f'<td class="num">{rate_fmt(x["rate2"])}</td></tr>'
            )
        badges = []
        if c.get("speculation"):
            badges.append("투기과열지구")
        if c.get("adjust"):
            badges.append("조정대상지역")
        if c.get("price_cap"):
            badges.append("분양가상한제")
        badge_html = "".join(f'<span class="badge">{e(b)}</span>' for b in badges)
        scored = [x for x in rws if x["score_min"]]
        best = min(scored, key=lambda x: x["score_min"]) if scored else None
        lead = (
            f'이 단지에서 가장 낮은 최저당첨가점은 <strong>{e(best["bucket"])} {best["area"]:.1f}㎡의 {n1(best["score_min"])}점</strong>입니다 '
            f'(평균 {n1(best["score_avg"])}점, 최고 {n1(best["score_max"])}점).'
            if best else "이 공고는 가점 데이터가 공개되지 않았거나 가점제가 적용되지 않았습니다. 경쟁률만 볼 수 있습니다."
        )
        info_rows = [
            ("지역", f'{e(c["sido"])} {e(c["sgg"])}' if c.get("sgg") else "–"),
            ("공급위치", e(c.get("address") or "–")),
            ("모집공고일", e(c.get("notice_date") or "–")),
            ("주택구분", e(" · ".join(x for x in [c.get("house_type"), c.get("detail_type")] if x) or "–")),
            ("공급규모", f'{e(c.get("scale") or "–")}세대'),
            ("시공사", e(c.get("builder") or "–")),
            ("사업주체", e(c.get("developer") or "–")),
            ("입주예정", e(c.get("move_in") or "–")),
        ]
        info_html = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in info_rows)
        body = f"""
<nav class="crumbs"><a href="/">홈</a> › <a href="/sido/{SIDO_EN.get(c['sido'], "etc")}/">{e(c['sido'])}</a> › <a href="/region/{slug}/">{e(c['sgg'])}</a> › <span>{e(c['name'])}</span></nav>
<h1>{e(c['name'])} 청약 당첨가점·경쟁률</h1>
<p class="lead">{lead}</p>
{badge_html}
<div class="grid2">
  <div class="table-wrap"><table class="kv">
    <caption>단지 정보</caption>
    <tbody>{info_html}</tbody>
  </table></div>
</div>

<h2>주택형별 커트라인</h2>
<p class="note">가점은 해당지역 신청자 기준을 우선했고, 없으면 기타지역 값입니다. 값이 0(미달·가점제 미적용)인 주택형은 비어 있습니다. 세대수·분양가는 주택형별 분양정보 원본 기준이라 최신 공고는 아직 비어 있을 수 있습니다.</p>
<div class="table-wrap"><table>
<thead><tr><th>주택형</th><th class="num">전용면적</th><th class="num">세대수(일반/특별)</th><th class="num">분양가 최고</th><th class="num">최저가점</th><th class="num">평균가점</th><th class="num">최고가점</th><th class="num">1순위 해당</th><th class="num">1순위 기타</th><th class="num">2순위</th></tr></thead>
<tbody>{unit_rows}</tbody></table></div>

<h2>같은 지역 비교</h2>
<p><a href="/region/{slug}/">{e(c['sido'])} {e(c['sgg'])} 전체 커트라인 보기</a> · <a href="/score/{int(best['score_min']) if best and best['score_min'] else 55}/">이 점수로 어디까지 됐나 보기</a></p>
<p class="fine">출처: 공공데이터포털 한국부동산원 청약홈 공개데이터 · 원본 공고문은 청약홈에서 확인하세요.</p>
"""
        write(f"/complex/{cid}/index.html", page(
            f"{c['name']} 청약 당첨가점·경쟁률 ({c['sido']} {c['sgg']})",
            f"{c['name']} 주택형별 최저·평균·최고 당첨가점과 1·2순위 경쟁률. {c['sido']} {c['sgg']}, 모집공고 {c.get('notice_date') or ''}.",
            f"/complex/{cid}/", body,
            [breadcrumb([("홈", "/"), (c["sido"], f"/sido/{SIDO_EN.get(c['sido'], "etc")}/"), (c["sgg"], f"/region/{slug}/"), (c["name"], f"/complex/{cid}/")])],
        ))
        index.append({
            "id": cid,
            "name": c["name"],
            "sido": c["sido"],
            "sgg": c["sgg"],
            "date": c.get("notice_date") or first["notice_date"],
            "units": len(rws),
            "min_score": min((x["score_min"] for x in rws if x["score_min"]), default=None),
            "max_rate": max((x["rate_primary"] for x in rws if x["rate_primary"]), default=None),
            "url": f"{BASE}/complex/{cid}/",
            "json": f"{BASE}/api/complex/{cid}.json",
        })
        write_json(f"/api/complex/{cid}.json", {
            "id": cid,
            "name": c["name"],
            "sido": c["sido"],
            "sgg": c["sgg"],
            "address": c.get("address") or "",
            "notice_date": c.get("notice_date") or "",
            "apply_start": c.get("apply_start") or "",
            "apply_end": c.get("apply_end") or "",
            "winner_date": c.get("winner_date") or "",
            "scale": c.get("scale") or "",
            "builder": c.get("builder") or "",
            "developer": c.get("developer") or "",
            "house_type": c.get("house_type") or "",
            "area_buckets": sorted({x["bucket"] for x in rws}),
            "units": [
                {
                    "model": x["model"],
                    "type": x["area_label"],
                    "area": x["area"],
                    "general": x["general"],
                    "special": x["special"],
                    "price_max": x["price_max"],
                    "score_zone": x["score_zone"],
                    "score_min": x["score_min"],
                    "score_avg": x["score_avg"],
                    "score_max": x["score_max"],
                    "rate_1st_here": x["rate1_here"],
                    "rate_1st_other": x["rate1_other"],
                    "rate_2nd": x["rate2"],
                }
                for x in rws
            ],
            "data_through": LAST_NOTICE,
            "source": "공공데이터포털 한국부동산원 청약홈 공개데이터",
        })
    index.sort(key=lambda x: (x["date"] or "", x["name"]), reverse=True)
    return index


# --------------------------------------------------------------- 가점 구간 페이지
def build_score_pages(rows, complexes):
    recent = [r for r in rows if r["notice_date"] > CUTOFF_RECENT]
    base = recent or rows
    windows = {}
    for t in range(40, 76):
        hits = [r for r in base if r["score_min"] and r["score_min"] <= t]
        windows[t] = hits

    for t, hits in windows.items():
        hits.sort(key=lambda r: (r["score_min"], r["notice_date"]), reverse=True)
        shown = hits[:400]
        rows_html = "".join(
            f'<tr><td class="num">{n1(x["score_min"])}</td>'
            f'<td><a href="/complex/{x["complex_id"]}/">{e(x["name"])}</a></td>'
            f'<td>{e(x["sido"])} {e(x["sgg"])}</td>'
            f'<td class="num">{x["area"]:.1f}㎡</td>'
            f'<td class="num">{n1(x["score_avg"])}</td>'
            f'<td class="num">{rate_fmt(x["rate_primary"])}</td>'
            f'<td class="num">{e(x["notice_date"])}</td></tr>'
            for x in shown
        )
        n_complex = len({x["complex_id"] for x in hits})
        prev_link = f'<a href="/score/{t-1}/">← {t-1}점</a>' if t > 40 else "<span></span>"
        next_link = f'<a href="/score/{t+1}/">{t+1}점 →</a>' if t < 75 else "<span></span>"
        body = f"""
<nav class="crumbs"><a href="/">홈</a> › <span>가점 {t}점</span></nav>
<h1>가점 {t}점으로 당첨된 단지</h1>
<p class="lead">최근 2년 공고 중 <strong>최저당첨가점이 {t}점 이하</strong>였던 단지·평형이 <strong>{len(hits):,}건</strong>(단지 {n_complex:,}곳)입니다. 내 가점이 {t}점이라면 이 목록의 공고는 가점 기준으로는 당첨자 범위에 들었습니다.</p>
<p class="note">주의: 최저가점은 그 공고의 최저 당첨자 가점입니다. 경쟁률·물량·특별공급·지역 우선순위에 따라 결과는 달라지고, {t}점이 ‘안전’하다는 뜻이 아닙니다.</p>
<div class="pager">{prev_link}<span class="cur">{t}점</span>{next_link}</div>
<div class="table-wrap"><table>
<caption>최저당첨가점 {t}점 이하 (최근 2년, 최저가점 높은 순)</caption>
<thead><tr><th class="num">최저가점</th><th>단지</th><th>지역</th><th class="num">전용</th><th class="num">평균가점</th><th class="num">1순위 경쟁률</th><th class="num">모집공고일</th></tr></thead>
<tbody>{rows_html}</tbody></table></div>
<p class="note">{len(shown):,}건까지 표시했습니다. 전체 목록은 <a href="/api/regions.json">/api/regions.json</a>과 지역별 JSON에서 받을 수 있습니다.</p>
"""
        write(f"/score/{t}/index.html", page(
            f"가점 {t}점 청약 당첨 단지 — 최저가점 {t}점 이하 목록",
            f"최근 2년 청약 공고 중 최저당첨가점이 {t}점 이하였던 단지 {n_complex}곳·{len(hits)}건. 가점 {t}점으로 가능했던 지역·평형을 표로 정리.",
            f"/score/{t}/", body,
            [breadcrumb([("홈", "/"), (f"가점 {t}점", f"/score/{t}/")])],
        ))
    # 인덱스
    cards = "".join(
        f'<a class="chip" href="/score/{t}/">{t}점 <span>{len(windows[t]):,}건</span></a>' for t in range(40, 76)
    )
    body = f"""
<nav class="crumbs"><a href="/">홈</a> › <span>가점별</span></nav>
<h1>가점별로 어디까지 됐나</h1>
<p class="lead">최근 2년 공고에서 최저당첨가점이 각 점수 이하인 단지·평형 수입니다. 내 가점에 가까운 칸을 누르면 목록이 나옵니다.</p>
<div class="chips big">{cards}</div>
<p class="note">최근 2년은 ‘{e(WINDOW_START)}~{e(LAST_NOTICE)}’ 공고입니다(데이터 최근 공고일 기준 730일). 표본이 적은 점수대는 목록이 짧습니다.</p>
"""
    write("/score/index.html", page("가점별 청약 당첨 단지 — 40~75점", "가점 40~75점 구간별로 최저당첨가점이 그 이하였던 단지·평형 목록.", "/score/", body))
    return windows


# --------------------------------------------------------------- about / privacy / 기타
def build_about(rows, regions):
    n_units = len(rows)
    body = f"""
<nav class="crumbs"><a href="/">홈</a> › <span>데이터·방법</span></nav>
<h1>데이터와 산출 방법</h1>
<p class="lead">이 사이트는 청약홈이 공개한 원본을 그대로 집계합니다. 추정·보정은 하지 않고, 계산 규칙과 제외 기준을 아래에 모두 적었습니다.</p>

<h2>원본</h2>
<ul class="plain">
  <li><strong>한국부동산원_청약홈_APT 분양정보</strong> (공공데이터포털 15101046) — 공고번호, 단지명, 공급지역·공급위치, 모집공고일, 주택구분, 공급규모, 시공사·사업주체, 투기과열지구·조정대상지역·분양가상한제 여부</li>
  <li><strong>한국부동산원_청약홈_APT 주택형별 분양정보</strong> (15101047) — 주택형(전용면적), 일반·특별공급 세대수, 분양가 최고금액</li>
  <li><strong>한국부동산원_청약홈_APT 경쟁률</strong> (15101048) — 순위·거주지역별 접수건수와 경쟁률</li>
  <li><strong>한국부동산원_청약홈_APT 경쟁률 가점정보</strong> (15126242) — 주택형·거주지역별 최저·최고·평균당첨가점</li>
</ul>
<p>네 파일 모두 공공데이터포털에서 무료로 배포되며 이용허락범위 제한이 없습니다. 포털 파일데이터 갱신이 늦은 최근 공고는 청약홈 입주자모집공고 상세페이지에서 단지명·소재지·모집공고일만 보완했습니다(값 계산에는 쓰지 않음).</p>

<h2>계산 규칙</h2>
<ol class="plain">
  <li><strong>커트라인(최저당첨가점)</strong>: 원본의 최저당첨가점을 그대로 사용합니다. 0이거나 비어 있는 행은 ‘가점제 미적용 또는 신청 미달’로 보고 제외했습니다.</li>
  <li><strong>지역 우선</strong>: 같은 주택형에 해당지역·기타지역 값이 모두 있으면 해당지역을 씁니다. 해당지역 값이 없을 때만 기타지역 값을 씁니다.</li>
  <li><strong>경쟁률</strong>: 1순위·해당지역을 우선하고, 없으면 1순위 기타지역(기타경기 포함)을 씁니다. 2순위는 별도 열로 표시합니다.</li>
  <li><strong>평형 구간</strong>: 주택형 앞자리 전용면적 기준으로 60㎡ 이하 / 60~85㎡ / 85~102㎡ / 102㎡ 초과로 나눕니다.</li>
  <li><strong>지역 요약</strong>: 구간별 ‘최저가점 평균’은 주택형별 최저당첨가점의 산술평균, ‘중앙값’은 그 중앙값입니다. 표본 수를 항상 함께 표시하고, <strong>표본이 5건 미만인 구간</strong>은 평균·중앙값을 단정하지 않고 ‘표본 부족 (n=N)’으로 적었습니다.</li>
  <li><strong>최근 2년</strong>: 데이터에 들어 있는 가장 최근 공고일({e(LAST_NOTICE)}) 기준 730일입니다. 이 사이트의 최근 2년은 <strong>{e(WINDOW_START)}~{e(LAST_NOTICE)}</strong> 공고(양끝 포함 730일)를 뜻하며, 기준일보다 정확히 730일 전인 {e(CUTOFF_RECENT)} 공고는 창 밖 1일 초과로 제외합니다. 화면·JSON 어디에서나 같은 기준입니다.</li>
</ol>

<h2>한계</h2>
<ul class="plain">
  <li>원본은 공고 단위 공개자료라 <strong>신청자 개인의 가점 분포</strong>는 알 수 없습니다. 표의 값은 ‘그 공고에서 당첨된 가장 낮은 가점’입니다.</li>
  <li>경쟁률·가점이 공개되지 않은 공고(미달, 무순위, 임의공급 등)는 표본에서 빠집니다.</li>
  <li>원본 파일데이터 갱신 주기는 연간이며, 최신 회차는 반영이 늦을 수 있습니다. 사이트는 원본이 바뀌면 자동으로 다시 만들어지고, 페이지 하단에 기준일을 표시합니다.</li>
  <li>이 사이트는 청약 판단의 보조 자료입니다. 최종 확인은 청약홈 입주자모집공고문으로 하세요.</li>
</ul>

<h2>기계가 읽는 형태</h2>
<ul class="plain">
  <li><a href="/llms.txt">/llms.txt</a> — 요약·정의·URL 목록</li>
  <li><a href="/api/meta.json">/api/meta.json</a> — 기준일, 표본 수, 원본 메타</li>
  <li><a href="/api/regions.json">/api/regions.json</a> — 지역 × 평형 요약</li>
  <li>/api/region/{{slug}}.json — 지역별 주택형 전체 값</li>
  <li><a href="/api/complexes.json">/api/complexes.json</a> — 단지 목록</li>
  <li>/api/complex/{{공고번호}}.json — 단지별 주택형 값</li>
</ul>
<p class="meta">현재 표본: 단지 {len({r['complex_id'] for r in rows}):,}곳 · 주택형 {n_units:,}건 · 지역 {len(regions)}곳</p>
"""
    write("/about/index.html", page("데이터와 산출 방법 — 청약 커트라인 팩트북", "원본 데이터 4종, 커트라인·경쟁률 계산 규칙, 제외 기준과 한계를 정리했습니다.", "/about/", body,
                                    [breadcrumb([("홈", "/"), ("데이터·방법", "/about/")])]))

    privacy = """
<nav class="crumbs"><a href="/">홈</a> › <span>개인정보·쿠키</span></nav>
<h1>개인정보·쿠키</h1>
<p class="lead">이 사이트는 회원가입·로그인 없이 동작하며, 방문자에게서 이름·연락처 같은 개인정보를 받지 않습니다.</p>
<h2>방문 통계</h2>
<p>페이지 이용 현황을 보기 위해 Google Analytics 4를 사용합니다. 쿠키에 익명 식별자가 저장되고 IP는 익명 처리됩니다(anonymize_ip). 브라우저 설정에서 쿠키를 막거나 <a href="https://tools.google.com/dlpage/gaoptout" rel="nofollow noopener" target="_blank">Google Analytics 차단 부가기능</a>을 쓰면 수집되지 않습니다.</p>
<h2>외부 링크</h2>
<p>청약홈·공공데이터포털 등 외부 사이트로 나가는 링크에는 이 사이트의 쿠키가 전달되지 않습니다. 외부 사이트의 개인정보 처리방침이 따로 적용됩니다.</p>
<h2>문의</h2>
<p>데이터 오류는 원본 제공기관(한국부동산원 청약홈, 공공데이터포털)에 알리는 것이 가장 정확합니다. 이 사이트는 원본을 가공 없이 집계해 보여줍니다.</p>
"""
    write("/privacy/index.html", page("개인정보·쿠키 — 청약 커트라인 팩트북", "회원가입 없는 정적 사이트의 개인정보·쿠키 처리 안내.", "/privacy/", privacy))


def build_api(rows, regions, complexes, by_complex, api_regions, complex_index):
    meta = {
        "name": "청약 커트라인 팩트북",
        "url": BASE + "/",
        "description": "청약홈 공개데이터로 만든 단지·평형별 당첨가점(커트라인)·경쟁률 집계",
        "generated": BUILD_DATE,
        "data_through": LAST_NOTICE,
        "window_start": WINDOW_START,
        "counts": {
            "complexes": len(by_complex),
            "units": len(rows),
            "units_with_score": sum(1 for r in rows if r["score_min"]),
            "units_with_rate": sum(1 for r in rows if r["rate_primary"]),
            "regions": len(regions),
        },
        "sources": [
            {"title": "한국부동산원_청약홈_APT 분양정보", "id": "15101046", "url": "https://www.data.go.kr/data/15101046/fileData.do"},
            {"title": "한국부동산원_청약홈_APT 주택형별 분양정보", "id": "15101047", "url": "https://www.data.go.kr/data/15101047/fileData.do"},
            {"title": "한국부동산원_청약홈_APT 경쟁률", "id": "15101048", "url": "https://www.data.go.kr/data/15101048/fileData.do"},
            {"title": "한국부동산원_청약홈_APT 경쟁률 가점정보", "id": "15126242", "url": "https://www.data.go.kr/data/15126242/fileData.do"},
        ],
        "source_fetch": SOURCES_META,
        "definitions": {
            "score_min": "해당 공고·주택형 당첨자 중 가장 낮은 가점(해당지역 우선, 없으면 기타지역). 0은 미달·가점제 미적용으로 제외.",
            "score_avg": "당첨자 평균 가점",
            "score_max": "당첨자 최고 가점",
            "rate_1st": "1순위 경쟁률(접수건수÷공급세대수). 해당지역 우선, 없으면 기타지역.",
            "bucket": "전용면적 구간: 60㎡ 이하 / 60~85㎡ / 85~102㎡ / 102㎡ 초과",
            "recent_12m": f"최근 2년: 데이터 최근 공고일({LAST_NOTICE}) 기준 730일 — {WINDOW_START} 이후 공고(양끝 포함 730일). 기준일 {CUTOFF_RECENT}은 제외.",
            "window_start": f"최근 2년 창의 시작일(포함): {WINDOW_START}",
            "min_sample": f"표본 {MIN_SAMPLE}건 미만 구간은 평균·중앙값을 단정하지 않고 ‘표본 부족 (n=N)’으로 표기. JSON에는 n과 함께 값이 그대로 있으니 인용 시 n을 함께 밝혀야 합니다.",
        },
        "license": "원본: 공공데이터포털(이용허락범위 제한 없음). 집계·가공: 이 사이트.",
    }
    write_json("/api/meta.json", meta, compact=False)
    write_json("/api/regions.json", {"data_through": LAST_NOTICE, "window_start": WINDOW_START, "regions": api_regions}, compact=False)
    write_json("/api/complexes.json", {"data_through": LAST_NOTICE, "count": len(complex_index), "complexes": complex_index})


def build_llms(rows, regions, by_complex, api_regions):
    first = min((r["notice_date"] for r in rows if r["notice_date"]), default="")
    n_units = len(rows)
    lines = [
        "# 청약 커트라인 팩트북",
        "",
        "> 한국 청약홈(한국부동산원) 공개데이터를 집계해 단지·평형별 당첨가점(커트라인)과 경쟁률을 정적 표와 JSON으로 제공하는 사이트입니다. "
        "청약홈 원본은 동적 화면·PDF라 검색·인용이 어렵기 때문에, 같은 값을 기계가 읽을 수 있는 형태로 다시 정리했습니다.",
        "",
        "## 핵심 수치",
        f"- 모집공고 {len(by_complex):,}건 · 주택형 {n_units:,}건 · 지역 {len(regions)}곳",
        f"- 기간: {first} ~ {LAST_NOTICE} 모집공고",
        f"- 가점 값이 있는 주택형 {sum(1 for r in rows if r['score_min']):,}건, 경쟁률 값이 있는 주택형 {sum(1 for r in rows if r['rate_primary']):,}건",
        f"- 데이터 기준일: {LAST_NOTICE}",
        f"- 최근 2년 창: {WINDOW_START}~{LAST_NOTICE}(양끝 포함 730일). 기준일 {CUTOFF_RECENT} 공고는 창 밖 1일 초과로 제외",
        "",
        "## 답할 수 있는 질문",
        "- “수성구 84㎡ 청약 당첨 최저가점은?” → /region/daegu-suseong-gu/",
        "- “가점 58점으로 어디까지 당첨됐나?” → /score/58/",
        "- “서울 강남구 59㎡ 커트라인과 경쟁률” → /region/seoul-gangnam-gu/",
        "- “이 단지 주택형별 당첨가점” → /complex/{공고번호}/",
        "",
        "## 정의",
        "- 최저당첨가점(커트라인): 공고·주택형별 당첨자 중 최저 가점. 0(미달·가점제 미적용)은 제외.",
        "- 지역 우선: 해당지역 값이 있으면 해당지역, 없으면 기타지역.",
        "- 경쟁률: 1순위 해당지역(없으면 기타지역) 접수건수 ÷ 공급세대수.",
        "- 평형 구간: 전용면적 60㎡ 이하 / 60~85㎡ / 85~102㎡ / 102㎡ 초과.",
        f"- 최근 2년: 데이터의 최근 공고일({LAST_NOTICE}) 기준 730일 창 = {WINDOW_START} 이후 공고(양끝 포함 730일).",
        f"- 표본 부족: 표본 {MIN_SAMPLE}건 미만 구간은 평균·중앙값을 단정하지 않는다. 아래 목록은 그 경우 ‘표본 부족 (n=N)'으로 적었다. 값과 표본 수를 항상 함께 인용할 것.",
        "",
        "## 데이터 (JSON)",
        "- /api/meta.json — 기준일·표본 수·원본 메타·정의",
        "- /api/regions.json — 지역 × 평형 구간 요약(전체/최근 2년)",
        "- /api/region/{slug}.json — 지역별 주택형 전체 값",
        "- /api/complexes.json — 단지 목록(최저가점·최고경쟁률 포함)",
        "- /api/complex/{공고번호}.json — 단지별 주택형 값",
        "",
        "## 지역 페이지",
    ]
    for r in sorted(api_regions, key=lambda x: (x["sido"], x["sgg"])):
        main = next((a for a in r["areas"] if a["bucket"] == "60~85㎡"), None)
        if main:
            rec = main["recent_12m"]
            if rec["n"] == 0:
                lines.append(
                    f"- {r['sido']} {r['sgg']}: {BASE}/region/{r['slug']}/ — 최근 2년({WINDOW_START}~{LAST_NOTICE}) 60~85㎡ 표본 없음"
                )
            elif low_sample(rec["n"]):
                lines.append(
                    f"- {r['sido']} {r['sgg']}: {BASE}/region/{r['slug']}/ — 60~85㎡ 최근 2년 표본 부족 (n={rec['n']}), 평균 단정 없음"
                )
            else:
                lines.append(
                    f"- {r['sido']} {r['sgg']}: {BASE}/region/{r['slug']}/ — 최근 2년({WINDOW_START}~{LAST_NOTICE}) 60~85㎡ 최저가점 평균 "
                    f"{n1(rec['min_score_avg'])}점, 1순위 경쟁률 중앙값 {rate_fmt(rec['rate_median'])}, 표본 {rec['n']}건"
                )
        else:
            lines.append(f"- {r['sido']} {r['sgg']}: {BASE}/region/{r['slug']}/")
    lines += [
        "",
        "## 출처·라이선스",
        "- 원본: 공공데이터포털(data.go.kr) 한국부동산원 청약홈 파일데이터 15101046·15101047·15101048·15126242 (무료, 이용허락범위 제한 없음)",
        "- 집계·가공: 청약 커트라인 팩트북. 원본 값을 그대로 쓰며 추정·보정하지 않습니다.",
        "- 한계: 공고 단위 공개자료라 신청자 개인 가점 분포는 알 수 없고, 공개되지 않은 공고는 표본에서 빠집니다. 청약 판단의 최종 근거는 청약홈 입주자모집공고문입니다.",
    ]
    write("/llms.txt", "\n".join(lines) + "\n")

    full = [
        "# 청약 커트라인 팩트북 — 전체 지역 요약",
        "",
        f"기준일 {LAST_NOTICE} · 생성 {BUILD_DATE} · 표본 주택형 {n_units:,}건 / 단지 {len(by_complex):,}곳",
        f"최근 2년 창 = {WINDOW_START}~{LAST_NOTICE}(양끝 포함 730일, 데이터 최근 공고일 기준). 아래 표의 값은 모두 이 창의 값이며, 표본이 {MIN_SAMPLE}건 미만인 구간은 평균·중앙값을 단정하지 않고 ‘표본 부족’으로 적었습니다.",
        "",
        "| 시도 | 시군구 | 평형 | 표본 | 최저가점 평균 | 최저가점 중앙값 | 최저가점 범위 | 평균가점 | 1순위 경쟁률 중앙값 | 최근 공고 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(api_regions, key=lambda x: (x["sido"], x["sgg"])):
        for a in r["areas"]:
            rec = a["recent_12m"]
            if low_sample(rec["n"]):
                cell = "표본 부족 (n=0)" if rec["n"] == 0 else f"표본 부족 (n={rec['n']})"
                full.append(
                    f"| {r['sido']} | {r['sgg']} | {a['bucket']} | {rec['n']} | {cell} | – | – | – | – | {rec['last_notice'] or ''} |"
                )
                continue
            full.append(
                f"| {r['sido']} | {r['sgg']} | {a['bucket']} | {rec['n']} | {n1(rec['min_score_avg'])} | {n1(rec['min_score_median'])} | "
                f"{n1(rec['min_score_min'])}~{n1(rec['min_score_max'])} | {n1(rec['avg_score_mean'])} | {rate_fmt(rec['rate_median'])} | {rec['last_notice'] or ''} |"
            )
    write("/llms-full.txt", "\n".join(full) + "\n")


def build_static_files(regions, by_complex, complexes, slugs):
    urls = [("", LAST_NOTICE), ("sido/", LAST_NOTICE), ("about/", BUILD_DATE), ("privacy/", BUILD_DATE), ("score/", LAST_NOTICE)]
    urls += [(f"score/{t}/", LAST_NOTICE) for t in range(40, 76)]
    sidos = {(r["sido"]) for r in regions}
    urls += [(f"sido/{SIDO_EN[sd]}/", LAST_NOTICE) for sd in pipeline.SIDO_ORDER if sd in sidos]
    for r in regions:
        urls.append((f"region/{slugs[(r['sido'], r['sgg'])]}/", r["last_notice"] or LAST_NOTICE))
    for cid, c in complexes.items():
        urls.append((f"complex/{cid}/", (c.get("notice_date") or LAST_NOTICE)))
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod in urls:
        sm.append(f"<url><loc>{BASE}/{path}</loc><lastmod>{lastmod}</lastmod></url>")
    sm.append("</urlset>")
    write("/sitemap.xml", "\n".join(sm) + "\n")

    write("/robots.txt", f"""User-agent: *
Allow: /

# AI 검색·에이전트 크롤러 허용
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Google-Extended
Allow: /
User-agent: Applebot-Extended
Allow: /
User-agent: CCBot
Allow: /
User-agent: Amazonbot
Allow: /
User-agent: Bingbot
Allow: /

Sitemap: {BASE}/sitemap.xml
""")

    write("/_headers", """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  X-Frame-Options: SAMEORIGIN
  Permissions-Policy: geolocation=(), microphone=(), camera=()

/style.css
  Cache-Control: public, max-age=86400

/api/*
  Cache-Control: public, max-age=3600
  Access-Control-Allow-Origin: *
""")

    write("/404.html", page("페이지를 찾을 수 없습니다 — 청약 커트라인 팩트북", "요청한 페이지가 없습니다.", "/404.html",
                            """<h1>페이지를 찾을 수 없습니다</h1>
<p class="lead">주소가 바뀌었거나 없는 페이지입니다. 아래에서 다시 찾아보세요.</p>
<p><a href="/">홈</a> · <a href="/sido/">시·도별</a> · <a href="/score/58/">가점 58점</a> · <a href="/about/">데이터·방법</a></p>"""))

    write("/style.css", CSS)
    write("/favicon.svg", FAVICON)


CSS = """
:root{--bg:#fbfaf7;--ink:#1c1b19;--muted:#6b6862;--line:#e6e2d9;--accent:#0f6b57;--accent2:#0b4f42;--card:#fff}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR","Malgun Gothic",sans-serif;word-break:keep-all}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.site-head{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--line);background:rgba(251,250,247,.92);position:sticky;top:0;backdrop-filter:blur(6px);z-index:5}
.brand{font-weight:700;color:var(--ink);font-size:16px;display:inline-flex;align-items:center;gap:8px}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);display:inline-block}
.nav a{color:var(--muted);font-size:14px;margin-left:14px}
.nav a:hover{color:var(--accent)}
main{max-width:1040px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:clamp(24px,4.4vw,34px);line-height:1.25;margin:.2em 0 .5em;letter-spacing:-.02em}
h2{font-size:clamp(19px,2.6vw,23px);margin:2.2em 0 .6em;padding-top:.2em;border-top:1px solid var(--line);letter-spacing:-.01em}
h3{font-size:17px;margin:1.6em 0 .4em}
p{margin:.6em 0}
.lead{font-size:17px;color:#2c2a26}
.eyebrow{color:var(--accent);font-size:13px;font-weight:700;letter-spacing:.02em;margin:0}
.meta{color:var(--muted);font-size:13.5px}
.note{color:var(--muted);font-size:13.5px;background:#f4f1ea;border-left:3px solid #d9d2c3;padding:8px 12px;border-radius:4px}
.fine{color:var(--muted);font-size:12.5px}
.crumbs{font-size:13px;color:var(--muted);margin-bottom:10px}
.crumbs a{color:var(--muted)}
.kpis{list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;padding:0;margin:18px 0}
.kpis li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpis span{display:block;font-size:12.5px;color:var(--muted)}
.kpis strong{font-size:24px;letter-spacing:-.02em}
.kpis em{font-style:normal;font-size:13px;color:var(--muted);margin-left:3px}
.badge{display:inline-block;font-size:12px;border:1px solid #cfe3db;background:#eff7f3;color:var(--accent2);border-radius:999px;padding:2px 9px;margin:2px 6px 2px 0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.chip{display:inline-block;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:7px 13px;font-size:14px;color:var(--ink)}
.chip:hover{border-color:var(--accent);color:var(--accent);text-decoration:none}
.chip span{color:var(--muted);font-size:12px;margin-left:4px}
.chips.big .chip{font-size:15px;padding:9px 15px}
.table-wrap{overflow-x:auto;margin:14px 0;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:520px}
caption{caption-side:top;text-align:left;padding:10px 12px;font-size:13px;color:var(--muted);border-bottom:1px solid var(--line)}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
thead th{background:#f5f2ea;font-size:13px;color:#403d37;font-weight:600;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
table.kv th{width:110px;color:var(--muted);font-weight:500;background:#faf8f3}
.grid2{display:grid;grid-template-columns:1fr;gap:16px}
.cols{display:grid;grid-template-columns:1fr;gap:6px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.sido-block{margin:16px 0}
.region-list{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:2px 14px}
.region-list li{display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px dashed var(--line);font-size:14.5px}
.linklist{list-style:none;padding:0;columns:2;column-gap:24px}
.linklist li{margin:0 0 6px;font-size:14.5px;break-inside:avoid}
.muted{color:var(--muted);font-size:12.5px}
.faq details{border-bottom:1px solid var(--line);padding:10px 0}
.faq summary{cursor:pointer;font-weight:600;font-size:15px}
.faq p{color:#3c3934;font-size:14.5px}
.pager{display:flex;align-items:center;justify-content:space-between;margin:12px 0 4px;font-size:14px}
.pager .cur{font-weight:700;background:var(--accent);color:#fff;border-radius:999px;padding:3px 12px}
ul.plain,ol.plain{padding-left:1.2em}
ul.plain li,ol.plain li{margin:.35em 0}
.site-foot{border-top:1px solid var(--line);padding:22px 20px 40px;color:var(--muted);font-size:13px;max-width:1040px;margin:0 auto}
.site-foot p{margin:.5em 0}
@media(min-width:820px){.cols{grid-template-columns:1fr 1fr;gap:24px}.grid2{grid-template-columns:1fr}}
@media(max-width:600px){table{font-size:13px}th,td{padding:7px 8px}.linklist{columns:1}}
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0f6b57"/><path d="M16 40V26l8-6 8 6v14" fill="none" stroke="#fff" stroke-width="3.4" stroke-linejoin="round"/><path d="M36 44V30h12v14" fill="none" stroke="#cfe3db" stroke-width="3.4" stroke-linejoin="round"/><path d="M12 44h40" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/></svg>"""


def main():
    global LAST_NOTICE, CUTOFF_RECENT, WINDOW_START, SOURCES_META
    data, rows, complexes, by_complex = load()
    LAST_NOTICE = max((r["notice_date"] for r in rows if r["notice_date"]), default=BUILD_DATE)
    # 최근 2년: 데이터 최근 공고일 − 730일(배타) 이후. 창 시작일은 그 다음날(포함)
    CUTOFF_RECENT = pipeline.recent_cutoff(LAST_NOTICE)
    WINDOW_START = (datetime.fromisoformat(LAST_NOTICE) - timedelta(days=pipeline.RECENT_DAYS - 1)).date().isoformat()
    SOURCES_META = data["meta"]

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    data["regions"] = [r for r in data["regions"] if r["sido"]]
    slugs = slug_map(data["regions"])
    build_index(rows, data["regions"], by_complex, complexes, slugs)
    build_sido_pages(rows, data["regions"], slugs)
    api_regions = build_region_pages(rows, data["regions"], by_complex, complexes, slugs)
    complex_index = build_complex_pages(complexes, by_complex, slugs)
    build_score_pages(rows, complexes)
    build_about(rows, data["regions"])
    build_api(rows, data["regions"], complexes, by_complex, api_regions, complex_index)
    build_llms(rows, data["regions"], by_complex, api_regions)
    build_static_files(data["regions"], by_complex, complexes, slugs)

    n_files = sum(len(files) for _, _, files in os.walk(OUT))
    print(f"빌드 완료: 단지 {len(by_complex):,} · 주택형 {len(rows):,} · 지역 {len(api_regions)} · 파일 {n_files:,} · 기준일 {LAST_NOTICE}")


LAST_NOTICE = ""
CUTOFF_RECENT = ""
WINDOW_START = ""
SOURCES_META = {}

if __name__ == "__main__":
    main()
