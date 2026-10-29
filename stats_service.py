"""统计聚合：单问卷统计 + 跨问卷单题统计。

答案在 answers[] 中冗余了 question_id（题目族）与 version_id，跨问卷统计可直接按族聚合。
单选/多选的 value 存 opt_id，跨版本合并按 opt_id 稳定累加。
"""

from db import QUESTION_VERSIONS, QUESTIONS, RESPONSES

SUBMITTED_MATCH = {"status": "submitted"}
ANSWERED_VALUE_FILTER = {"$nin": [None, "", []]}


def count_submissions(db, survey_id):
    return db[RESPONSES].count_documents({"survey_id": survey_id, **SUBMITTED_MATCH})


def _per_survey_pipeline(survey_id, qid):
    return [
        {"$match": {"survey_id": survey_id, **SUBMITTED_MATCH}},
        {"$unwind": "$answers"},
        {"$match": {"answers.survey_question_id": qid}},
    ]


def _answered_count(db, base, value_match):
    rows = list(db[RESPONSES].aggregate(list(base) + [value_match, {"$count": "n"}]))
    return rows[0]["n"] if rows else 0


def question_stats(
    db, survey_id, question, total_submissions, answer_limit=500, answer_offset=0
):
    """计算单题统计；question 为 survey_questions 引用（含 opt_id 选项）。"""
    qid = question["_id"]
    base = _per_survey_pipeline(survey_id, qid)
    value_match = {"$match": {"answers.value": ANSWERED_VALUE_FILTER}}

    result = {
        "question": question,
        "type": question["type"],
        "total_submissions": total_submissions,
        "answered_count": 0,
        "unanswered_count": total_submissions,
        "options": [],
        "answer_rows": [],
        "number": None,
        "truncated": False,
        "offset": answer_offset,
        "limit": answer_limit,
    }

    if question["type"] in ("single", "multiple"):
        pipeline = list(base) + [value_match]
        if question["type"] == "multiple":
            pipeline.append({"$unwind": "$answers.value"})
        pipeline.append({"$group": {"_id": "$answers.value", "count": {"$sum": 1}}})
        counts = {row["_id"]: row["count"] for row in db[RESPONSES].aggregate(pipeline)}

        answered = _answered_count(db, base, value_match)
        result["answered_count"] = answered
        result["unanswered_count"] = max(total_submissions - answered, 0)
        for opt in question.get("options", []):
            count = counts.get(opt["opt_id"], 0)
            result["options"].append(
                {
                    "key": opt["key"],
                    "opt_id": opt["opt_id"],
                    "label": opt["text"],
                    "count": count,
                    "percent": round(count * 100 / answered, 1) if answered else 0.0,
                }
            )
        return result

    if question["type"] == "text":
        total_answered = _answered_count(db, base, value_match)
        result["answered_count"] = total_answered
        result["unanswered_count"] = max(total_submissions - total_answered, 0)
        pipeline = (
            list(base)
            + [
                value_match,
                {"$sort": {"submitted_at": -1}},
                {"$skip": answer_offset},
                {"$limit": answer_limit + 1},
                {"$project": {"_id": 0, "value": "$answers.value", "at": "$submitted_at"}},
            ]
        )
        rows = list(db[RESPONSES].aggregate(pipeline))
        result["truncated"] = len(rows) > answer_limit
        result["answer_rows"] = rows[:answer_limit]
        return result

    if question["type"] == "number":
        total_answered = _answered_count(db, base, value_match)
        result["answered_count"] = total_answered
        result["unanswered_count"] = max(total_submissions - total_answered, 0)

        group = list(
            db[RESPONSES].aggregate(
                list(base)
                + [
                    value_match,
                    {
                        "$group": {
                            "_id": None,
                            "count": {"$sum": 1},
                            "avg": {"$avg": "$answers.value"},
                            "min": {"$min": "$answers.value"},
                            "max": {"$max": "$answers.value"},
                        }
                    },
                ]
            )
        )
        if group:
            row = group[0]
            result["number"] = {
                "count": row["count"],
                "avg": round(row["avg"], 2) if row.get("avg") is not None else None,
                "min": row.get("min"),
                "max": row.get("max"),
            }
        else:
            result["number"] = {"count": 0, "avg": None, "min": None, "max": None}

        pipeline = (
            list(base)
            + [
                value_match,
                {"$sort": {"submitted_at": -1}},
                {"$skip": answer_offset},
                {"$limit": answer_limit + 1},
                {"$project": {"_id": 0, "value": "$answers.value", "at": "$submitted_at"}},
            ]
        )
        rows = list(db[RESPONSES].aggregate(pipeline))
        result["truncated"] = len(rows) > answer_limit
        result["answer_rows"] = rows[:answer_limit]
        return result

    return result


def cross_question_stats(db, question_id):
    """跨问卷单题统计：merged 按 opt_id 累加，by_version 按版本分层，等价组仅标注。"""
    question = db[QUESTIONS].find_one({"_id": question_id})
    versions = list(
        db[QUESTION_VERSIONS].find({"question_id": question_id}).sort("version_no", 1)
    )
    qtype = question["type"]

    result = {
        "question": question,
        "type": qtype,
        "versions": [
            {"version_no": v["version_no"], "content_hash": v["content_hash"][:10]}
            for v in versions
        ],
        "merged": [],
        "by_version": [],
        "equivalent_groups": [],
        "text_rows": [],
        "number": None,
        "total_answered": 0,
    }

    if qtype in ("single", "multiple"):
        counts = _cross_option_counts(db, question_id, qtype)
        answered_by_version = _cross_answered_by_version(db, question_id)
        total_answered = sum(answered_by_version.values())
        result["total_answered"] = total_answered

        for v in versions:
            answered = answered_by_version.get(v["_id"], 0)
            opt_rows = []
            for opt in v.get("options", []):
                cnt = counts.get((v["_id"], opt["opt_id"]), 0)
                opt_rows.append(
                    {
                        "opt_id": opt["opt_id"],
                        "text": opt["text"],
                        "count": cnt,
                        "percent": round(cnt * 100 / answered, 1) if answered else 0.0,
                    }
                )
            result["by_version"].append(
                {
                    "version_no": v["version_no"],
                    "content_hash": v["content_hash"][:10],
                    "answered": answered,
                    "options": opt_rows,
                }
            )

        merged_map = {}
        for v in versions:
            for opt in v.get("options", []):
                entry = merged_map.setdefault(
                    opt["opt_id"],
                    {
                        "opt_id": opt["opt_id"],
                        "text": opt["text"],
                        "count": 0,
                        "appeared_in_versions": [],
                        "label_differs": False,
                    },
                )
                cnt = counts.get((v["_id"], opt["opt_id"]), 0)
                entry["count"] += cnt
                if cnt > 0:
                    entry["appeared_in_versions"].append(v["version_no"])
                if opt["text"] != entry["text"]:
                    entry["label_differs"] = True
        for entry in merged_map.values():
            entry["percent"] = (
                round(entry["count"] * 100 / total_answered, 1) if total_answered else 0.0
            )
        result["merged"] = sorted(merged_map.values(), key=lambda e: e["opt_id"])

    elif qtype == "text":
        rows = list(
            db[RESPONSES].aggregate(
                [
                    {"$match": {"status": "submitted", "answers.question_id": question_id}},
                    {"$unwind": "$answers"},
                    {
                        "$match": {
                            "answers.question_id": question_id,
                            "answers.value": ANSWERED_VALUE_FILTER,
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "version_id": "$answers.version_id",
                            "value": "$answers.value",
                        }
                    },
                ]
            )
        )
        vno = {v["_id"]: v["version_no"] for v in versions}
        result["text_rows"] = [
            {"value": r["value"], "version_no": vno.get(r["version_id"])} for r in rows
        ]
        result["total_answered"] = len(rows)

    elif qtype == "number":
        rows = list(
            db[RESPONSES].aggregate(
                [
                    {"$match": {"status": "submitted", "answers.question_id": question_id}},
                    {"$unwind": "$answers"},
                    {
                        "$match": {
                            "answers.question_id": question_id,
                            "answers.value": ANSWERED_VALUE_FILTER,
                        }
                    },
                    {
                        "$group": {
                            "_id": "$answers.version_id",
                            "count": {"$sum": 1},
                            "avg": {"$avg": "$answers.value"},
                            "min": {"$min": "$answers.value"},
                            "max": {"$max": "$answers.value"},
                        }
                    },
                ]
            )
        )
        vno = {v["_id"]: v["version_no"] for v in versions}
        result["by_version"] = [
            {
                "version_no": vno.get(r["_id"]),
                "count": r["count"],
                "avg": round(r["avg"], 2) if r.get("avg") is not None else None,
                "min": r.get("min"),
                "max": r.get("max"),
            }
            for r in rows
        ]
        overall = list(
            db[RESPONSES].aggregate(
                [
                    {"$match": {"status": "submitted", "answers.question_id": question_id}},
                    {"$unwind": "$answers"},
                    {
                        "$match": {
                            "answers.question_id": question_id,
                            "answers.value": ANSWERED_VALUE_FILTER,
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "count": {"$sum": 1},
                            "avg": {"$avg": "$answers.value"},
                            "min": {"$min": "$answers.value"},
                            "max": {"$max": "$answers.value"},
                        }
                    },
                ]
            )
        )
        if overall:
            o = overall[0]
            result["number"] = {
                "count": o["count"],
                "avg": round(o["avg"], 2) if o.get("avg") is not None else None,
                "min": o.get("min"),
                "max": o.get("max"),
            }
            result["total_answered"] = o["count"]

    # 等价组：content_hash 相同的版本归为一组，仅作标注
    groups = {}
    for v in versions:
        groups.setdefault(v["content_hash"], []).append(v["version_no"])
    result["equivalent_groups"] = [
        {"content_hash": h[:10], "version_nos": nos}
        for h, nos in groups.items()
        if len(nos) > 1
    ]
    return result


def _cross_option_counts(db, question_id, qtype):
    pipeline = [
        {"$match": {"status": "submitted", "answers.question_id": question_id}},
        {"$unwind": "$answers"},
        {
            "$match": {
                "answers.question_id": question_id,
                "answers.value": ANSWERED_VALUE_FILTER,
            }
        },
    ]
    if qtype == "multiple":
        pipeline.append({"$unwind": "$answers.value"})
    pipeline.append(
        {
            "$group": {
                "_id": {"version_id": "$answers.version_id", "opt_id": "$answers.value"},
                "count": {"$sum": 1},
            }
        }
    )
    counts = {}
    for row in db[RESPONSES].aggregate(pipeline):
        key = (row["_id"]["version_id"], row["_id"]["opt_id"])
        counts[key] = counts.get(key, 0) + row["count"]
    return counts


def _cross_answered_by_version(db, question_id):
    rows = db[RESPONSES].aggregate(
        [
            {"$match": {"status": "submitted", "answers.question_id": question_id}},
            {"$unwind": "$answers"},
            {
                "$match": {
                    "answers.question_id": question_id,
                    "answers.value": ANSWERED_VALUE_FILTER,
                }
            },
            {"$group": {"_id": "$answers.version_id", "n": {"$sum": 1}}},
        ]
    )
    return {r["_id"]: r["n"] for r in rows}
