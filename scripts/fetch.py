#!/usr/bin/env python3
"""공공데이터포털(한국부동산원 청약홈) 파일데이터 4종을 내려받는다.

인증키 없이 내려받는 경로(파일데이터 다운로드)만 사용하므로 무과금·무계정으로 동작한다.
실패하면 기존 스냅샷(data/raw)을 그대로 두고 nonzero로 끝난다(빌드는 스냅샷으로 계속 가능).
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

DATASETS = {
    "info": {"pk": "15101046", "name": "한국부동산원_청약홈_APT 분양정보"},
    "unit": {"pk": "15101047", "name": "한국부동산원_청약홈_APT 주택형별 분양정보"},
    "rate": {"pk": "15101048", "name": "한국부동산원_청약홈_APT 경쟁률"},
    "score": {"pk": "15126242", "name": "한국부동산원_청약홈_APT 경쟁률 가점정보"},
}


def http(url, data=None, referer=None, timeout=90):
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    if data is not None:
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=headers)
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download_args(pk):
    """데이터셋 상세 페이지에서 다운로드 호출 인자(uddi, 이력번호)를 읽는다."""
    html = http(f"https://www.data.go.kr/data/{pk}/fileData.do").decode("utf-8", "replace")
    m = re.search(r"fn_fileDataDown\('([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)'\)", html)
    if not m:
        raise RuntimeError(f"{pk}: 다운로드 버튼 인자를 찾지 못함")
    return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)


def fetch_one(pk):
    pub, uddi, atch, sn, hist = download_args(pk)
    page = f"https://www.data.go.kr/data/{pk}/fileData.do"
    body = json.loads(
        http(
            "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do",
            {
                "publicDataPk": pub,
                "publicDataDetailPk": uddi,
                "atchFileId": atch,
                "fileDetailSn": sn,
                "publicDataTyCode": "PR0051",
                "publicDataHistSn": hist,
            },
            referer=page,
        ).decode("utf-8", "replace")
    )
    if not body.get("status"):
        raise RuntimeError(f"{pk}: 파일 메타 조회 실패 {body.get('error')}")
    info = body["dataSetFileDetailInfo"]
    url = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=%s&fileDetailSn=%s&dataNm=%s" % (
        body["atchFileId"],
        body["fileDetailSn"],
        urllib.parse.quote(info.get("dataNm") or pk),
    )
    blob = http(url, referer=page)
    if len(blob) < 10_000 or b"<html" in blob[:2000].lower():
        raise RuntimeError(f"{pk}: 내려받은 파일이 데이터가 아님 ({len(blob)} bytes)")
    return blob, info


def main():
    os.makedirs(RAW, exist_ok=True)
    meta_path = os.path.join(RAW, "sources.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    failures = []
    for key, ds in DATASETS.items():
        target = os.path.join(RAW, f"{key}.csv")
        try:
            blob, info = fetch_one(ds["pk"])
        except Exception as exc:  # noqa: BLE001 - 네트워크/차단은 어떤 형태로든 난다
            print(f"[warn] {key} ({ds['pk']}) 내려받기 실패: {exc}", file=sys.stderr)
            failures.append(key)
            continue
        tmp = target + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, target)
        meta[key] = {
            "pk": ds["pk"],
            "name": ds["name"],
            "file": info.get("orginlFileNm") or f"{key}.csv",
            "records": info.get("atchFileCo"),
            "dataset_updated": str(info.get("updtDt") or "")[:10],
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
        print(f"[ok] {key} <- {meta[key]['file']} ({len(blob):,} bytes)")
        time.sleep(2)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)

    if failures:
        print(f"[warn] 실패한 데이터셋: {', '.join(failures)} — 기존 스냅샷으로 빌드합니다", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
