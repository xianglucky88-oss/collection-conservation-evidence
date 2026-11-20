import json
from pathlib import Path
import unittest

from collection_conservation_evidence import PIPELINES, process

ROOT = Path(__file__).resolve().parent.parent
NAME = "material-effects"


def load(name: str):
    return json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))


def by_id(facts):
    return {fact["fact_id"]: fact for fact in facts}


def small_trial(containers, coupons, **extra):
    return {"max_grade": 1, "containers": containers, "coupons": coupons, **extra}


class MaterialEffectsTests(unittest.TestCase):
    def test_example_matches_expected(self):
        expected = json.loads(
            (ROOT / "tests" / "expected" / f"{NAME}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(process(NAME, load(NAME)), expected)

    def test_pipeline_is_registered(self):
        self.assertIn(NAME, PIPELINES)

    def test_same_container_coupons_are_one_evidence_unit(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "c2", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "t1", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
                {"id": "t2", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
                {"id": "t3", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 3},
            ],
        )
        facts = by_id(process(NAME, data))
        control = facts["baseline|control|P|silver"]
        combination = facts["baseline|combination|P|silver|liner"]
        self.assertEqual(control["container_count"], 1)
        self.assertEqual(control["coupon_count"], 2)
        self.assertEqual(combination["container_count"], 1)
        self.assertEqual(combination["coupon_count"], 3)
        # Container median is the median of its coupons, not a pooled coupon count.
        self.assertEqual(combination["treatment_median_change"], 1)
        self.assertEqual(combination["direction"], "increase")

    def test_preexisting_blemish_is_excluded_from_change(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "t1", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 3, "pre_existing_blemish": True},
                {"id": "t2", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
            ],
        )
        facts = by_id(process(NAME, data))
        combination = facts["baseline|combination|P|silver|liner"]
        self.assertEqual(combination["valid_record_count"], 1)
        self.assertEqual(combination["comparable_above_visual_limit_count"], 0)
        excluded = combination["excluded_coupons"]
        self.assertEqual([row["coupon"] for row in excluded], ["t1"])
        self.assertEqual(excluded[0]["reason"], "pre_existing_blemish")

    def test_missing_pre_grade_blocks_direction_until_amended(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "t1", "container": "T", "metal": "silver",
                 "post_grade": 2},
            ],
            amendments=[
                {"id": "A1", "kind": "add_pre_grade", "coupon": "t1", "pre_grade": 0}
            ],
        )
        facts = by_id(process(NAME, data))
        self.assertEqual(
            facts["baseline|combination|P|silver|liner"]["status"], "insufficient_records"
        )
        self.assertEqual(
            facts["baseline|material|P|silver|liner|liner"]["status"],
            "pending_insufficient_records",
        )
        self.assertEqual(
            facts["A1|combination|P|silver|liner"]["direction"], "increase"
        )
        impact = facts["A1|impact|combination|P|silver|liner"]
        self.assertEqual(impact["before"]["direction"], "undetermined")
        self.assertEqual(impact["after"]["direction"], "increase")
        self.assertEqual(impact["scenario_kind"], "add_pre_grade")

    def test_invalid_control_removes_direction_and_requests_replacement(self):
        data = small_trial(
            [
                {"id": "C1", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "C2", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c11", "container": "C1", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "c21", "container": "C2", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
                {"id": "t1", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 2},
            ],
            amendments=[
                {"id": "A1", "kind": "invalidate_control", "container": "C2",
                 "note": "shared exposure with the treated stack"}
            ],
        )
        facts = by_id(process(NAME, data))
        baseline_control = facts["baseline|control|P|silver"]
        self.assertEqual(baseline_control["median_change"], 0.5)
        amended_control = facts["A1|control|P|silver"]
        self.assertEqual(amended_control["container_ids"], ["C1"])
        self.assertEqual(amended_control["invalidated_container_ids"], ["C2"])
        self.assertEqual(amended_control["median_change"], 0)
        # Direction is unchanged, so no conclusion-change fact is emitted,
        # but the amended control summary carries the new baseline.
        self.assertNotIn("A1|impact|combination|P|silver|liner", facts)
        self.assertEqual(
            facts["A1|combination|P|silver|liner"]["relative_change"], 2
        )
        recommendation = facts["A1|recommendation|01"]
        self.assertEqual(recommendation["reason"], "single_blank_control")

    def test_no_valid_control_keeps_material_effect_pending(self):
        data = small_trial(
            [
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "t1", "container": "T", "metal": "silver",
                 "pre_grade": 0, "post_grade": 2},
            ],
        )
        facts = by_id(process(NAME, data))
        combination = facts["baseline|combination|P|silver|liner"]
        self.assertEqual(combination["status"], "no_valid_control")
        self.assertEqual(combination["direction"], "undetermined")
        material = facts["baseline|material|P|silver|liner|liner"]
        self.assertEqual(material["status"], "pending_no_valid_control")
        self.assertEqual(material["direction"], "undetermined")
        self.assertEqual(facts["baseline|recommendation|01"]["reason"],
                         "missing_blank_control")

    def test_combination_effect_does_not_imply_single_material_effect(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "L", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
                {"id": "LA", "role": "treatment", "materials": ["liner", "adhesive"],
                 "exposure_period": "P"},
                {"id": "A", "role": "treatment", "materials": ["adhesive"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "l1", "container": "L", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
                {"id": "la1", "container": "LA", "metal": "silver",
                 "pre_grade": 0, "post_grade": 2},
                {"id": "a1", "container": "A", "metal": "silver",
                 "pre_grade": 0, "post_grade": 1},
            ],
        )
        facts = by_id(process(NAME, data))
        self.assertEqual(facts["baseline|combination|P|silver|adhesive+liner"]["direction"],
                         "increase")
        adhesive = facts["baseline|material|P|silver|adhesive+liner|adhesive"]
        liner = facts["baseline|material|P|silver|adhesive+liner|liner"]
        self.assertEqual((adhesive["status"], adhesive["direction"]),
                         ("evaluated", "increase"))
        self.assertEqual((liner["status"], liner["direction"]),
                         ("evaluated", "increase"))
        self.assertEqual(adhesive["comparison_combination"], ["liner"])

    def test_rejects_invalid_grouping(self):
        control_with_material = small_trial(
            [{"id": "C", "role": "control", "materials": ["liner"],
              "exposure_period": "P"}],
            [{"id": "c1", "container": "C", "metal": "silver",
              "pre_grade": 0, "post_grade": 0}],
        )
        with self.assertRaises(ValueError):
            process(NAME, control_with_material)

        unknown_container = small_trial(
            [{"id": "C", "role": "control", "materials": [],
              "exposure_period": "P"}],
            [{"id": "x", "container": "missing", "metal": "silver",
              "pre_grade": 0, "post_grade": 0}],
        )
        with self.assertRaises(ValueError):
            process(NAME, unknown_container)

        empty_container = small_trial(
            [{"id": "C", "role": "control", "materials": [],
              "exposure_period": "P"}],
            [],
        )
        with self.assertRaises(ValueError):
            process(NAME, empty_container)

    def test_rejects_amendment_that_repeats_existing_record(self):
        data = small_trial(
            [{"id": "C", "role": "control", "materials": [],
              "exposure_period": "P"}],
            [{"id": "c1", "container": "C", "metal": "silver",
              "pre_grade": 0, "post_grade": 0}],
            amendments=[{"id": "A1", "kind": "add_pre_grade", "coupon": "c1",
                         "pre_grade": 1}],
        )
        with self.assertRaises(ValueError):
            process(NAME, data)

    def test_material_effect_pending_when_matched_combination_has_no_comparable_record(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "L", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
                {"id": "LA", "role": "treatment", "materials": ["adhesive", "liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "l1", "container": "L", "metal": "silver",
                 "pre_grade": 0, "post_grade": 3, "pre_existing_blemish": True},
                {"id": "la1", "container": "LA", "metal": "silver",
                 "pre_grade": 0, "post_grade": 2},
            ],
        )
        facts = by_id(process(NAME, data))
        # The liner-only combination exists but has no comparable coupon.
        self.assertEqual(facts["baseline|combination|P|silver|liner"]["status"],
                         "insufficient_records")
        adhesive = facts["baseline|material|P|silver|adhesive+liner|adhesive"]
        self.assertEqual(adhesive["status"],
                         "pending_matched_combination_insufficient_records")
        recommendations = [
            fact for fact in facts.values() if fact["scope"] == "recommendation"
        ]
        matched = [
            fact for fact in recommendations
            if fact["reason"] == "insufficient_matched_combination_records"
        ]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["materials"], ["liner"])

    def test_amendment_impact_records_change_type(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
                {"id": "T", "role": "treatment", "materials": ["liner"],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
                {"id": "t1", "container": "T", "metal": "silver",
                 "post_grade": 2},
            ],
            amendments=[
                {"id": "A1", "kind": "add_pre_grade", "coupon": "t1", "pre_grade": 0}
            ],
        )
        facts = by_id(process(NAME, data))
        impact = facts["A1|impact|combination|P|silver|liner"]
        self.assertEqual(impact["change"], "status_and_direction")
        self.assertEqual(impact["before"]["status"], "insufficient_records")
        self.assertEqual(impact["after"]["status"], "evaluated")

    def test_control_without_treatment_gets_no_recommendation(self):
        data = small_trial(
            [
                {"id": "C", "role": "control", "materials": [],
                 "exposure_period": "P"},
            ],
            [
                {"id": "c1", "container": "C", "metal": "silver",
                 "pre_grade": 0, "post_grade": 0},
            ],
        )
        facts = process(NAME, data)
        self.assertEqual([f for f in facts if f["scope"] == "recommendation"], [])
