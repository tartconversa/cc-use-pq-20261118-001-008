import unittest
from tests.common import ServiceCase
from meridian.demo import LEGACY, SPLIT, decision, observation, query, source, vocabulary_payloads
from meridian.errors import MeridianError


class VocabularyRegistryTests(ServiceCase):
    def test_registration_is_idempotent_and_immutable(self):
        payload = vocabulary_payloads()[0]
        repeat = self.service.vocabularies.register(payload)
        self.assertTrue(repeat["duplicate"])
        changed = payload | {"rationale": "rewrite history"}
        with self.assertRaises(MeridianError) as caught:
            self.service.vocabularies.register(changed)
        self.assertEqual(caught.exception.code, "vocabulary_conflict")

    def test_definitions_keep_per_period_explanations(self):
        legacy = self.service.vocabularies.describe(LEGACY)
        split = self.service.vocabularies.describe(SPLIT)
        self.assertIn("broad caliber", legacy["definitions"]["technology"]["explanation"])
        self.assertEqual(split["definitions"]["technology.ai"]["parent"], "technology")
        self.assertEqual(split["rationale"], vocabulary_payloads()[1]["rationale"])

    def test_parent_must_be_defined(self):
        bad = {"version_id": "v-bad", "rationale": "broken",
               "definitions": {"tech.ai": {"parent": "technology", "explanation": "orphan"}}}
        with self.assertRaises(MeridianError) as caught:
            self.service.vocabularies.register(bad)
        self.assertEqual(caught.exception.code, "invalid_vocabulary")

    def test_parent_cycle_is_rejected(self):
        bad = {"version_id": "v-cycle", "rationale": "broken",
               "definitions": {"a": {"parent": "b", "explanation": "x"},
                               "b": {"parent": "a", "explanation": "y"}}}
        with self.assertRaises(MeridianError) as caught:
            self.service.vocabularies.register(bad)
        self.assertEqual(caught.exception.code, "invalid_vocabulary")

    def test_decision_requires_registered_vocabulary(self):
        self.add("a")
        unknown = decision("a", "alice", "positive", vocabulary="v-unknown")
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(unknown)
        self.assertEqual(caught.exception.code, "vocabulary_missing")

    def test_topic_must_be_defined_in_chosen_version(self):
        self.add("a")
        misplaced = decision("a", "alice", "positive", topic="technology.ai")
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(misplaced)
        self.assertEqual(caught.exception.code, "topic_outside_vocabulary")

    def test_source_permission_still_applies(self):
        self.service.sources.register(source("limited", "North", "Outlet L")
                                      | {"topics": ["technology"]})
        self.add("x", "limited")
        item = decision("x", "alice", "positive", topic="culture")
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(item)
        self.assertEqual(caught.exception.code, "topic_outside_source")

    def test_reviewer_may_recode_same_revision_under_new_vocabulary(self):
        self.accepted("a")
        self.code("a", topic="technology.ai", vocabulary=SPLIT)
        states = self.service.coding.status_by_vocabulary("local-fixture", "a", 1)
        self.assertEqual(states[LEGACY]["status"], "consensus")
        self.assertEqual(states[SPLIT]["status"], "consensus")

    def test_adjudication_is_scoped_per_vocabulary(self):
        self.add("a")
        for vocabulary, topic in ((LEGACY, "technology"), (SPLIT, "technology.ai")):
            self.service.coding.submit(decision("a", "alice", "positive", topic=topic,
                                                vocabulary=vocabulary))
            self.service.coding.submit(decision("a", "bob", "negative", topic=topic,
                                                vocabulary=vocabulary))
        ruling = {"origin": "local-fixture", "external_id": "a", "revision": 1,
                  "adjudicator": "carol", "vocabulary": LEGACY, "topic": "technology",
                  "stance": "mixed", "audience": "general", "reason": "resolve legacy only"}
        self.service.coding.adjudicate(ruling)
        states = self.service.coding.status_by_vocabulary("local-fixture", "a", 1)
        self.assertEqual(states[LEGACY]["status"], "adjudicated")
        self.assertEqual(states[SPLIT]["status"], "disputed")

    def test_source_topics_update_preserves_old_judgments(self):
        self.accepted("a")
        updated = self.service.sources.set_topics(
            "north-a", ["technology.ai", "technology.biotech", "culture"])
        self.assertEqual(updated["version"], 2)
        report = self.report()
        self.assertEqual(report["story_count"], 1)
        self.assertEqual(report["stories"][0]["vocabularies"], [LEGACY])
        self.add("b")
        stale = decision("b", "alice", "positive")
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(stale)
        self.assertEqual(caught.exception.code, "topic_outside_source")
        self.assertTrue(self.service.audit.run()["ok"])


class BasisReportTests(ServiceCase):
    def test_fine_basis_marks_legacy_only_stories_pending_rereview(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        report = self.report(basis="fine", vocabulary=SPLIT)
        self.assertEqual(report["story_count"], 1)
        self.assertEqual(report["excluded"], {"pending_rereview": 1})
        self.assertEqual(report["regions"][0]["pending_rereview"], 1)

    def test_legacy_basis_marks_new_only_stories_pending_rereview(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        report = self.report(basis="legacy", vocabulary=LEGACY)
        self.assertEqual(report["story_count"], 1)
        self.assertEqual(report["excluded"], {"pending_rereview": 1})

    def test_parent_basis_rolls_up_and_prefers_newest_version(self):
        self.accepted("a")
        self.code("a", stance="negative", topic="technology.ai", vocabulary=SPLIT)
        report = self.report()
        story = report["stories"][0]
        self.assertEqual(story["topic"], "technology")
        self.assertEqual(story["recorded_topics"], ["technology.ai"])
        self.assertEqual(story["vocabularies"], [SPLIT])
        self.assertEqual(story["stance"], "negative")

    def test_parent_basis_merges_calibers_into_one_story(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared", topic="technology.biotech", vocabulary=SPLIT)
        report = self.report()
        self.assertEqual(report["story_count"], 1)
        story = report["stories"][0]
        self.assertEqual(story["observation_count"], 2)
        self.assertEqual(story["vocabularies"], [LEGACY, SPLIT])
        self.assertEqual(story["recorded_topics"], ["technology", "technology.biotech"])
        self.assertTrue(story["mixed_basis"])
        self.assertEqual(report["mixed_basis_stories"], 1)

    def test_parent_basis_spans_versions_without_vocabulary_parameter(self):
        with self.assertRaises(MeridianError) as caught:
            self.report(vocabulary=LEGACY)
        self.assertEqual(caught.exception.code, "invalid_query")

    def test_fine_basis_requires_explicit_vocabulary(self):
        with self.assertRaises(MeridianError) as caught:
            self.service.analytics.report(query("fine"))
        self.assertEqual(caught.exception.code, "invalid_query")

    def test_unknown_basis_is_rejected(self):
        with self.assertRaises(MeridianError) as caught:
            self.report(basis="auto")
        self.assertEqual(caught.exception.code, "invalid_basis")

    def test_report_requires_registered_query_vocabulary(self):
        with self.assertRaises(MeridianError) as caught:
            self.report(basis="fine", vocabulary="v-unknown")
        self.assertEqual(caught.exception.code, "vocabulary_missing")

    def test_fine_gate_flags_unreviewed_legacy_labels(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        preview = self.service.releases.preview(query("fine", SPLIT))
        self.assertFalse(preview["quality"]["passed"])
        self.assertIn("unreviewed_legacy_labels", preview["quality"]["reasons"])
        with self.assertRaises(MeridianError) as caught:
            self.service.releases.publish("fine-release", query("fine", SPLIT), preview["token"])
        self.assertEqual(caught.exception.code, "quality_failed")

    def test_parent_basis_release_passes_during_transition(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        preview = self.service.releases.preview(query())
        self.assertTrue(preview["quality"]["passed"])
        release = self.service.releases.publish("parent-release", query(), preview["token"])
        self.assertEqual(release["report"]["story_count"], 2)

    def test_region_rows_expose_denominator_pending_and_versions(self):
        self.accepted("a", "north-a", "one")
        self.accepted("b", "north-b", "two", topic="technology.ai", vocabulary=SPLIT)
        report = self.report(basis="fine", vocabulary=SPLIT)
        region = report["regions"][0]
        self.assertEqual(region["story_count"], 1)
        self.assertEqual(region["pending_rereview"], 1)
        self.assertEqual(region["judgment_versions"], {SPLIT: 1})
        self.assertEqual(report["judgment_versions"], {SPLIT: 1})


class ComparabilityTests(ServiceCase):
    def window(self, start, end, basis, vocabulary=None):
        return {"start": start, "end": end, "regions": ["North"], "topics": [],
                "audiences": [], "basis": basis, "vocabulary": vocabulary}

    def test_basis_mismatch_is_flagged_not_hidden(self):
        self.accepted("a", published_at="2026-11-02T10:00:00+00:00")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT,
                      published_at="2026-11-20T10:00:00+00:00")
        left = self.window("2026-11-01T00:00:00+00:00", "2026-11-15T00:00:00+00:00",
                           "legacy", LEGACY)
        right = self.window("2026-11-15T00:00:00+00:00", "2026-12-01T00:00:00+00:00",
                            "fine", SPLIT)
        result = self.service.comparisons.windows(left, right)
        self.assertFalse(result["comparability"]["comparable"])
        kinds = [issue["issue"] for issue in result["comparability"]["issues"]]
        self.assertIn("basis_mismatch", kinds)
        self.assertIn("judgment_basis_shift", kinds)

    def test_rereview_pending_marks_period_not_directly_comparable(self):
        self.accepted("a", published_at="2026-11-02T10:00:00+00:00")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT,
                      published_at="2026-11-20T10:00:00+00:00")
        left = self.window("2026-11-01T00:00:00+00:00", "2026-11-15T00:00:00+00:00",
                           "fine", SPLIT)
        right = self.window("2026-11-15T00:00:00+00:00", "2026-12-01T00:00:00+00:00",
                            "fine", SPLIT)
        result = self.service.comparisons.windows(left, right)
        issues = result["comparability"]["issues"]
        pending = [issue for issue in issues if issue["issue"] == "rereview_pending"]
        self.assertEqual(pending[0]["side"], "left")
        self.assertEqual(pending[0]["observations"], 1)
        row = result["regions"][0]
        self.assertEqual(row["left_pending_rereview"], 1)
        self.assertEqual(row["right_pending_rereview"], 0)

    def test_clean_same_basis_comparison_is_comparable(self):
        self.accepted("a", published_at="2026-11-02T10:00:00+00:00")
        self.accepted("b", published_at="2026-11-20T10:00:00+00:00")
        left = self.window("2026-11-01T00:00:00+00:00", "2026-11-15T00:00:00+00:00",
                           "legacy", LEGACY)
        right = self.window("2026-11-15T00:00:00+00:00", "2026-12-01T00:00:00+00:00",
                            "legacy", LEGACY)
        result = self.service.comparisons.windows(left, right)
        self.assertTrue(result["comparability"]["comparable"])
        self.assertEqual(result["comparability"]["issues"], [])

    def test_region_comparison_separates_caliber_shift_from_reporting_change(self):
        self.accepted("a", "north-a", "one")
        self.accepted("b", "south-a", "two", topic="technology.ai", vocabulary=SPLIT)
        result = self.service.comparisons.regions(query() | {"regions": ["North", "South"]},
                                                  "North", "South")
        self.assertFalse(result["comparability"]["comparable"])
        shift = [issue for issue in result["comparability"]["issues"]
                 if issue["issue"] == "judgment_basis_shift"]
        self.assertEqual(shift[0]["left"], {LEGACY: 1})
        self.assertEqual(shift[0]["right"], {SPLIT: 1})


class VocabularyProvenanceTests(ServiceCase):
    def test_late_arriving_old_period_story_counts_under_judgment_vocabulary(self):
        late = observation("late", "north-a", "old-story", "2026-11-02T10:00:00+00:00")
        late["received_at"] = "2026-11-18T09:00:00+00:00"
        self.service.observations.ingest(late)
        self.code("late", topic="technology.ai", vocabulary=SPLIT)
        fine = self.report(basis="fine", vocabulary=SPLIT)
        self.assertEqual(fine["story_count"], 1)
        self.assertEqual(fine["excluded"], {})
        self.assertEqual(fine["stories"][0]["vocabularies"], [SPLIT])

    def test_correction_detaches_adjudicated_opinion_from_active_story(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        self.service.coding.submit(decision("a", "bob", "negative"))
        ruling = {"origin": "local-fixture", "external_id": "a", "revision": 1,
                  "adjudicator": "carol", "vocabulary": LEGACY, "topic": "technology",
                  "stance": "mixed", "audience": "general", "reason": "split the difference"}
        self.service.coding.adjudicate(ruling)
        self.assertEqual(self.report()["story_count"], 1)
        changed = self.service.observations.describe("local-fixture", "a")["data"] | {
            "story_key": "corrected"}
        self.service.observations.ingest(changed, expected_revision=1)
        report = self.report()
        self.assertEqual(report["story_count"], 0)
        self.assertEqual(report["excluded"], {"uncoded": 1})
        history = self.service.provenance.observation_history("local-fixture", "a")
        first, second = history["revisions"]
        self.assertFalse(first["is_head"])
        self.assertEqual(first["coding_status_by_vocabulary"], {LEGACY: "adjudicated"})
        self.assertTrue(second["is_head"])
        self.assertEqual(second["coding_status_by_vocabulary"], {})

    def test_frozen_release_keeps_vocabulary_snapshot(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        self.service.releases.publish("release", query(), preview["token"])
        frozen = self.service.releases.describe("release")["report"]
        self.assertIn(LEGACY, frozen["vocabulary_definitions"])
        explanation = frozen["vocabulary_definitions"][LEGACY]["definitions"]["technology"]
        self.assertIn("broad caliber", explanation["explanation"])
        bundle = self.service.exports.frozen("release")
        self.assertTrue(self.service.exports.verify(bundle)["valid"])
        self.assertEqual(bundle["evidence"][0]["vocabulary"], LEGACY)

    def test_export_evidence_shows_judgment_version_per_story(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        bundle = self.service.exports.current(query("fine", SPLIT))
        rows = {row["external_id"]: row for row in bundle["evidence"]}
        self.assertEqual(rows["a"]["category"], "pending_rereview")
        self.assertIsNone(rows["a"]["basis_vocabulary"])
        self.assertEqual(sorted(rows["a"]["judgments"]), [LEGACY])
        self.assertEqual(rows["b"]["basis_vocabulary"], SPLIT)
        self.assertEqual(rows["b"]["basis_topic"], "technology.ai")
        self.assertTrue(self.service.exports.verify(bundle)["valid"])

    def test_source_contribution_marks_pending_rereview(self):
        self.accepted("a")
        self.accepted("b", topic="technology.ai", vocabulary=SPLIT)
        result = self.service.provenance.source_contribution(query("fine", SPLIT), "north-a")
        self.assertEqual(result["categories"], {"eligible": 1, "pending_rereview": 1})
        self.assertEqual(result["stories"][0]["vocabularies"], [SPLIT])

    def test_review_queue_prioritizes_pending_rereview(self):
        self.accepted("a")
        self.add("b")
        queue = self.service.diagnostics.queue(query("fine", SPLIT))
        self.assertEqual(queue["by_status"], {"pending_rereview": 1, "uncoded": 1})
        self.assertEqual(queue["items"][0]["status"], "pending_rereview")
        self.assertEqual(queue["items"][0]["resolved_vocabularies"], [LEGACY])
        readiness = self.service.diagnostics.readiness(query("fine", SPLIT))
        self.assertIn("rereview_pending", readiness["warnings"])

    def test_release_compare_surfaces_caliber_drift_with_zero_count_delta(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        self.service.releases.publish("release", query(), preview["token"])
        self.code("a", topic="technology.ai", vocabulary=SPLIT)
        comparison = self.service.releases.compare("release")
        self.assertEqual(comparison["story_count_delta"], 0)
        self.assertFalse(comparison["same_result"])
        changes = self.service.provenance.release_changes("release")
        self.assertEqual(changes["changed_stories"][0]["change"], "revised")
        self.assertEqual(changes["changed_stories"][0]["before"]["vocabularies"], [LEGACY])
        self.assertEqual(changes["changed_stories"][0]["after"]["vocabularies"], [SPLIT])


if __name__ == "__main__":
    unittest.main()
