"""统计页面（创建者侧）。"""

import math

from flask import Blueprint, abort, current_app, g, render_template, request

from auth import login_required
from db import QUESTIONS, SURVEYS, get_db
from stats_service import count_submissions, question_stats
from utils import QUESTION_TYPES, status_label, to_object_id

bp = Blueprint("stats", __name__)


def _owned_survey_or_404(survey_id):
    oid = to_object_id(survey_id)
    if oid is None:
        abort(404)
    survey = get_db()[SURVEYS].find_one({"_id": oid, "owner_id": g.user["_id"]})
    if survey is None:
        abort(404)
    return survey


def _questions(survey_id):
    return list(get_db()[QUESTIONS].find({"survey_id": survey_id}).sort("order", 1))


@bp.get("/surveys/<survey_id>/stats")
@login_required
def overview(survey_id):
    survey = _owned_survey_or_404(survey_id)
    db = get_db()
    total = count_submissions(db, survey["_id"])
    questions = _questions(survey["_id"])
    stats_rows = [
        question_stats(db, survey["_id"], question, total, answer_limit=10)
        for question in questions
    ]
    return render_template(
        "stats/overview.html",
        survey=survey,
        total=total,
        stats_rows=stats_rows,
        question_types=QUESTION_TYPES,
        display_status=status_label(survey),
    )


@bp.get("/surveys/<survey_id>/stats/questions/<question_id>")
@login_required
def question_detail(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    qid = to_object_id(question_id)
    if qid is None:
        abort(404)
    question = get_db()[QUESTIONS].find_one({"_id": qid, "survey_id": survey["_id"]})
    if question is None:
        abort(404)

    total = count_submissions(get_db(), survey["_id"])
    per_page = max(1, int(current_app.config["STATS_ANSWER_LIMIT"]))
    try:
        page = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    def load(page_number):
        return question_stats(
            get_db(),
            survey["_id"],
            question,
            total,
            answer_limit=per_page,
            answer_offset=(page_number - 1) * per_page,
        )

    stats = load(page)
    total_pages = max(1, math.ceil(stats["answered_count"] / per_page))
    if page > total_pages:
        page = total_pages
        stats = load(page)

    offset = (page - 1) * per_page
    stats.update(
        {
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "range_start": offset + 1 if stats["answer_rows"] else 0,
            "range_end": offset + len(stats["answer_rows"]),
        }
    )
    return render_template(
        "stats/question.html",
        survey=survey,
        stats=stats,
        question=question,
        total=total,
        question_types=QUESTION_TYPES,
    )
