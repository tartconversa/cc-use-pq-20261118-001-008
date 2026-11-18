import tempfile
import unittest
from pathlib import Path
from meridian.demo import LEGACY, SPLIT, decision, observation, query, source, vocabulary_payloads
from meridian.service import MeridianService


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "db.sqlite3")
        self.service = MeridianService(self.path)
        for payload in vocabulary_payloads():
            self.service.vocabularies.register(payload)
        self.service.sources.register(source("north-a", "North", "Outlet A"))
        self.service.sources.register(source("north-b", "North", "Outlet B"))
        self.service.sources.register(source("south-a", "South", "Outlet C"))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def add(self, external_id, source_id="north-a", story_key=None, published_at=None):
        item = observation(external_id, source_id, story_key or external_id)
        if published_at is not None:
            item["published_at"] = published_at
        return self.service.observations.ingest(item)

    def code(self, external_id, stance="positive", *, source_id="north-a",
             topic="technology", vocabulary=LEGACY):
        self.service.coding.submit(decision(external_id, "alice", stance, topic=topic,
                                        vocabulary=vocabulary))
        self.service.coding.submit(decision(external_id, "bob", stance, topic=topic,
                                            vocabulary=vocabulary))

    def accepted(self, external_id, source_id="north-a", story_key=None, published_at=None,
                 **coding):
        self.add(external_id, source_id, story_key, published_at=published_at)
        self.code(external_id, source_id=source_id, **coding)

    def report(self, **overrides):
        return self.service.analytics.report(query(**overrides))
