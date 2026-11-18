#!/usr/bin/env python3
"""파이프라인 단위 테스트 (python3 -m unittest discover tests)"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline  # noqa: E402


class TestRegionParse(unittest.TestCase):
    def test_sido(self):
        self.assertEqual(pipeline.sido_of("경기", "경기도 성남시 분당구 정자동"), "경기")
        self.assertEqual(pipeline.sido_of(None, "서울특별시 강남구 역삼동"), "서울")
        self.assertEqual(pipeline.sido_of(None, "부산광역시 해운대구 우동"), "부산")
        self.assertEqual(pipeline.sido_of(None, "김포 풍무역세권 C5블록 (경기도 김포시 사우동)"), "경기")

    def test_sgg_basic(self):
        self.assertEqual(pipeline.sgg_from_address("서울특별시 강남구 역삼동 1"), "강남구")
        self.assertEqual(pipeline.sgg_from_address("경기도 수원시 장안구 정자동 111"), "수원시")
        self.assertEqual(pipeline.sgg_from_address("대구광역시 수성구 범어동 100"), "수성구")
        self.assertEqual(pipeline.sgg_from_address("제주특별자치도 서귀포시 동홍동"), "서귀포시")

    def test_sgg_edge(self):
        # 시·구가 붙은 표기는 시 단위로 합치고, 지구·신도시 이름은 지역으로 쓰지 않는다
        self.assertEqual(pipeline.sgg_from_address("경기도 성남시 수정구 고등동"), "성남시")
        self.assertEqual(pipeline.sgg_from_address("경기도 화성특례시 동탄2 택지개발지구", sido="경기"), "화성시")
        self.assertEqual(pipeline.sgg_from_address("서울시 강동구 상일동 28", sido="서울"), "강동구")
        self.assertIsNone(pipeline.sgg_from_address("경기도 어딘가 공공주택지구 A1BL", sido="경기"))

    def test_area_bucket(self):
        self.assertEqual(pipeline.area_bucket(59.9), "60㎡ 이하")
        self.assertEqual(pipeline.area_bucket(60.0), "60㎡ 이하")
        self.assertEqual(pipeline.area_bucket(84.9), "60~85㎡")
        self.assertEqual(pipeline.area_bucket(85.0), "60~85㎡")
        self.assertEqual(pipeline.area_bucket(99.9), "85~102㎡")
        self.assertEqual(pipeline.area_bucket(134.0), "102㎡ 초과")

    def test_unit_spec(self):
        self.assertAlmostEqual(pipeline.parse_unit_spec("084.9079A"), 84.9079)
        self.assertAlmostEqual(pipeline.parse_unit_spec("059.9667"), 59.9667)
        self.assertIsNone(pipeline.parse_unit_spec(""))


class TestScoreNormalize(unittest.TestCase):
    def test_zero_and_missing_dropped(self):
        rows = [
            {"공고번호": "1", "모델번호": "01", "거주지역": "해당지역", "최저당첨가점": "59", "최고당첨가점": "63", "평균당첨가점": "60.75"},
            {"공고번호": "1", "모델번호": "01", "거주지역": "기타지역", "최저당첨가점": "0", "최고당첨가점": "0", "평균당첨가점": "0"},
            {"공고번호": "1", "모델번호": "02", "거주지역": "해당지역", "최저당첨가점": "", "최고당첨가점": "", "평균당첨가점": ""},
        ]
        scores = pipeline.norm_scores(rows)
        self.assertIn("해당지역", scores[("1", "01")])
        self.assertNotIn("기타지역", scores[("1", "01")])
        self.assertNotIn(("1", "02"), scores)
        self.assertEqual(scores[("1", "01")]["해당지역"]["min"], 59.0)

    def test_rate_zero_dropped(self):
        rows = [
            {"공고번호": "2", "모델번호": "01", "순위": "1", "거주지역": "해당지역", "접수건수": "100", "경쟁률": "10.5"},
            {"공고번호": "2", "모델번호": "01", "순위": "2", "거주지역": "해당지역", "접수건수": "0", "경쟁률": "0"},
        ]
        rates = pipeline.norm_rates(rows)
        self.assertEqual(rates[("2", "01")]["1"]["해당지역"]["rate"], 10.5)
        self.assertNotIn("2", rates[("2", "01")])


class TestSummarize(unittest.TestCase):
    def rows(self):
        base = {"complex_id": "c", "complex": "x", "sido": "대구", "sgg": "수성구", "model": "01",
                "area": 84.9, "bucket": "60~85㎡", "notice_date": "2025-05-01"}
        return [
            dict(base, score_min=50.0, score_avg=55.0, score_max=60.0, rate_primary=10.0),
            dict(base, score_min=54.0, score_avg=58.0, score_max=62.0, rate_primary=20.0),
            dict(base, score_min=None, score_avg=None, score_max=None, rate_primary=5.0),
        ]

    def test_values(self):
        s = pipeline.summarize(self.rows())
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["min_score_avg"], 52.0)
        self.assertEqual(s["min_score_median"], 52.0)
        self.assertEqual(s["min_score_min"], 50.0)
        self.assertEqual(s["rate_median"], 10.0)

    def test_cutoff(self):
        s = pipeline.summarize(self.rows(), "2026-01-01")
        self.assertIsNone(s)


class TestBuildPrice(unittest.TestCase):
    def test_e2e_small(self):
        """가짜 원본으로 단지 → 지역 집계가 끝까지 도는지 확인한다."""
        info = [{"공고번호": "2025000001", "주택명": "테스트단지", "공급지역명": "대구",
                 "공급위치": "대구광역시 수성구 범어동 1", "모집공고일": "2025-06-01", "주택구분코드명": "APT",
                 "주택상세구분코드명": "민영", "공급규모": "100", "건설업체명_시공사": "", "사업주체명_시행사": "",
                 "입주예정월": "2028-01"}]
        units = [{"공고번호": "2025000001", "모델번호": "01", "주택형": "084.9000A", "주택공급면적": "112.0",
                  "일반공급세대수": "80", "특별공급세대수": "20", "공급금액_분양최고금액": "50000"}]
        scores = [{"공고번호": "2025000001", "모델번호": "01", "거주지역": "해당지역",
                   "최저당첨가점": "58", "최고당첨가점": "66", "평균당첨가점": "61.5"}]
        rates = [{"공고번호": "2025000001", "모델번호": "01", "순위": "1", "거주지역": "해당지역",
                  "접수건수": "1234", "경쟁률": "12.34"}]
        comps = pipeline.assign_regions(pipeline.norm_complexes(info, {}))
        rows = pipeline.build_units_table(comps, pipeline.norm_units(units),
                                          pipeline.norm_scores(scores), pipeline.norm_rates(rates))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["sgg"], "수성구")
        self.assertEqual(row["bucket"], "60~85㎡")
        self.assertEqual(row["score_min"], 58.0)
        self.assertAlmostEqual(row["rate_primary"], 12.34)
        regions = pipeline.build_region_index(rows)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["areas"][0]["bucket"], "60~85㎡")


if __name__ == "__main__":
    unittest.main()
