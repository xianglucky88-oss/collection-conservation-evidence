import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from collection_conservation_evidence import PIPELINES, process
from collection_conservation_evidence.storage import store

ROOT = Path(__file__).resolve().parent.parent


class BaselineTests(unittest.TestCase):
    def test_all_pipeline_outputs(self):
        self.assertEqual(len(PIPELINES), 5)
        for name in PIPELINES:
            with self.subTest(pipeline=name):
                source = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
                expected = json.loads((ROOT / "tests" / "expected" / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(process(name, source), expected)

    def test_cli_and_sqlite_replay(self):
        name = "light-exposure"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "facts.sqlite"
            cmd = [sys.executable, "-m", "collection_conservation_evidence", name, str(ROOT / "examples" / f"{name}.json"), "--sqlite", str(dest)]
            first = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            second = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone(), (1,))
                stored = db.execute("SELECT fact_json FROM facts ORDER BY fact_id").fetchall()
                self.assertEqual([json.loads(raw) for raw, in stored], sorted(result["facts"], key=lambda f: f["fact_id"]))

    def test_duplicate_fact_rolls_back_new_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "facts.sqlite"
            store(dest, "test", {"batch": 1}, [{"fact_id": "ok"}])
            with self.assertRaises(sqlite3.IntegrityError):
                store(dest, "test", {"batch": 2}, [{"fact_id": "dup"}, {"fact_id": "dup"}])
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone(), (1,))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM facts").fetchone(), (1,))

    def test_reject_invalid_input_before_database_write(self):
        name = "light-exposure"
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"records": [], "records": []}', encoding="utf-8")
            dest = Path(tmp) / "facts.sqlite"
            run = subprocess.run([sys.executable, "-m", "collection_conservation_evidence", name, str(bad), "--sqlite", str(dest)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("duplicate JSON key", run.stderr)
            self.assertFalse(dest.exists())
            with self.assertRaises(ValueError):
                process(name, [])
            with self.assertRaises((ValueError, KeyError)):
                process(name, {"records": [{"id": "missing-required-fields"}]})

    def test_trap_pipeline_rejects_legacy_or_malformed_input(self):
        with self.assertRaises((ValueError, KeyError)):
            process("trap-activity", [])
        # 结构完整但没有数据时产出空事实集，而不是报错
        self.assertEqual(
            process("trap-activity", {"zones": [], "traps": [], "checks": []}), [])
        legacy = {"records": [{"id": "check-9", "trap": "trap-N",
                               "taxon": "carpet beetle", "count": 3, "deployed_days": 12}]}
        with self.assertRaises((ValueError, KeyError)):
            process("trap-activity", legacy)
        malformed = {
            "zones": [{"id": "zone-a"}],
            "traps": [{"id": "trap-1", "placements": [
                {"zone": "zone-a", "start_day": 0, "end_day": 7}]}],
            "checks": [{"id": "check-1", "trap": "trap-1", "start_day": 0, "end_day": 7,
                        "entries": [{"id": "entry-1", "count": 1,
                                     "rank": "species", "genus": "Anthrenus"}]}],
        }
        with self.assertRaises(ValueError):
            process("trap-activity", malformed)


def _by_id(facts):
    return {fact["fact_id"]: fact for fact in facts}


class TrapActivityTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((ROOT / "examples" / "trap-activity.json").read_text(encoding="utf-8"))
        self.facts = _by_id(process("trap-activity", self.source))

    def test_amended_entry_is_authoritative_once_but_keeps_audit_sources(self):
        check = self.facts["trap-check:check-1"]
        self.assertEqual(check["authoritative_entry_id"], "entry-1-amended")
        self.assertEqual([entry["count"] for entry in check["entries"]], [2, 3])
        sources = {entry["entry"]: entry["source"] for entry in check["entries"]}
        self.assertEqual(sources["entry-1-initial"], "paper-log-week-47")
        self.assertEqual(sources["entry-1-amended"], "follow-up-email-week-47")
        zone_a = self.facts["zone-activity:zone-a"]
        # 补录后的 3 只只计入一次，被补录的 2 只不重复进入
        self.assertEqual(zone_a["count_definite"], 4)

    def test_handover_batch_is_exclusive_range_per_zone(self):
        batch = self.facts["handover-batch:check-4"]
        self.assertEqual(batch["zones"], ["zone-a", "zone-b"])
        self.assertEqual(batch["count"], 4)
        check = self.facts["trap-check:check-4"]
        self.assertTrue(check["ambiguous_handover"])
        for allocation in check["allocations"]:
            self.assertEqual((allocation["min_count"], allocation["max_count"]), (0, 4))
        # 同一批 4 只无法归属：每区的上界都最多能容纳整批，但两区不会同时得到它
        zone_a = self.facts["zone-activity:zone-a"]
        zone_b = self.facts["zone-activity:zone-b"]
        self.assertEqual(zone_a["count_max"] - zone_a["count_min"], 4)
        self.assertEqual(zone_b["count_max"] - zone_b["count_min"], 4)
        self.assertEqual(zone_a["count_min"] + zone_b["count_min"],
                         zone_a["count_definite"] + zone_b["count_definite"])
        # 观察盒日仍按交接时刻切分
        days = {item["zone"]: item["observed_days"] for item in check["allocations"]}
        self.assertEqual(days, {"zone-a": 3.0, "zone-b": 4.0})

    def test_early_removal_splits_zero_catch_from_missing_observation(self):
        zone_b = self.facts["zone-activity:zone-b"]
        self.assertEqual(zone_b["zero_catch_checks"], ["check-7"])
        self.assertEqual(zone_b["unobserved_segments"],
                         [{"days": 3.0, "end_day": 6.0, "start_day": 3.0, "trap": "trap-5"}])
        # trap-5 布放 4 天但只取得 1 天有效观察
        deployment = self.facts["trap-deployment:trap-5"]
        self.assertEqual(deployment["deployed_days"], 4.0)
        self.assertEqual(zone_b["deployed_trap_days"], 18.0)
        self.assertEqual(zone_b["effective_trap_days"], 12.0)

    def test_genus_comparison_includes_species_rank_and_batches(self):
        genus_a = self.facts["genus-comparison:Anthrenus:zone-a"]
        self.assertEqual(genus_a["checks"], ["check-1", "check-2", "check-4"])
        self.assertEqual((genus_a["count_min"], genus_a["count_definite"], genus_a["count_max"]),
                         (4, 4, 8))
        genus_b = self.facts["genus-comparison:Tineola:zone-b"]
        # 种级 check-5 的 5 只与属级 check-6 的 1 只合并
        self.assertEqual(genus_b["count_definite"], 6)
        self.assertEqual(genus_b["genus_rank_checks"], ["check-6"])

    def test_species_comparison_flags_genus_rank_and_handover_gaps(self):
        species_a = self.facts["species-comparison:Anthrenus-verbasci:zone-a"]
        self.assertEqual(species_a["presence_checks"], ["check-1"])
        self.assertEqual(species_a["genus_rank_checks"], ["check-2"])
        self.assertEqual(species_a["ambiguous_handover_checks"], ["check-4"])
        self.assertFalse(species_a["diagnosis_complete"])
        species_b = self.facts["species-comparison:Tineola-bisselliella:zone-b"]
        self.assertEqual(species_b["presence_checks"], ["check-5"])
        # check-7 是带种级鉴定的零计数，构成未出现证据；无鉴定零计数不进 absence
        self.assertEqual(species_b["absence_checks"], ["check-7"])
        self.assertEqual(species_b["genus_rank_checks"], ["check-6"])

    def test_zone_conclusion_traces_back_to_traps_and_rates(self):
        for zone_id in ("zone-a", "zone-b"):
            fact = self.facts[f"zone-activity:{zone_id}"]
            self.assertTrue(fact["traps"])
            self.assertIn("effective_trap_days", fact)
            self.assertIn("count_min", fact)
        verdict = self.facts["zone-comparison:zone-a:zone-b"]
        self.assertIn("inconclusive", verdict["verdict"])

    def test_reject_overlapping_checks_so_counts_cannot_double_enter(self):
        source = json.loads(json.dumps(self.source))
        source["checks"].append({
            "id": "check-overlap", "trap": "trap-1", "start_day": 1, "end_day": 2,
            "entries": [{"id": "entry-overlap", "count": 1, "rank": "genus",
                         "genus": "Anthrenus", "source": "paper-log-week-47"}],
        })
        with self.assertRaises(ValueError):
            process("trap-activity", source)

    def test_reject_amendment_chain_without_single_authority(self):
        source = json.loads(json.dumps(self.source))
        for check in source["checks"]:
            if check["id"] == "check-1":
                check["entries"][1].pop("amends")
        with self.assertRaises(ValueError):
            process("trap-activity", source)

    def test_reject_check_outside_deployment_and_noncontiguous_move(self):
        source = json.loads(json.dumps(self.source))
        source["checks"][0]["end_day"] = 9
        with self.assertRaises(ValueError):
            process("trap-activity", source)
        source = json.loads(json.dumps(self.source))
        for trap in source["traps"]:
            if trap["id"] == "trap-3":
                trap["placements"][1]["start_day"] = 4
        with self.assertRaises(ValueError):
            process("trap-activity", source)

    def test_zone_without_any_observation_is_not_ranked(self):
        source = json.loads(json.dumps(self.source))
        source["zones"].append({"id": "zone-c", "name": "空区"})
        facts = _by_id(process("trap-activity", source))
        self.assertIsNone(facts["zone-activity:zone-c"]["min_per_trap_day"])
        self.assertIn("incomparable", facts["zone-comparison:zone-b:zone-c"]["verdict"])
