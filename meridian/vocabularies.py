"""版本化编码词表：注册后不可变，保留各时段的解释与审核依据。"""
from .contracts import digest, utc_now, vocabulary_version
from .errors import MeridianError, require
from .storage import decode, encode


class VocabularyRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, payload):
        with self.store.transaction():
            return self._register_locked(payload)

    def _register_locked(self, payload):
        data = vocabulary_version(payload)
        existing = self.store.one("SELECT * FROM vocabulary_versions WHERE version_id=?",
                                  (data["version_id"],))
        if existing:
            current = self.describe(data["version_id"])
            if all(current[key] == data[key] for key in data):
                return {**current, "duplicate": True}
            raise MeridianError("vocabulary_conflict",
                                "vocabulary version is immutable once registered")
        now = utc_now()
        with self.store.transaction():
            event_no = self.store.event("vocabulary_registered", data["version_id"],
                                        digest(data), now)
            self.store.db.execute(
                "INSERT INTO vocabulary_versions(version_id,definitions,rationale,created_at,event_no) "
                "VALUES(?,?,?,?,?)",
                (data["version_id"], encode(data["definitions"]), data["rationale"], now, event_no))
        return {**self.describe(data["version_id"]), "duplicate": False}

    def describe(self, version_id):
        row = self.store.one("SELECT * FROM vocabulary_versions WHERE version_id=?", (version_id,))
        require(row is not None, "vocabulary_missing", "vocabulary version not found",
                version_id=version_id)
        return {"version_id": row["version_id"], "definitions": decode(row["definitions"]),
                "rationale": row["rationale"], "created_at": row["created_at"],
                "event_no": row["event_no"]}

    def list(self):
        rows = self.store.all("SELECT version_id FROM vocabulary_versions ORDER BY event_no")
        return [self.describe(row["version_id"]) for row in rows]

    def require_topic(self, version_id, topic):
        version = self.describe(version_id)
        require(topic in version["definitions"], "topic_outside_vocabulary",
                "topic is not defined in this vocabulary version",
                version_id=version_id, topic=topic)
        return version

    def root(self, version_id, topic):
        version = self.require_topic(version_id, topic)
        node = topic
        while version["definitions"][node]["parent"] is not None:
            node = version["definitions"][node]["parent"]
        return node

    def snapshot(self, version_ids):
        return {version_id: self.describe(version_id) for version_id in sorted(version_ids)}
