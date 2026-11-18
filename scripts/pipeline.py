#!/usr/bin/env python3
"""청약홈 공개데이터 정규화·집계 (LLM 없이 순수 파이썬).

원본: 공공데이터포털 파일데이터 4종 (data/raw/*.csv, CP949)
산출: 단지(공고)·주택형·지역 집계 사전
"""
import csv
import io
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CACHE = os.path.join(ROOT, "data", "cache")
KST = timezone(timedelta(hours=9))

SIDO_SHORT = {
    "서울": "서울", "서울특별시": "서울",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "인천": "인천", "인천광역시": "인천",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주",
}
SIDO_ORDER = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
              "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
SIDO_RE = re.compile(
    r"^(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
    r"경기도|강원특별자치도|강원도|충청북도|충청남도|전라북도|전북특별자치도|전라남도|경상북도|경상남도|"
    r"제주특별자치도|제주도|경기|서울|부산|대구|인천|광주|대전|울산|세종)(?:\s+|$)"
)
SGG_TOKEN = re.compile(r"^([가-힣]{1,4}(?:시|군|구))$")

AREA_BUCKETS = [
    ("60㎡ 이하", 0.0, 60.0),
    ("60~85㎡", 60.0, 85.0),
    ("85~102㎡", 85.0, 102.0),
    ("102㎡ 초과", 102.0, 10_000.0),
]


# ---------------------------------------------------------------- 원본 읽기
def read_csv(key):
    path = os.path.join(RAW, f"{key}.csv")
    blob = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = blob.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"{path}: 인코딩을 알 수 없음")
    return list(csv.DictReader(io.StringIO(text)))


def load_sources_meta():
    path = os.path.join(RAW, "sources.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_detail_cache():
    path = os.path.join(CACHE, "detail_meta.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------- 지역 파싱
def sido_of(region_name, address):
    rn = (region_name or "").strip()
    if rn in SIDO_SHORT:
        return SIDO_SHORT[rn]
    m = SIDO_RE.match(address or "")
    if m:
        return SIDO_SHORT.get(m.group(1))
    # 주소 앞에 시·도가 없는 경우(예: "김포 풍무역세권 C5블록 (경기도 김포시 …)") 본문에서 찾는다
    m2 = re.search(
        r"(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
        r"경기도|강원특별자치도|강원도|충청북도|충청남도|전라북도|전북특별자치도|전라남도|"
        r"경상북도|경상남도|제주특별자치도|제주도)", address or "")
    if m2:
        return SIDO_SHORT.get(m2.group(1))
    return None


def strip_sido(address):
    return SIDO_RE.sub("", (address or "").strip()).strip()


def sgg_from_address(address, known_sgg=(), sido=None):
    """주소 문자열에서 시·군·구 이름을 뽑는다.

    - '경기도 수원시 장안구 …' → '수원시' (구가 있는 시는 시 단위로 묶는다)
    - '경기도 성남수정구 …'처럼 시·구가 붙아 있는 표기도 시 단위로 정리
    - '공공주택지구'·'신도시' 같은 지구·특구 이름은 지역명이 아니므로 건너뛴다
    """
    raw = address or ""
    a = re.sub(r"[()]", " ", strip_sido(raw))
    bad_words = ("지구", "신도시", "특구", "도시", "택지", "산업단지", "지식정보타운")
    for token in a.split()[:4]:
        token = token.split("(")[0].split(",")[0]
        if any(b in token for b in bad_words):
            continue
        m = SGG_TOKEN.match(token)
        if not m:
            continue
        name = m.group(1)
        if "광역" in name or "특별" in name:
            continue  # '인천광역시' 같은 시·도 표기
        if name.endswith("시") and sido and name[:-1] == sido:
            continue  # 주소 맨 앞의 시·도 표기(예: '서울시 …')
        if sido and name.startswith(sido) and len(name) > len(sido) + 1:
            continue  # '인천검단시'처럼 시·도가 붙은 표기 → 다음 토큰에서 판단
        if name.endswith("특례시"):
            return name[:-3] + "시"
        if name.endswith("구"):
            mc = re.search(r"(?<![가-힣])([가-힣]{2,4})시(?=\s|$)", raw)
            if (mc and mc.start() > 0 and "특별" not in mc.group(1) and "광역" not in mc.group(1)
                    and not raw.strip().startswith(mc.group(0))):
                return mc.group(1) + "시"
            return name
        return name
    # 지구·신도시 이름으로 건너뛴 경우: 주소 안에 알려진 시·군·구 이름의 예간이 있는지
    for name in sorted(known_sgg, key=len, reverse=True):
        stem = name[:-1]
        if len(stem) >= 2 and stem in raw:
            return name
    return None


def area_bucket(area):
    for label, lo, hi in AREA_BUCKETS:
        if lo < area <= hi:
            return label
    return AREA_BUCKETS[-1][0]


def parse_unit_spec(type_str):
    """'084.9079A' → 84.9079 (전용면적)"""
    m = re.match(r"\s*(\d+(?:\.\d+)?)", type_str or "")
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- 정규화
def norm_complexes(info_rows, detail_cache):
    out = {}
    for row in info_rows:
        gid = (row.get("공고번호") or "").strip()
        if not gid:
            continue
        addr = (row.get("공급위치") or "").strip()
        sido = sido_of(row.get("공급지역명"), addr)
        out[gid] = {
            "id": gid,
            "name": (row.get("주택명") or "").strip(),
            "sido": sido,
            "sgg": None,
            "address": addr,
            "notice_date": (row.get("모집공고일") or "").strip(),
            "apply_start": (row.get("청약접수시작일") or "").strip(),
            "apply_end": (row.get("청약접수종료일") or "").strip(),
            "winner_date": (row.get("당첨자발표일") or "").strip(),
            "house_type": (row.get("주택구분코드명") or "").strip(),
            "detail_type": (row.get("주택상세구분코드명") or "").strip(),
            "scale": (row.get("공급규모") or "").strip(),
            "builder": (row.get("건설업체명_시공사") or "").strip(),
            "developer": (row.get("사업주체명_시행사") or "").strip(),
            "move_in": (row.get("입주예정월") or "").strip(),
            "speculation": (row.get("투기과열지구") or "").strip() == "Y",
            "adjust": (row.get("조정대상지역") or "").strip() == "Y",
            "price_cap": (row.get("분양가상한제") or "").strip() == "Y",
            "source": "portal",
        }
    # 포털 분양정보에 없는 최근 공고는 청약홈 상세페이지 캐시로 채운다
    for gid, meta in detail_cache.items():
        if gid in out and out[gid]["name"]:
            continue
        base = out.get(gid, {"id": gid, "source": "applyhome"})
        base.update({
            "name": meta.get("name") or base.get("name") or "",
            "address": meta.get("address") or base.get("address") or "",
            "notice_date": meta.get("notice_date") or base.get("notice_date") or "",
            "scale": meta.get("scale") or base.get("scale") or "",
        })
        base["sido"] = sido_of(None, base.get("address"))
        base["source"] = "applyhome"
        out[gid] = base
    return out


def assign_regions(complexes):
    """단지 주소에서 지역(시도·시군구)을 채운다.

    1) 주소에서 시·군·구를 뽑고
    2) '성남수정구'처럼 시·구가 붙은 표기는 시 단위로 합치고
    3) 지구·신도시 이름처럼 지역이 아닌 값은 한 번 더 사전 대조한 뒤 버린다.
    """
    bad_words = ("지구", "신도시", "특구", "도시", "택지", "산업단지", "지식정보타운")
    for c in complexes.values():
        if c.get("sido") == "세종":
            c["sgg"] = "세종"
            continue
        c["sgg"] = sgg_from_address(c.get("address") or "", (), c.get("sido"))

    counts = {}
    for c in complexes.values():
        sgg = c.get("sgg")
        if not sgg:
            continue
        if any(b in sgg for b in bad_words) or sgg == (c.get("sido") or "") + "시":
            continue
        counts[sgg] = counts.get(sgg, 0) + 1
    # 한 번만 나온 이름은 오타·특수 표기일 가능성이 높아 대조 사전에서 제외한다
    known = {k for k, v in counts.items() if v >= 2}
    city_stems = {k[:-1] for k in known if k.endswith("시") and len(k) >= 3}

    for c in complexes.values():
        sgg = c.get("sgg")
        if not sgg:
            continue
        if sgg.endswith("구") and len(sgg) >= 4:
            for stem in sorted(city_stems, key=len, reverse=True):
                if sgg.startswith(stem) and sgg != stem + "구":
                    c["sgg"] = stem + "시"   # 성남수정구 → 성남시
                    sgg = c["sgg"]
                    break
        if any(b in sgg for b in bad_words) or sgg == (c.get("sido") or "") + "시":
            c["sgg"] = sgg_from_address(c.get("address") or "", known, c.get("sido"))
    return complexes


def norm_units(unit_rows):
    units = {}
    for row in unit_rows:
        gid = (row.get("공고번호") or "").strip()
        model = (row.get("모델번호") or "").strip()
        if not gid or not model:
            continue
        area = parse_unit_spec(row.get("주택형"))
        def num(k):
            v = row.get(k)
            try:
                return float(str(v).replace(",", "")) if v not in (None, "") else None
            except ValueError:
                return None
        units[(gid, model)] = {
            "type": (row.get("주택형") or "").strip(),
            "area": area,
            "area_exclusive": num("주택공급면적"),
            "general": num("일반공급세대수"),
            "special": num("특별공급세대수"),
            "price_max": num("공급금액_분양최고금액"),
        }
    return units


def fill_units_from_market(units, score_rows, rate_rows):
    """주택형별 분양정보 파일이 아직 갱신되지 않은 최근 공고는
    경쟁률·가점 파일의 주택형(전용면적)·모델번호로 주택형을 채운다(세대수·분양가는 비움)."""
    for row in list(score_rows) + list(rate_rows):
        gid = (row.get("공고번호") or "").strip()
        model = (row.get("모델번호") or "").strip()
        if not gid or not model or (gid, model) in units:
            continue
        t = (row.get("주택형") or "").strip()
        units[(gid, model)] = {
            "type": t,
            "area": parse_unit_spec(t),
            "area_exclusive": None,
            "general": None,
            "special": None,
            "price_max": None,
        }
    return units


def norm_scores(score_rows):
    """(공고, 모델) → {'해당지역': {...}, '기타지역': {...}} (0은 미달/미적용으로 보고 버린다)"""
    scores = defaultdict(dict)
    for row in score_rows:
        gid = (row.get("공고번호") or "").strip()
        model = (row.get("모델번호") or "").strip()
        zone = (row.get("거주지역") or "").strip()
        if not gid or not model or not zone:
            continue
        try:
            lo = float(row.get("최저당첨가점") or 0)
            hi = float(row.get("최고당첨가점") or 0)
            avg = float(row.get("평균당첨가점") or 0)
        except ValueError:
            continue
        if lo <= 0 or hi <= 0:
            continue
        scores[(gid, model)][zone] = {"min": lo, "max": hi, "avg": avg}
    return scores


def norm_rates(rate_rows):
    """(공고, 모델) → {'1': {'해당지역': (건수, 경쟁률), ...}, '2': {...}}"""
    rates = defaultdict(lambda: defaultdict(dict))
    for row in rate_rows:
        gid = (row.get("공고번호") or "").strip()
        model = (row.get("모델번호") or "").strip()
        rank = (row.get("순위") or "").strip()
        zone = (row.get("거주지역") or "").strip()
        if not gid or not model or not rank or not zone:
            continue
        try:
            rate = float(row.get("경쟁률") or 0)
            cnt = float(row.get("접수건수") or 0)
        except ValueError:
            continue
        if rate <= 0:
            continue
        rates[(gid, model)][rank][zone] = {"rate": rate, "count": cnt}
    return rates


# ---------------------------------------------------------------- 집계
def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def round1(v):
    return None if v is None else round(v + 1e-9, 1)


def build_units_table(complexes, units, scores, rates):
    """(공고, 모델) 단위로 가점·경쟁률을 붙인 표를 만든다."""
    rows = []
    for (gid, model), u in sorted(units.items()):
        comp = complexes.get(gid)
        if not comp or not comp.get("sgg"):
            continue
        sc = scores.get((gid, model), {})
        rt = rates.get((gid, model), {})
        r1 = rt.get("1", {})
        r2 = rt.get("2", {})
        base = sc.get("해당지역") or sc.get("기타지역")
        if not base and not r1 and not r2:
            continue
        area = u["area"]
        if area is None:
            continue

        def pick_zone(d):
            return d.get("해당지역") or d.get("기타지역") or None

        s_here = sc.get("해당지역")
        s_other = sc.get("기타지역")
        r1_here = r1.get("해당지역")
        r1_other = r1.get("기타지역") or r1.get("기타경기")
        r2_any = r2.get("해당지역") or r2.get("기타지역") or r2.get("기타경기")
        rows.append({
            "complex_id": gid,
            "model": model,
            "name": comp["name"],
            "sido": comp["sido"],
            "sgg": comp["sgg"],
            "notice_date": comp.get("notice_date") or "",
            "area": round(area, 1),
            "area_label": u["type"],
            "bucket": area_bucket(area),
            "general": u["general"],
            "special": u["special"],
            "price_max": u["price_max"],
            "score_min": (s_here or s_other or {}).get("min"),
            "score_max": (s_here or s_other or {}).get("max"),
            "score_avg": (s_here or s_other or {}).get("avg"),
            "score_min_other": (s_other or {}).get("min"),
            "score_zone": "해당지역" if s_here else ("기타지역" if s_other else None),
            "rate1_here": (r1_here or {}).get("rate"),
            "rate1_other": (r1_other or {}).get("rate"),
            "rate1_count": (r1_here or {}).get("count"),
            "rate2": (r2_any or {}).get("rate"),
            "rate_primary": (r1_here or r1_other or {}).get("rate"),
        })
    return rows


RECENT_DAYS = 730  # ‘최근 2년’ 창(일). 지침서 §5와 같은 값


def recent_cutoff(last_notice):
    """최근 2년 창의 배타 기준일.

    이 날짜 **이후**(>) 공고만 최근 2년으로 센다. 예: 최근 공고일 2026-08-21 →
    기준일 2024-08-21 → 창은 2024-08-22~2026-08-21(양끝 포함 730일).
    """
    if not last_notice:
        return None
    return (datetime.fromisoformat(last_notice) - timedelta(days=RECENT_DAYS)).date().isoformat()


def summarize(rows, cutoff=None):
    if cutoff:
        # 기준일 당일(창 밖 1일 초과)은 제외해 창 길이를 정확히 730일로 맞춘다
        rows = [r for r in rows if r["notice_date"] > cutoff]
    mins = [r["score_min"] for r in rows if r["score_min"]]
    avgs = [r["score_avg"] for r in rows if r["score_avg"]]
    maxs = [r["score_max"] for r in rows if r["score_max"]]
    rates_ = [r["rate_primary"] for r in rows if r["rate_primary"]]
    if not rows:
        return None
    return {
        "n": len(rows),
        "complexes": len({r["complex_id"] for r in rows}),
        "min_score_avg": round1(statistics.fmean(mins)) if mins else None,
        "min_score_median": round1(statistics.median(mins)) if mins else None,
        "min_score_low": round1(pct(mins, 0.25)) if mins else None,
        "min_score_min": round1(min(mins)) if mins else None,
        "min_score_max": round1(max(mins)) if mins else None,
        "avg_score_mean": round1(statistics.fmean(avgs)) if avgs else None,
        "max_score_mean": round1(statistics.fmean(maxs)) if maxs else None,
        "rate_median": round1(statistics.median(rates_)) if rates_ else None,
        "rate_avg": round1(statistics.fmean(rates_)) if rates_ else None,
        "last_notice": max((r["notice_date"] for r in rows if r["notice_date"]), default=None),
    }


def empty_summary():
    """최근 창에 자료가 없을 때의 요약값.

    전체기간 값으로 대신 채우지 않는다(‘최근’ 칸에 옛 날짜가 실리는 오염 방지).
    """
    return {
        "n": 0,
        "complexes": 0,
        "min_score_avg": None,
        "min_score_median": None,
        "min_score_low": None,
        "min_score_min": None,
        "min_score_max": None,
        "avg_score_mean": None,
        "max_score_mean": None,
        "rate_median": None,
        "rate_avg": None,
        "last_notice": None,
    }


def build_region_index(unit_rows):
    """지역(시도+시군구) × 평형구간 집계.

    ‘최근 2년’ 창은 오늘 날짜가 아니라 **데이터의 최근 공고일** 기준으로 잡는다
    (빌드 시점에 따라 창이 흔들리지 않게).
    """
    by_region = defaultdict(list)
    for r in unit_rows:
        by_region[(r["sido"], r["sgg"])].append(r)
    last_notice_all = max((r["notice_date"] for r in unit_rows if r["notice_date"]), default="")
    cutoff_recent = recent_cutoff(last_notice_all)
    regions = []
    for (sido, sgg), rws in by_region.items():
        areas = []
        for label, _, _ in AREA_BUCKETS:
            sub = [r for r in rws if r["bucket"] == label]
            allsum = summarize(sub)
            if not allsum:
                continue
            recent_sum = summarize(sub, cutoff_recent) if cutoff_recent else None
            areas.append({
                "bucket": label,
                "all": allsum,
                "recent_12m": recent_sum or empty_summary(),
                "has_recent": recent_sum is not None,
            })
        if not areas:
            continue
        region_recent = summarize(rws, cutoff_recent) if cutoff_recent else None
        regions.append({
            "sido": sido,
            "sgg": sgg,
            "areas": areas,
            "totals": summarize(rws),
            "recent_12m": region_recent or empty_summary(),
            "has_recent": region_recent is not None,
            "last_notice": max((r["notice_date"] for r in rws if r["notice_date"]), default=None),
            "complex_ids": sorted({r["complex_id"] for r in rws}, reverse=True),
        })
    regions.sort(key=lambda x: (SIDO_ORDER.index(x["sido"]) if x["sido"] in SIDO_ORDER else 99, x["sgg"]))
    return regions


def build_all():
    info_rows = read_csv("info")
    unit_rows = read_csv("unit")
    score_rows = read_csv("score")
    rate_rows = read_csv("rate")
    detail_cache = load_detail_cache()

    complexes = assign_regions(norm_complexes(info_rows, detail_cache))
    units = norm_units(unit_rows)
    scores = norm_scores(score_rows)
    rates = norm_rates(rate_rows)
    units = fill_units_from_market(units, score_rows, rate_rows)
    rows = build_units_table(complexes, units, scores, rates)
    regions = build_region_index(rows)
    return {
        "complexes": complexes,
        "units": units,
        "scores": scores,
        "rates": rates,
        "rows": rows,
        "regions": regions,
        "meta": load_sources_meta(),
    }
