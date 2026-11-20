import json
import unittest
from pathlib import Path

from collection_conservation_evidence import process
from collection_conservation_evidence.storage import canonical_json

ROOT = Path(__file__).resolve().parent.parent


def facts_of(data):
    facts = process("light-dose-audit", data)
    return {fact["fact_id"]: fact for fact in facts}


BASE = {
    "object": "scroll-S",
    "window_start": "2025-12-15T09:00",
    "as_of": "2026-11-20T00:00",
    "query_lux": 50,
    "annual_quotas": {"2025": 25000, "2026": 200000},
    "locations": [
        {"id": "case-C", "kind": "display", "max_lux": 120},
        {"id": "vault-D", "kind": "dark"},
        {"id": "gallery-G", "kind": "display"},
    ],
    "probes": [
        {"id": "p1", "location": "case-C"},
        {"id": "p2", "location": "case-C"},
        {"id": "p3", "location": "vault-D"},
        {"id": "p4", "location": "gallery-G"},
    ],
    "handovers": [
        {"id": "h1", "from": "case-C", "to": "vault-D",
         "earliest": "2026-01-12T08:00", "latest": "2026-01-13T08:00"},
        {"id": "h2", "from": "vault-D", "to": "gallery-G",
         "earliest": "2026-03-02T09:00", "latest": "2026-03-03T09:00"},
    ],
    "readings": [
        {"id": "r1", "probe": "p1", "start": "2025-12-20T09:00",
         "end": "2025-12-24T17:00", "lux": 50},
        {"id": "r4", "probe": "p2", "start": "2025-12-22T09:00",
         "end": "2025-12-23T09:00", "lux": 46},
        {"id": "r2", "probe": "p1", "start": "2025-12-24T17:00",
         "end": "2026-01-12T08:00", "lux": 50},
        {"id": "r3", "probe": "p1", "start": "2026-01-05T09:00",
         "end": "2026-01-06T09:00", "lux": 40, "corrects": "r2"},
        {"id": "r5", "probe": "p3", "start": "2026-02-10T10:00",
         "end": "2026-02-10T12:00", "lux": 5},
        {"id": "r6", "probe": "p4", "start": "2026-03-03T09:00",
         "end": "2026-06-01T09:00", "lux": 30},
        {"id": "r7", "probe": "p4", "start": "2026-09-01T09:00",
         "end": "2026-11-10T09:00", "lux": 30},
    ],
}


class LightDoseAuditTests(unittest.TestCase):
    def test_example_matches_expected_snapshot(self):
        source = json.loads((ROOT / "examples" / "light-dose-audit.json").read_text("utf-8"))
        expected = json.loads((ROOT / "tests" / "expected" / "light-dose-audit.json").read_text("utf-8"))
        self.assertEqual(process("light-dose-audit", source), expected)

    def test_annual_bounds_and_year_split(self):
        facts = facts_of(BASE)
        years = facts["audit"]["years"]
        # 2025 测量 13854；空档上界 120h*120=14400。
        self.assertEqual(years["2025"]["lower_lux_hours"], 13854.0)
        self.assertEqual(years["2025"]["upper_lux_hours"], 28350.0)
        self.assertTrue(years["2025"]["upper_bound_supported"])
        # 展期跨年度：2025 年只承担 12-31 前发生的 r2 部分（175h*50=8750）。
        self.assertEqual(facts["seg:004"]["lower_lux_hours"], 8750.0)
        self.assertEqual(facts["seg:004"]["year"], 2025)
        self.assertEqual(facts["seg:005"]["year"], 2026)
        # 2026 含无依据上界的空档（gallery-G 无 max_lux）。
        self.assertIsNone(years["2026"]["upper_lux_hours"])
        self.assertFalse(years["2026"]["upper_bound_supported"])

    def test_overlapping_probes_take_min_max(self):
        facts = facts_of(BASE)
        overlap = facts["seg:002"]
        self.assertEqual(overlap["reading_ids"], ["r1", "r4"])
        self.assertEqual(overlap["lower_lux"], 46.0)
        self.assertEqual(overlap["upper_lux"], 50.0)

    def test_correction_replaces_original_within_actual_range(self):
        facts = facts_of(BASE)
        corrected = facts["seg:006"]
        self.assertEqual(corrected["reading_ids"], ["r3"])
        self.assertEqual(corrected["lower_lux_hours"], 960.0)  # 40*24
        # r2 在更正范围之外仍保留 50 lux。
        self.assertEqual(facts["seg:005"]["reading_ids"], ["r2"])
        self.assertEqual(facts["seg:005"]["lower_lux_hours"], 5250.0)

    def test_dark_stays_are_proven_zero_and_separated_from_gaps(self):
        facts = facts_of(BASE)
        audit = facts["audit"]
        self.assertEqual([d["id"] for d in audit["dark_stays"]], ["dark-001", "dark-002"])
        for stay in audit["dark_stays"]:
            self.assertEqual(stay["lower_lux_hours"], 0.0)
            self.assertEqual(stay["upper_lux_hours"], 0.0)
        self.assertEqual(sum(d["hours"] for d in audit["dark_stays"]), 1151.0)
        gap_ids = {g["id"] for g in audit["unmeasured_gaps"]}
        self.assertEqual(gap_ids, {"gap-001", "gap-002", "gap-003", "gap-004", "gap-005"})
        # 暗库巡查读数时段是 measured，不被并入 dark stay。
        self.assertEqual(facts["seg:010"]["category"], "measured")

    def test_handover_window_belongs_to_one_window_with_bounded_and_unbounded_cases(self):
        facts = facts_of(BASE)
        audit = facts["audit"]
        h1, h2 = audit["handovers"]
        # h1: case-C(120)/vault-D(0)，下界 0，上界 24h*120。
        self.assertEqual(h1["lower_lux_hours"], 0.0)
        self.assertEqual(h1["upper_lux_hours"], 2880.0)
        self.assertTrue(h1["upper_bound_supported"])
        # h2: vault-D(0)/gallery-G(无上限)，上界不可支持。
        self.assertIsNone(h2["upper_lux_hours"])
        self.assertFalse(h2["upper_bound_supported"])
        # 每个交接窗口内的原子段只归属该窗口，不重复计入位置。
        h1_segs = [f for f in facts.values()
                   if f.get("handover_id") == "h1" and f["fact_id"].startswith("seg:")]
        self.assertEqual(sum(s["hours"] for s in h1_segs), 24.0)

    def test_remaining_display_hours_and_warning_responsibility(self):
        facts = facts_of(BASE)
        remaining = {r["year"]: r for r in facts["audit"]["remaining_display"]}
        # 2025 上界 28350 > 额度 25000：保证剩余 0；下界剩余 (25000-13854)/50。
        self.assertEqual(remaining[2025]["guaranteed_remaining_hours"], 0.0)
        self.assertEqual(remaining[2025]["best_effort_remaining_hours"], 222.92)
        # 2026 上界不可支持：保证剩余为 null，仅给 best-effort。
        self.assertIsNone(remaining[2026]["guaranteed_remaining_hours"])
        self.assertEqual(remaining[2026]["best_effort_remaining_hours"], 1428.6)
        warnings = {(w["code"], w["year"]): w for w in facts["audit"]["warnings"]}
        self.assertEqual(warnings[("possible_exhaustion", 2025)]["responsible"], ["seg:000"])
        self.assertEqual(
            warnings[("upper_bound_unsupported", 2026)]["responsible"],
            ["seg:012", "seg:014", "seg:016"],
        )

    def test_confirmed_over_limit_warning(self):
        data = json.loads(json.dumps(BASE))
        data["annual_quotas"]["2025"] = 100
        facts = facts_of(data)
        codes = {(w["code"], w["year"]) for w in facts["audit"]["warnings"]}
        self.assertIn(("confirmed_over_limit", 2025), codes)

    def test_measurement_inside_handover_window_is_used(self):
        data = json.loads(json.dumps(BASE))
        data["readings"].append({
            "id": "r8", "probe": "p1", "start": "2026-01-12T12:00",
            "end": "2026-01-12T18:00", "lux": 55,
        })
        facts = facts_of(data)
        inside = [f for f in facts.values()
                  if f.get("handover_id") == "h1" and f["fact_id"].startswith("seg:")]
        measured = [f for f in inside if f["category"] == "measured"]
        self.assertTrue(measured)
        self.assertEqual({rid for f in measured for rid in f["reading_ids"]}, {"r8"})
        audit = facts["audit"]
        h1 = next(h for h in audit["handovers"] if h["id"] == "h1")
        self.assertEqual(h1["measured_hours"], 6.0)
        # 有测量的 6h 上界 55*6=330，其余 18h 上界 120*18=2160。
        self.assertEqual(h1["upper_lux_hours"], 2490.0)

    def test_probe_reading_outside_its_location_is_ignored(self):
        data = json.loads(json.dumps(BASE))
        # p4 属于 gallery-G，其读数时间落在藏品仍在 vault-D 的时段内，不得计入。
        data["readings"].append({
            "id": "r9", "probe": "p4", "start": "2026-02-01T00:00",
            "end": "2026-02-02T00:00", "lux": 80,
        })
        facts = facts_of(data)
        audit = facts["audit"]
        vault_segments = [
            f for f in facts.values()
            if f["fact_id"].startswith("seg:") and f["location"] == "vault-D"
        ]
        self.assertTrue(all(f["reading_ids"] == [] or "r9" not in f["reading_ids"]
                            for f in vault_segments))
        # r9 仍作为有效测量范围保留，可追溯未采用原因。
        self.assertIn(
            "r9", [s["reading_id"] for s in audit["measurement_scopes"]]
        )

    def test_fully_superseded_reading_keeps_traceable_scope(self):
        data = json.loads(json.dumps(BASE))
        data["readings"].append({
            "id": "r10", "probe": "p1", "start": "2025-12-20T09:00",
            "end": "2025-12-24T17:00", "lux": 51, "corrects": "r1",
        })
        facts = facts_of(data)
        scopes = {s["reading_id"]: s for s in facts["audit"]["measurement_scopes"]}
        self.assertTrue(scopes["r1"]["superseded"])
        self.assertEqual(scopes["r1"]["ranges"], [])
        self.assertFalse(scopes["r10"]["superseded"])
        # 原 r1 时段全部由 r10 的 51 lux 承担。
        seg = facts["seg:001"]
        self.assertEqual(seg["reading_ids"], ["r10"])
        self.assertEqual(seg["lower_lux_hours"], 51 * 48)

    def test_cross_year_exhibition_splits_actual_dose_per_year(self):
        facts = facts_of(BASE)
        # r2 展期（2025-12-24 至 2026-01-12）跨过年度边界：
        # 2025 部分 175h、2026 更正前部分 105h，各自计入对应年度。
        y2025 = sum(f["lower_lux_hours"] for k, f in facts.items()
                    if k.startswith("seg:") and f["year"] == 2025)
        y2026 = sum(f["lower_lux_hours"] for k, f in facts.items()
                    if k.startswith("seg:") and f["year"] == 2026
                    and f["category"] == "measured")
        self.assertEqual(y2025, 13854.0)
        self.assertEqual(y2026, 128570.0)

    def test_reject_invalid_handover_and_quota_inputs(self):
        bad_window = json.loads(json.dumps(BASE))
        bad_window["as_of"] = "2025-01-01T00:00"
        with self.assertRaises(ValueError):
            process("light-dose-audit", bad_window)
        bad_chain = json.loads(json.dumps(BASE))
        bad_chain["handovers"][1]["from"] = "case-C"
        with self.assertRaises(ValueError):
            process("light-dose-audit", bad_chain)
        bad_correction = json.loads(json.dumps(BASE))
        bad_correction["readings"][3]["corrects"] = "r1"  # r3 与 r1 无重叠
        with self.assertRaises(ValueError):
            process("light-dose-audit", bad_correction)
        bad_quota = json.loads(json.dumps(BASE))
        bad_quota["annual_quotas"] = {"25": 1000}
        with self.assertRaises(ValueError):
            process("light-dose-audit", bad_quota)


if __name__ == "__main__":
    unittest.main()
