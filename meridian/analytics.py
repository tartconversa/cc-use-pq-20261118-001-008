"""区域汇总、显式排除与故事级去重。趋势口径由研究员显式选择：legacy、parent 或 fine。"""
from collections import Counter, defaultdict
from .contracts import digest, report_query
from .errors import require

RESOLVED = {"consensus", "adjudicated"}


class EvidenceAnalytics:
    def __init__(self, store, observations, sources, coding, vocabularies):
        self.store = store
        self.observations = observations
        self.sources = sources
        self.coding = coding
        self.vocabularies = vocabularies

    def _rows(self, spec):
        rows = []
        for item in self.observations.current():
            data = item["data"]
            if not spec["start"] <= data["published_at"] < spec["end"]:
                continue
            source = self.sources.describe(data["source_id"])
            if spec["regions"] and source["region"] not in spec["regions"]:
                continue
            states = self.coding.status_by_vocabulary(data["origin"], data["external_id"],
                                                      item["revision"])
            rows.append({"item": item, "source": source, "states": states})
        return rows

    @staticmethod
    def _resolved(row):
        return {version: state["coding"] for version, state in row["states"].items()
                if state["status"] in RESOLVED}

    def _resolve(self, row, spec):
        """把一条观察归入口径类别；返回 (category, judgment|None)。

        judgment 形状：{"vocabulary", "recorded_topic", "topic", "stance", "audience"}，
        其中 topic 是用于分组的口径议题（parent 口径下为根类别）。
        """
        item = row["item"]
        if item["status"] == "retracted":
            return "retracted", None
        states = row["states"]
        basis = spec["basis"]
        if basis in {"legacy", "fine"}:
            version = spec["vocabulary"]
            state = states.get(version)
            if state and state["status"] in RESOLVED:
                coding = state["coding"]
                if spec["topics"] and coding["topic"] not in spec["topics"]:
                    return "outside_topic", None
                if spec["audiences"] and coding["audience"] not in spec["audiences"]:
                    return "outside_audience", None
                return "eligible", {"vocabulary": version, "recorded_topic": coding["topic"],
                                    "topic": coding["topic"], "stance": coding["stance"],
                                    "audience": coding["audience"]}
            if state and state["status"] == "disputed":
                return "disputed", None
            resolved_elsewhere = any(other != version and other_state["status"] in RESOLVED
                                     for other, other_state in states.items())
            if resolved_elsewhere:
                return "pending_rereview", None
            return "uncoded", None
        resolved = self._resolved(row)
        if resolved:
            chosen = max(resolved, key=lambda version: self.vocabularies.describe(version)["event_no"])
            coding = resolved[chosen]
            root = self.vocabularies.root(chosen, coding["topic"])
            if spec["topics"] and root not in spec["topics"]:
                return "outside_topic", None
            if spec["audiences"] and coding["audience"] not in spec["audiences"]:
                return "outside_audience", None
            return "eligible", {"vocabulary": chosen, "recorded_topic": coding["topic"],
                                "topic": root, "stance": coding["stance"],
                                "audience": coding["audience"]}
        if any(state["status"] == "disputed" for state in states.values()):
            return "disputed", None
        return "uncoded", None

    def _classify(self, row, spec):
        return self._resolve(row, spec)[0]

    def report(self, payload):
        spec = report_query(payload)
        if spec["vocabulary"] is not None:
            self.vocabularies.describe(spec["vocabulary"])
        observed = self._rows(spec)
        exclusions = Counter()
        groups = defaultdict(list)
        provenance = []
        pending_by_region = Counter()
        referenced_versions = {spec["vocabulary"]} if spec["vocabulary"] else set()
        for row in observed:
            category, judgment = self._resolve(row, spec)
            data = row["item"]["data"]
            resolved = self._resolved(row)
            referenced_versions.update(resolved)
            provenance.append({"origin": data["origin"], "external_id": data["external_id"],
                               "revision": row["item"]["revision"],
                               "source_id": data["source_id"], "category": category,
                               "vocabulary": judgment["vocabulary"] if judgment else None,
                               "judgments": resolved})
            if category != "eligible":
                exclusions[category] += 1
                if category == "pending_rereview":
                    pending_by_region[row["source"]["region"]] += 1
                continue
            key = (row["source"]["region"], data["story_key"], judgment["topic"],
                   judgment["audience"])
            groups[key].append({"row": row, "judgment": judgment})
        story_rows = []
        for (region, story, topic, audience), members in sorted(groups.items()):
            ordered = sorted(members, key=lambda member: (member["row"]["item"]["data"]["published_at"],
                                                          member["row"]["item"]["data"]["source_id"],
                                                          member["row"]["item"]["data"]["origin"],
                                                          member["row"]["item"]["data"]["external_id"]))
            stances = [member["judgment"]["stance"] for member in members]
            stance = stances[0] if len(set(stances)) == 1 else "mixed"
            vocabularies = sorted({member["judgment"]["vocabulary"] for member in members})
            recorded = sorted({member["judgment"]["recorded_topic"] for member in members})
            story_rows.append({"region": region, "story_key": story, "topic": topic,
                               "audience": audience, "stance": stance,
                               "observation_count": len(members),
                               "vocabularies": vocabularies, "recorded_topics": recorded,
                               "mixed_basis": len(vocabularies) > 1,
                               "outlets": sorted({member["row"]["source"]["outlet"] for member in members}),
                               "representative": {"origin": ordered[0]["row"]["item"]["data"]["origin"],
                                                  "external_id": ordered[0]["row"]["item"]["data"]["external_id"]}})
        by_region = defaultdict(lambda: {"stories": 0, "stances": Counter(), "topics": Counter(),
                                         "audiences": Counter(), "observations": 0,
                                         "judgment_versions": Counter(), "mixed_basis_stories": 0})
        for story in story_rows:
            bucket = by_region[story["region"]]
            bucket["stories"] += 1
            bucket["stances"][story["stance"]] += 1
            bucket["topics"][story["topic"]] += 1
            bucket["audiences"][story["audience"]] += 1
            bucket["observations"] += story["observation_count"]
            for version in story["vocabularies"]:
                bucket["judgment_versions"][version] += 1
            bucket["mixed_basis_stories"] += int(story["mixed_basis"])
        regions = []
        for region in sorted(set(by_region) | set(pending_by_region)):
            data = by_region[region]
            denominator = data["stories"]
            regions.append({"region": region, "story_count": denominator,
                            "observation_count": data["observations"],
                            "stance_counts": dict(sorted(data["stances"].items())),
                            "stance_shares": {key: round(value / denominator, 6)
                                              for key, value in sorted(data["stances"].items())},
                            "topic_counts": dict(sorted(data["topics"].items())),
                            "audience_counts": dict(sorted(data["audiences"].items())),
                            "pending_rereview": pending_by_region.get(region, 0),
                            "judgment_versions": dict(sorted(data["judgment_versions"].items())),
                            "mixed_basis_stories": data["mixed_basis_stories"]})
        judgment_versions = Counter()
        for story in story_rows:
            for version in story["vocabularies"]:
                judgment_versions[version] += 1
        referenced_versions.update(judgment_versions)
        return {"query": spec, "query_hash": digest(spec), "event_head": self.store.head(),
                "observations_in_window": len(observed),
                "eligible_observations": sum(len(members) for members in groups.values()),
                "excluded": dict(sorted(exclusions.items())), "story_count": len(story_rows),
                "stories": story_rows, "regions": regions,
                "judgment_versions": dict(sorted(judgment_versions.items())),
                "mixed_basis_stories": sum(1 for story in story_rows if story["mixed_basis"]),
                "vocabulary_definitions": self.vocabularies.snapshot(referenced_versions),
                "provenance": sorted(provenance, key=lambda item: (item["origin"], item["external_id"]))}

    def trace_story(self, spec, region, story_key):
        report = self.report(spec)
        rows = [row for row in report["stories"] if row["region"] == region and row["story_key"] == story_key]
        require(rows, "story_missing", "story not found in query")
        observations = []
        for row in self._rows(report["query"]):
            data = row["item"]["data"]
            if row["source"]["region"] == region and data["story_key"] == story_key:
                observations.append({"origin": data["origin"], "external_id": data["external_id"],
                                     "revision": row["item"]["revision"],
                                     "classification": self._classify(row, report["query"]),
                                     "coding_status_by_vocabulary":
                                         {version: state["status"]
                                          for version, state in row["states"].items()},
                                     "judgments": self._resolved(row),
                                     "source_id": data["source_id"]})
        return {"stories": rows, "observations": sorted(observations, key=lambda x: (x["origin"], x["external_id"]))}

    def quality(self, report, *, minimum_stories=1, maximum_exclusion_share=0.4):
        require(type(minimum_stories) is int and minimum_stories >= 1, "invalid_gate", "minimum_stories invalid")
        require(isinstance(maximum_exclusion_share, (int, float)) and 0 <= maximum_exclusion_share <= 1,
                "invalid_gate", "maximum_exclusion_share invalid")
        total = report["observations_in_window"]
        reasons = []
        if report["story_count"] < minimum_stories:
            reasons.append("insufficient_stories")
        excluded = sum(report["excluded"].values())
        if total == 0 or excluded / total > maximum_exclusion_share:
            reasons.append("excess_exclusions")
        if report["excluded"].get("disputed", 0):
            reasons.append("unresolved_disputes")
        if report["query"]["basis"] == "fine" and report["excluded"].get("pending_rereview", 0):
            reasons.append("unreviewed_legacy_labels")
        if any(story["stance"] == "mixed" and story["observation_count"] > 1
               for story in report["stories"]):
            reasons.append("cross_source_stance_conflict")
        return {"passed": not reasons, "reasons": reasons, "total": total,
                "excluded": excluded, "minimum_stories": minimum_stories,
                "maximum_exclusion_share": maximum_exclusion_share}
