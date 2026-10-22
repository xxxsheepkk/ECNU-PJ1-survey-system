"""统计聚合：实时从 responses 计算，不保存冗余计数。"""

from db import RESPONSES

SUBMITTED_MATCH = {"status": "submitted"}
ANSWERED_VALUE_FILTER = {"$nin": [None, "", []]}


def count_submissions(db, survey_id):
    return db[RESPONSES].count_documents({"survey_id": survey_id, **SUBMITTED_MATCH})


def question_stats(
    db, survey_id, question, total_submissions, answer_limit=500, answer_offset=0
):
    """计算单题统计；文本/数字答案按提交时间倒序分页。"""
    qid = question["_id"]
    base = [
        {"$match": {"survey_id": survey_id, **SUBMITTED_MATCH}},
        {"$unwind": "$answers"},
        {"$match": {"answers.question_id": qid}},
    ]
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
            count = counts.get(opt["key"], 0)
            result["options"].append(
                {
                    "key": opt["key"],
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


def _answered_count(db, base, value_match):
    rows = list(db[RESPONSES].aggregate(list(base) + [value_match, {"$count": "n"}]))
    return rows[0]["n"] if rows else 0
