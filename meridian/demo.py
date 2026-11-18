"""运行本地合成数据的端到端验收场景：宽泛科技议题拆分为细分类别。"""
import json
import tempfile
from pathlib import Path
from .service import MeridianService

LEGACY = "vocab-legacy"
SPLIT = "vocab-split"


def vocabulary_payloads():
    return [
        {"version_id": LEGACY,
         "rationale": "Original broad topic vocabulary; kept so historical judgments stay reviewable",
         "definitions": {
             "technology": {"parent": None,
                            "explanation": "All technology coverage under the original broad caliber"},
             "culture": {"parent": None, "explanation": "Cultural coverage"}}},
        {"version_id": SPLIT,
         "rationale": "Split the broad technology code into reviewed fine categories",
         "definitions": {
             "technology": {"parent": None,
                            "explanation": "Umbrella category compatible with the legacy broad code"},
             "technology.ai": {"parent": "technology",
                               "explanation": "Artificial intelligence and automation"},
             "technology.biotech": {"parent": "technology",
                                    "explanation": "Biotechnology and health technology"},
             "culture": {"parent": None, "explanation": "Cultural coverage"}}},
    ]


def source(source_id, region, outlet):
    return {"source_id": source_id, "region": region, "outlet": outlet,
            "topics": ["technology", "technology.ai", "technology.biotech", "culture"],
            "audiences": ["general", "students"]}


def observation(external_id, source_id, story_key, published_at="2026-11-01T10:00:00+00:00"):
    return {"origin": "local-fixture", "external_id": external_id, "source_id": source_id,
            "story_key": story_key, "published_at": published_at,
            "received_at": "2026-11-02T09:00:00+00:00",
            "headline": f"Synthetic observation {external_id}",
            "url": f"https://example.invalid/{external_id}"}


def decision(external_id, reviewer, stance, topic="technology", audience="general",
             vocabulary=LEGACY):
    return {"decision_id": f"{external_id}-{reviewer}-{vocabulary}", "origin": "local-fixture",
            "external_id": external_id, "revision": 1, "reviewer": reviewer,
            "vocabulary": vocabulary, "topic": topic, "stance": stance, "audience": audience,
            "reason": "Synthetic reviewer judgment based on supplied fixture"}


def query(basis="parent", vocabulary=None):
    return {"start": "2026-11-01T00:00:00+00:00", "end": "2026-12-01T00:00:00+00:00",
            "regions": ["North"], "topics": [], "audiences": [],
            "basis": basis, "vocabulary": vocabulary}


def scenario():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "scenario.sqlite3")
        service = MeridianService(path)
        for payload in vocabulary_payloads():
            service.vocabularies.register(payload)
        service.sources.register(source("s1", "North", "Outlet A"))
        service.sources.register(source("s2", "North", "Outlet B"))
        service.sources.register(source("s3", "South", "Outlet C"))
        rows = [observation("a", "s1", "story-1"), observation("b", "s2", "story-1"),
                observation("c", "s1", "story-2")]
        first = service.batches.step("fixture-batch", rows, limit=1)
        assert first["cursor"] == 1
        batch = service.batches.step("fixture-batch", rows)
        assert batch["accepted"] == 3 and batch["state"] == "closed"
        for external_id in ("a", "b"):
            service.coding.submit(decision(external_id, "reviewer-a", "positive"))
            service.coding.submit(decision(external_id, "reviewer-b", "positive"))
        service.coding.submit(decision("c", "reviewer-a", "positive", topic="technology.ai",
                                       vocabulary=SPLIT))
        service.coding.submit(decision("c", "reviewer-b", "positive", topic="technology.ai",
                                       vocabulary=SPLIT))
        parent = service.analytics.report(query())
        assert parent["story_count"] == 2
        assert parent["judgment_versions"] == {LEGACY: 1, SPLIT: 1}
        fine = service.analytics.report(query("fine", SPLIT))
        assert fine["story_count"] == 1 and fine["excluded"] == {"pending_rereview": 2}
        legacy = service.analytics.report(query("legacy", LEGACY))
        assert legacy["story_count"] == 1 and legacy["excluded"] == {"pending_rereview": 1}
        gated = service.releases.preview(query("fine", SPLIT))
        assert "unreviewed_legacy_labels" in gated["quality"]["reasons"]
        preview = service.releases.preview(query())
        assert preview["quality"]["passed"]
        release = service.releases.publish("november", query(), preview["token"])
        assert release["report"]["story_count"] == 2
        for external_id in ("a", "b"):
            service.coding.submit(decision(external_id, "reviewer-a", "positive",
                                           topic="technology.ai", vocabulary=SPLIT))
            service.coding.submit(decision(external_id, "reviewer-b", "positive",
                                           topic="technology.ai", vocabulary=SPLIT))
        rereviewed = service.analytics.report(query("fine", SPLIT))
        assert rereviewed["story_count"] == 2 and rereviewed["excluded"] == {}
        drift = service.releases.compare("november")
        assert drift["story_count_delta"] == 0 and not drift["same_result"]
        service.observations.retract("local-fixture", "c", "bad source record", expected_revision=1)
        comparison = service.releases.compare("november")
        assert comparison["story_count_delta"] == -1
        assert service.releases.describe("november")["report"]["story_count"] == 2
        audit = service.audit.run()
        assert audit["ok"]
        result = {"batch": batch["state"], "released_stories": 2,
                  "fine_pending_before_rereview": fine["excluded"]["pending_rereview"],
                  "fine_stories_after_rereview": rereviewed["story_count"],
                  "current_delta": comparison["story_count_delta"], "audit": audit["ok"]}
        service.close()
        return result


if __name__ == "__main__":
    print(json.dumps(scenario(), ensure_ascii=False, sort_keys=True))
