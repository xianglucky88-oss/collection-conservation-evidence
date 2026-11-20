import json
from pathlib import Path
import unittest

from collection_conservation_evidence import process

ROOT = Path(__file__).resolve().parent.parent


def round_fact(facts):
    return next(fact for fact in facts if fact["fact_type"] == "round")


def position(facts, name):
    return next(item for item in round_fact(facts)["positions"] if item["position"] == name)


def base(**overrides):
    data = {
        "lower_rh_pct": 45,
        "upper_rh_pct": 55,
        "required_hold_minutes": 10,
        "max_observation_gap_minutes": 6,
        "required_positions": ["shelf-top", "shelf-bottom"],
        "records": [],
    }
    data.update(overrides)
    return data


def observation(identifier, position_name, rh, minutes):
    return {
        "id": identifier,
        "position": position_name,
        "rh_pct": rh,
        "minutes_after_close": minutes,
    }


def series(position_name, values):
    return [
        observation(f"{position_name}-{minutes}", position_name, rh, minutes)
        for rh, minutes in values
    ]


class HumidityResponseTests(unittest.TestCase):
    def test_transient_in_band_reading_is_not_a_recovery(self):
        observations = series(
            "shelf-top", [(50, 0), (60, 6), (51, 12), (52, 18), (50, 24), (49, 30)]
        )
        observations += series(
            "shelf-bottom", [(65, 0), (60, 6), (58, 12), (56, 18), (54, 24), (52, 30), (51, 36)]
        )
        facts = process("humidity-response", base(records=[{
            "id": "r", "case": "c", "layout": "L", "observations": observations,
        }]))

        top = position(facts, "shelf-top")
        self.assertEqual(top["first_in_band_minutes"], 0.0)
        self.assertTrue(top["first_in_band_was_transient"])
        self.assertEqual(top["transient_in_band"][0]["entered_minutes"], 0.0)
        self.assertEqual(top["recovery_minutes"], 12.0)
        self.assertEqual(top["recovery_confirmed_by_minutes"], 24.0)
        self.assertLess(top["recovery_lower_bound_minutes"], top["recovery_minutes"])

    def test_observation_gap_prevents_confirmation_and_keeps_bounds(self):
        observations = series("shelf-top", [(50, 0), (50, 6), (50, 12), (50, 18)])
        observations += series("shelf-bottom", [(60, 0), (60, 6)])
        observations.append(observation("bottom-30", "shelf-bottom", 60, 30))
        facts = process("humidity-response", base(records=[{
            "id": "r", "case": "c", "layout": "L", "observations": observations,
        }]))

        bottom = position(facts, "shelf-bottom")
        self.assertEqual(bottom["status"], "unconfirmed")
        self.assertEqual(bottom["finding"], "never_in_band")
        self.assertEqual(bottom["recovery_lower_bound_minutes"], 30.0)
        self.assertIsNone(bottom["unconfirmed_in_band_since_minutes"])
        self.assertEqual(bottom["observation_gaps"][0]["gap_minutes"], 24.0)

        top = position(facts, "shelf-top")
        self.assertEqual(top["recovery_minutes"], 0.0)

    def test_gap_severed_in_band_run_cannot_be_stitched(self):
        observations = series("shelf-top", [(50, 0), (50, 6)])
        observations.append(observation("top-24", "shelf-top", 50, 24))
        observations += series("shelf-bottom", [(50, 0), (50, 6), (50, 12), (50, 18)])
        facts = process("humidity-response", base(records=[{
            "id": "r", "case": "c", "layout": "L", "observations": observations,
        }]))

        top = position(facts, "shelf-top")
        self.assertEqual(top["status"], "unconfirmed")
        self.assertEqual(top["finding"], "hold_not_met_at_end_of_monitoring")
        self.assertEqual(top["unconfirmed_in_band_since_minutes"], 24.0)
        self.assertEqual(len(top["severed_in_band_runs"]), 1)
        self.assertEqual(top["severed_in_band_runs"][0]["entered_minutes"], 0.0)
        self.assertEqual(top["severed_in_band_runs"][0]["next_observation_minutes"], 24.0)
        self.assertEqual(round_fact(facts)["round_status"], "incomplete")

    def test_reopening_excludes_later_readings_and_separates_conclusions(self):
        done = series("shelf-top", [(50, 0), (50, 6), (50, 12), (50, 18)])
        done += series("shelf-bottom", [(50, 0), (50, 6), (50, 12), (50, 18)])
        partial = series("shelf-top", [(50, 0), (50, 6), (50, 12), (50, 18)])
        partial += series("shelf-bottom", [(60, 0), (60, 6)])
        partial.append(observation("after-reopen", "shelf-top", 50, 50))
        facts = process("humidity-response", base(records=[
            {"id": "done", "case": "c", "layout": "L", "observations": done},
            {"id": "partial", "case": "c", "layout": "L", "reopened_at_minutes": 20,
             "observations": partial},
        ]))

        by_id = {fact["fact_id"]: fact for fact in facts if fact["fact_type"] == "round"}
        self.assertEqual(by_id["done"]["round_conclusion"], "complete")
        self.assertIsNone(by_id["done"]["reopened_at_minutes"])
        self.assertEqual(by_id["partial"]["round_conclusion"], "incomplete_at_reopening")
        self.assertEqual(by_id["partial"]["excluded_observation_ids"], ["after-reopen"])
        self.assertEqual(by_id["partial"]["monitored_until_minutes"], 20.0)
        self.assertEqual(by_id["partial"]["recovery_minutes"], None)
        self.assertEqual(by_id["partial"]["confirmed_required_position_count"], 1)

    def test_completed_round_before_reopening_is_kept(self):
        observations = series("shelf-top", [(50, 0), (50, 6), (50, 12), (50, 18)])
        observations += series("shelf-bottom", [(50, 0), (50, 6), (50, 12), (50, 18)])
        observations.append(observation("late", "shelf-top", 60, 30))
        facts = process("humidity-response", base(records=[{
            "id": "r", "case": "c", "layout": "L", "reopened_at_minutes": 24,
            "observations": observations,
        }]))

        fact = round_fact(facts)
        self.assertEqual(fact["round_conclusion"], "completed_before_reopening")
        self.assertEqual(fact["recovery_minutes"], 0.0)
        self.assertEqual(fact["excluded_observation_ids"], ["late"])

    def test_unmonitored_required_position_blocks_the_round(self):
        observations = series("shelf-top", [(50, 0), (50, 6), (50, 12), (50, 18)])
        facts = process("humidity-response", base(records=[{
            "id": "r", "case": "c", "layout": "L", "observations": observations,
        }]))

        bottom = position(facts, "shelf-bottom")
        self.assertEqual(bottom["status"], "unmonitored")
        self.assertIsNone(bottom["recovery_lower_bound_minutes"])
        fact = round_fact(facts)
        self.assertEqual(fact["round_status"], "incomplete")
        self.assertEqual(fact["unmonitored_required_positions"], ["shelf-bottom"])

    def test_layout_summary_reports_slowest_rounds_and_planning_readiness(self):
        slow = series("shelf-top", [(50, 0), (50, 6), (50, 12)])
        slow += series("shelf-middle", [(50, 0), (50, 6), (50, 12)])
        slow += series(
            "shelf-bottom", [(60, 0), (60, 6), (60, 12), (56, 18), (54, 24), (52, 30), (51, 36)]
        )
        fast = series("shelf-top", [(50, 0), (50, 6), (50, 12)])
        fast += series("shelf-middle", [(50, 0), (50, 6), (50, 12)])
        fast += series("shelf-bottom", [(50, 0), (50, 6), (50, 12), (50, 18)])
        incomplete = series("shelf-top", [(60, 0), (60, 6)])
        incomplete += series("shelf-bottom", [(60, 0), (60, 6)])

        data = base(
            required_positions=["shelf-top", "shelf-middle", "shelf-bottom"],
            records=[
                {"id": "a", "case": "c", "layout": "L", "observations": slow},
                {"id": "b", "case": "c", "layout": "L", "observations": fast},
                {"id": "c", "case": "c", "layout": "L", "observations": incomplete},
            ],
        )
        layout = next(
            fact for fact in process("humidity-response", data) if fact["fact_type"] == "layout"
        )

        self.assertEqual(layout["round_count"], 3)
        self.assertEqual(layout["complete_round_ids"], ["a", "b"])
        self.assertEqual(layout["incomplete_round_ids"], ["c"])
        self.assertEqual(layout["rounds_without_any_confirmed_recovery"], ["c"])
        self.assertEqual(layout["slowest_confirmed_recovery_minutes"], 24.0)
        self.assertEqual(layout["slowest_evidenced_positions"][0]["position"], "shelf-bottom")
        self.assertEqual(layout["slowest_evidenced_positions"][0]["round_id"], "a")
        self.assertEqual(layout["recovery_floor_minutes"], 24.0)
        self.assertFalse(layout["planning_ready"])
        self.assertIsNone(layout["planning_recovery_minutes"])
        self.assertEqual(layout["planning_blocked_by_round_ids"], ["c"])
        self.assertEqual(
            layout["planning_blocked_by_positions"],
            ["shelf-bottom", "shelf-middle", "shelf-top"],
        )

        middle = next(item for item in layout["positions"] if item["position"] == "shelf-middle")
        self.assertEqual(middle["confirmed_round_ids"], ["a", "b"])
        self.assertEqual(middle["slowest_confirmed_minutes"], 0.0)

    def test_layout_is_planning_ready_once_every_round_is_complete(self):
        observations = series("shelf-top", [(50, 0), (50, 6), (50, 12)])
        observations += series(
            "shelf-bottom", [(60, 0), (60, 6), (60, 12), (56, 18), (54, 24), (52, 30), (51, 36)]
        )
        data = base(
            records=[{"id": "a", "case": "c", "layout": "L", "observations": observations}]
        )
        layout = next(
            fact for fact in process("humidity-response", data) if fact["fact_type"] == "layout"
        )

        self.assertTrue(layout["planning_ready"])
        self.assertEqual(layout["planning_recovery_minutes"], 24.0)
        self.assertEqual(layout["planning_confirmed_by_minutes"], 36.0)
        self.assertEqual(layout["planning_blocked_by_round_ids"], [])

    def test_invalid_inputs_are_rejected(self):
        observations = series("shelf-top", [(50, 0)])
        record = {"id": "r", "case": "c", "layout": "L", "observations": observations}
        cases = [
            base(required_positions=[]),
            base(required_hold_minutes=0),
            base(max_observation_gap_minutes=0),
            base(lower_rh_pct=60, upper_rh_pct=50),
            base(records=[{
                **record,
                "observations": observations + [observation("dup", "shelf-top", 50, 0)],
            }]),
            base(records=[{
                **record,
                "observations": observations + [observation("neg", "shelf-top", 50, -1)],
            }]),
            base(records=[{**record, "reopened_at_minutes": 0}]),
            base(records=[{**record, "reopened_at_minutes": -5}]),
            base(records=[{**record, "reopened_at_minutes": "ten"}]),
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    process("humidity-response", data)

    def test_expected_sample_covers_all_conclusion_shapes(self):
        source = json.loads(
            (ROOT / "examples" / "humidity-response.json").read_text(encoding="utf-8")
        )
        facts = process("humidity-response", source)
        rounds = [fact for fact in facts if fact["fact_type"] == "round"]
        layouts = [fact for fact in facts if fact["fact_type"] == "layout"]

        self.assertEqual(len(rounds), 5)
        self.assertEqual(len(layouts), 3)
        self.assertEqual(
            {fact["round_conclusion"] for fact in rounds},
            {"complete", "incomplete_at_reopening", "incomplete"},
        )
        self.assertEqual(
            {item["finding"] for fact in rounds for item in fact["positions"]},
            {
                "confirmed_recovery",
                "hold_not_met_at_end_of_monitoring",
                "outside_band_at_last_observation",
                "interrupted_before_hold_met",
                "never_in_band",
                "no_observations",
                "monitoring_lost_during_candidate_stretch",
            },
        )
        ready = [fact for fact in layouts if fact["planning_ready"]]
        self.assertEqual([fact["layout"] for fact in ready], ["cabinet-A-double"])
        self.assertTrue(any(fact["rounds_without_any_confirmed_recovery"] for fact in layouts))


if __name__ == "__main__":
    unittest.main()
