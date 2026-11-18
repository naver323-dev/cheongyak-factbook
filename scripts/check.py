#!/usr/bin/env python3
"""사이트 산출물 자동 검증(배포 전·CI 공통).

실행: python3 scripts/check.py [--ga G-XXXX]
검사 항목
 1. 필수 파일 존재 · JSON 파싱
 2. 페이지 수와 sitemap·API 목록의 일치
 3. 모든 페이지의 canonical·viewport·GA 태그
 4. 금지 문구(실명·군 관련) 미포함
 5. 값 정합성(최저 ≤ 평균 ≤ 최고, 0~84점, 표본≠0)
 6. llms.txt 내부 링크가 실제 파일과 일치
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
# 검사할 금지 문구는 저장소에 남기지 않는다.
#   - 로컬: config/forbidden_terms.txt (gitignore) 또는 FORBIDDEN_TERMS 환경변수
#   - CI  : 저장소 변수/시크릿으로 주입(없으면 이 항목만 건너뛴다)
def _load_terms():
    terms = [t.strip() for t in os.environ.get("FORBIDDEN_TERMS", "").split(",") if t.strip()]
    path = os.path.join(ROOT, "config", "forbidden_terms.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    terms.append(line)
    return terms


FORBIDDEN = []
FORBIDDEN_AUTHORED = []
AUTHORED_FILES = ["index.html", "about/index.html", "privacy/index.html", "llms.txt", "llms-full.txt", "api/meta.json"]
errors = []
warns = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def read(path):
    with open(os.path.join(SITE, path), encoding="utf-8") as f:
        return f.read()


def walk_html():
    for base, _, files in os.walk(SITE):
        for f in files:
            if f.endswith(".html"):
                yield os.path.relpath(os.path.join(base, f), SITE)


def main():
    global FORBIDDEN, FORBIDDEN_AUTHORED
    terms = _load_terms()
    FORBIDDEN = terms
    FORBIDDEN_AUTHORED = terms
    ga = None
    if len(sys.argv) > 2 and sys.argv[1] == "--ga":
        ga = sys.argv[2]

    required = ["index.html", "llms.txt", "robots.txt", "sitemap.xml", "_headers",
                "api/meta.json", "api/regions.json", "api/complexes.json",
                "about/index.html", "privacy/index.html", "404.html", "style.css"]
    for path in required:
        if not os.path.exists(os.path.join(SITE, path)):
            err(f"필수 파일 없음: {path}")

    meta = json.loads(read("api/meta.json"))
    regions = json.loads(read("api/regions.json"))["regions"]
    complexes = json.loads(read("api/complexes.json"))["complexes"]
    if len(regions) != meta["counts"]["regions"]:
        err(f"지역 수 불일치: regions.json {len(regions)} vs meta {meta['counts']['regions']}")
    if len(complexes) != meta["counts"]["complexes"]:
        err(f"단지 수 불일치: complexes.json {len(complexes)} vs meta {meta['counts']['complexes']}")

    # 지역 페이지·JSON 존재
    for r in regions:
        for p in (f"region/{r['slug']}/index.html", f"api/region/{r['slug']}.json"):
            if not os.path.exists(os.path.join(SITE, p)):
                err(f"지역 산출물 없음: {p}")
    # 단지 페이지·JSON 존재
    missing = 0
    for c in complexes:
        if not os.path.exists(os.path.join(SITE, f"complex/{c['id']}/index.html")):
            missing += 1
        if not os.path.exists(os.path.join(SITE, f"api/complex/{c['id']}.json")):
            missing += 1
    if missing:
        err(f"단지 산출물 누락 {missing}건")

    # 값 정합성
    bad_values = 0
    for c in complexes:
        if c["min_score"] is not None and not (0 < c["min_score"] <= 84):
            bad_values += 1
    if bad_values:
        err(f"최저가점 범위 이상 {bad_values}건")
    checked = 0
    for name in os.listdir(os.path.join(SITE, "api", "complex")):
        data = json.loads(read(f"api/complex/{name}"))
        for u in data["units"]:
            checked += 1
            s = (u["score_min"], u["score_avg"], u["score_max"])
            if all(v is not None for v in s):
                if not (s[0] <= s[1] <= s[2]):
                    err(f"가점 순서 이상 {name} {u['model']}: {s}")
                if s[2] > 84:
                    err(f"가점 상한 초과 {name} {u['model']}: {s}")
            elif any(v is not None for v in s) and not all(v is not None for v in s):
                warn(f"가점 일부 누락 {name} {u['model']}: {s}")
    if checked == 0:
        err("단지 JSON에 주택형 데이터가 없음")

    # 표본 0 지역
    for r in regions:
        if r["units"] <= 0:
            err(f"표본 0 지역: {r['slug']}")

    # HTML 공통 요소
    html_files = list(walk_html())
    for path in html_files:
        text = read(path)
        if '<meta name="viewport"' not in text:
            err(f"viewport 없음: {path}")
        if 'rel="canonical"' not in text:
            err(f"canonical 없음: {path}")
        if ga and ga not in text:
            err(f"GA 태그 없음: {path}")
        if 'lang="ko"' not in text:
            err(f"lang=ko 없음: {path}")

    # JSON-LD 유효성
    for path in ["index.html", "about/index.html"]:
        text = read(path)
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
        if not blocks:
            err(f"JSON-LD 없음: {path}")
        for b in blocks:
            try:
                json.loads(b)
            except json.JSONDecodeError as exc:
                err(f"JSON-LD 깨짐 {path}: {exc}")

    # 금지 문구 (목록이 없으면 건너뜀)
    if not FORBIDDEN:
        print("참고: 금지 문구 목록이 없어 해당 검사는 건너뜁니다(config/forbidden_terms.txt)")
    for path in html_files + ["llms.txt", "llms-full.txt", "api/meta.json"]:
        text = read(path)
        for word in FORBIDDEN:
            if word in text:
                err(f"금지 문구 '{word}' 발견: {path}")
    for path in AUTHORED_FILES:
        text = read(path)
        for word in FORBIDDEN_AUTHORED:
            if word in text:
                err(f"금지 문구 '{word}' 발견: {path}")

    # sitemap ↔ 파일
    sm = read("sitemap.xml")
    locs = re.findall(r"<loc>(.*?)</loc>", sm)
    listed = {loc.split("/", 3)[-1] for loc in locs}
    missing_from_sitemap = [
        p for p in html_files
        if p != "404.html" and (p[: -len("index.html")] if p.endswith("index.html") else p) not in listed
    ]
    if missing_from_sitemap:
        err(f"sitemap 누락 {len(missing_from_sitemap)}건: {missing_from_sitemap[:5]}")
    for loc in locs:
        path = loc.split("/", 3)[-1]
        if path and not path.endswith("/"):
            target = path
        else:
            target = path + "index.html"
        if not os.path.exists(os.path.join(SITE, target)):
            err(f"sitemap 대상 없음: {loc}")

    # llms.txt 내부 링크
    llms = read("llms.txt")
    for m in re.finditer(r"https://[^\s/]+(/[^\s]*)", llms):
        path = m.group(1).strip(".,)")
        if path in ("/", ""):
            continue
        target = path.lstrip("/")
        if path.endswith("/"):
            target += "index.html"
        if not os.path.exists(os.path.join(SITE, target)):
            err(f"llms.txt 링크 대상 없음: {path}")
    if "커트라인" not in llms:
        err("llms.txt 내용 이상")

    # robots
    robots = read("robots.txt")
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"):
        if bot not in robots:
            err(f"robots.txt에 {bot} 허용 없음")

    print(f"검사: HTML {len(html_files):,} · 지역 {len(regions)} · 단지 {len(complexes):,} · 주택형 {checked:,}")
    if warns:
        print(f"경고 {len(warns)}건")
        for w in warns[:10]:
            print("  -", w)
    if errors:
        print(f"오류 {len(errors)}건")
        for err_msg in errors[:40]:
            print("  ✗", err_msg)
        return 1
    print("모든 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
