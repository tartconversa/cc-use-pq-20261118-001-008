"""独立人工编码及争议裁定，不自动推断倾向。每条判断显式记录所用词表版本。"""
from .contracts import adjudication, decision, digest, utc_now
from .errors import MeridianError, require
from .storage import decode, encode

RESOLVED = {"consensus", "adjudicated"}


class CodingDesk:
    def __init__(self, store, observations, sources, vocabularies):
        self.store = store
        self.observations = observations
        self.sources = sources
        self.vocabularies = vocabularies

    def _eligible(self, origin, external_id, revision):
        item = self.observations.describe(origin, external_id, revision)
        require(item["is_head"] and item["status"] == "active", "stale_observation",
                "coding requires active head revision")
        return item

    def _validate_judgment(self, item, data):
        self.vocabularies.require_topic(data["vocabulary"], data["topic"])
        self.sources.validate_coding(item["data"]["source_id"], data["topic"], data["audience"])

    def submit(self, payload):
        with self.store.transaction():
            return self._submit_locked(payload)

    def _submit_locked(self, payload):
        data = decision(payload)
        existing = self.store.one("SELECT * FROM decisions WHERE decision_id=?", (data["decision_id"],))
        if existing:
            saved = self.describe(data["decision_id"])
            if all(saved[key] == data[key] for key in data):
                return saved | {"duplicate": True}
            raise MeridianError("decision_conflict", "decision id was reused with different content")
        item = self._eligible(data["origin"], data["external_id"], data["revision"])
        self._validate_judgment(item, data)
        key = (data["origin"], data["external_id"], data["revision"], data["reviewer"],
               data["vocabulary"])
        previous = self.store.one("SELECT decision_id FROM decisions WHERE origin=? AND external_id=? "
                                  "AND revision=? AND reviewer=? AND vocabulary=?", key)
        require(previous is None, "reviewer_conflict",
                "reviewer already coded this revision under this vocabulary")
        now = utc_now()
        with self.store.transaction():
            event_no = self.store.event("coding_submitted", data["decision_id"], digest(data), now)
            self.store.db.execute(
                "INSERT INTO decisions(decision_id,origin,external_id,revision,reviewer,vocabulary,"
                "topic,stance,audience,reason,created_at,event_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (data["decision_id"], *key, data["topic"], data["stance"],
                 data["audience"], data["reason"], now, event_no),
            )
        return self.describe(data["decision_id"]) | {"duplicate": False}

    def describe(self, decision_id):
        row = self.store.one("SELECT * FROM decisions WHERE decision_id=?", (decision_id,))
        require(row is not None, "decision_missing", "decision not found")
        return {key: row[key] for key in ("decision_id", "origin", "external_id", "revision",
                                           "reviewer", "vocabulary", "topic", "stance", "audience",
                                           "reason", "event_no", "created_at")}

    def decisions_for(self, origin, external_id, revision, vocabulary=None):
        if vocabulary is None:
            rows = self.store.all("SELECT decision_id FROM decisions WHERE origin=? AND external_id=? "
                                  "AND revision=? ORDER BY vocabulary,reviewer",
                                  (origin, external_id, revision))
        else:
            rows = self.store.all("SELECT decision_id FROM decisions WHERE origin=? AND external_id=? "
                                  "AND revision=? AND vocabulary=? ORDER BY reviewer",
                                  (origin, external_id, revision, vocabulary))
        return [self.describe(row["decision_id"]) for row in rows]

    def status(self, origin, external_id, revision, vocabulary):
        decisions = self.decisions_for(origin, external_id, revision, vocabulary)
        if len(decisions) < 2:
            return {"status": "awaiting_review", "vocabulary": vocabulary, "decisions": decisions}
        values = {(row["topic"], row["stance"], row["audience"]) for row in decisions}
        if len(values) == 1:
            return {"status": "consensus", "vocabulary": vocabulary, "decisions": decisions,
                    "coding": dict(zip(("topic", "stance", "audience"), next(iter(values))))}
        key = f"{origin}/{external_id}/{revision}/{vocabulary}"
        adjudicated = self.store.one("SELECT * FROM adjudications WHERE case_key=?", (key,))
        if adjudicated:
            heads = [row["decision_id"] for row in decisions]
            if decode(adjudicated["decision_heads"]) == heads:
                return {"status": "adjudicated", "vocabulary": vocabulary, "decisions": decisions,
                        "coding": {key: adjudicated[key] for key in ("topic", "stance", "audience")},
                        "adjudicator": adjudicated["adjudicator"]}
        return {"status": "disputed", "vocabulary": vocabulary, "decisions": decisions}

    def status_by_vocabulary(self, origin, external_id, revision):
        rows = self.store.all("SELECT DISTINCT vocabulary FROM decisions WHERE origin=? "
                              "AND external_id=? AND revision=? ORDER BY vocabulary",
                              (origin, external_id, revision))
        return {row["vocabulary"]: self.status(origin, external_id, revision, row["vocabulary"])
                for row in rows}

    def adjudicate(self, payload):
        with self.store.transaction():
            return self._adjudicate_locked(payload)

    def _adjudicate_locked(self, payload):
        data = adjudication(payload)
        item = self._eligible(data["origin"], data["external_id"], data["revision"])
        self._validate_judgment(item, data)
        vocabulary = data["vocabulary"]
        status = self.status(data["origin"], data["external_id"], data["revision"], vocabulary)
        require(status["status"] in {"disputed", "adjudicated"}, "adjudication_not_needed",
                "only disagreement can be adjudicated")
        require(data["adjudicator"] not in {row["reviewer"] for row in status["decisions"]},
                "self_adjudication", "reviewer cannot adjudicate own case")
        key = f"{data['origin']}/{data['external_id']}/{data['revision']}/{vocabulary}"
        heads = [row["decision_id"] for row in status["decisions"]]
        existing = self.store.one("SELECT * FROM adjudications WHERE case_key=?", (key,))
        if existing:
            same = (all(existing[field] == data[field] for field in ("adjudicator", "topic",
                                                                  "stance", "audience", "reason"))
                    and decode(existing["decision_heads"]) == heads)
            require(same, "adjudication_conflict", "case already has another ruling")
            return self.status(data["origin"], data["external_id"], data["revision"],
                               vocabulary) | {"duplicate": True}
        now = utc_now()
        with self.store.transaction():
            event_no = self.store.event("case_adjudicated", key, digest(data), now)
            self.store.db.execute(
                "INSERT INTO adjudications(case_key,origin,external_id,revision,vocabulary,"
                "adjudicator,topic,stance,audience,reason,decision_heads,created_at,event_no) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, data["origin"], data["external_id"], data["revision"], vocabulary,
                 data["adjudicator"], data["topic"], data["stance"], data["audience"],
                 data["reason"], encode(heads), now, event_no),
            )
        return self.status(data["origin"], data["external_id"], data["revision"],
                           vocabulary) | {"duplicate": False}
