#!/usr/bin/env python3
"""포털 분양정보에 아직 없는(최근) 공고의 단지명·소재지를 청약홈 상세페이지에서 읽어 캐시한다.

- 대상: 경쟁률/가점 데이터에는 있으나 분양정보 파일에 없는 공고 (보통 최신 회차)
- 결과: data/cache/detail_meta.json  {공고번호: {name, address, notice_date, scale, fetched_at}}
- 이미 캐시된 공고는 다시 요청하지 않는다. 실패한 공고는 캐시에 남기지 않고 다음 실행에서 재시도.
"""
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pipeline  # noqa: E402

CACHE = os.path.join(ROOT, "data", "cache", "detail_meta.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
DELAY = 0.6


def fetch_detail(gid):
    url = ("https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancDetail.do"
           f"?houseManageNo={gid}&pblancNo={gid}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        blob = r.read()
    try:
        html = blob.decode("cp949")
    except UnicodeDecodeError:
        html = blob.decode("utf-8", "replace")
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    m = re.search(r"주요정보\s+(.+?)\s+공급위치\s+(.+?)\s+공급규모\s+([\d,]+)\s*세대", text)
    name = address = scale = ""
    if m:
        name, address, scale = (x.strip() for x in m.groups())
    else:
        mi = re.search(r"공급위치\s+(.+?)\s+공급규모\s+([\d,]+)\s*세대", text)
        if mi:
            address, scale = mi.group(1).strip(), mi.group(2).strip()
            head = text.split("공급위치")[0]
            name = head.split("주요정보")[-1].strip() if "주요정보" in head else ""
    md = re.search(r"모집공고일\s*(\d{4}-\d{2}-\d{2})", text)
    notice = md.group(1) if md else ""

    if not (name or address):
        raise RuntimeError("상세페이지에서 단지 정보를 찾지 못함")
    name = re.sub(r"^(입주자모집공고\s*)?(주요정보\s*)+", "", name).strip()
    return {
        "name": re.sub(r"\s+", " ", name)[:120],
        "address": re.sub(r"\s+", " ", address)[:200],
        "notice_date": notice,
        "scale": scale.replace(",", "") if scale else "",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main():
    needed = set()
    for key in ("rate", "score"):
        for row in pipeline.read_csv(key):
            gid = (row.get("공고번호") or "").strip()
            if gid:
                needed.add(gid)
    have = {(r.get("공고번호") or "").strip() for r in pipeline.read_csv("info")}
    cache = pipeline.load_detail_cache()
    todo = sorted(needed - have - set(cache), reverse=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--limit":
        todo = todo[: int(sys.argv[2])]
    print(f"대상 공고 {len(todo)}건 (데이터 있음 {len(needed)} / 포털 수록 {len(have)} / 캐시 {len(cache)})")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)

    ok = fail = 0
    for i, gid in enumerate(todo, 1):
        try:
            meta = fetch_detail(gid)
            cache[gid] = meta
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"[warn] {gid}: {exc}", file=sys.stderr)
        if i % 25 == 0:
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
            print(f"  … {i}/{len(todo)} (ok {ok}, fail {fail})")
        time.sleep(DELAY)

    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"완료: 성공 {ok}, 실패 {fail}, 캐시 {len(cache)}건")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
