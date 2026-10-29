"""填写问卷：开始、逐题保存、检查、提交、放弃。"""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pymongo.errors import DuplicateKeyError

from auth import login_required
from db import RESPONSES, SURVEY_QUESTIONS, SURVEYS, get_db
from logic import evaluate_next_question, normalize_answer
from utils import (
    answer_to_text,
    is_survey_open,
    respondent_key,
    to_object_id,
    utcnow,
)

bp = Blueprint("fill", __name__)


def _survey_or_404(public_id):
    survey = get_db()[SURVEYS].find_one({"public_id": public_id})
    if survey is None:
        abort(404)
    return survey


def _questions(survey_id):
    return list(
        get_db()[SURVEY_QUESTIONS].find({"survey_id": survey_id}).sort("order", 1)
    )


def _draft_session_key(public_id):
    return f"draft_{public_id}"


def _get_draft(survey):
    db = get_db()
    key = _draft_session_key(survey["public_id"])
    draft_id = to_object_id(session.get(key))
    if draft_id is not None:
        draft = db[RESPONSES].find_one(
            {
                "_id": draft_id,
                "survey_id": survey["_id"],
                "respondent_id": g.user["_id"],
                "status": "in_progress",
            }
        )
        if draft is not None:
            return draft
        session.pop(key, None)

    draft = db[RESPONSES].find_one(
        {
            "survey_id": survey["_id"],
            "respondent_id": g.user["_id"],
            "status": "in_progress",
        },
        sort=[("started_at", -1)],
    )
    if draft is not None:
        session[key] = str(draft["_id"])
    return draft


def _completed_count(survey):
    key = respondent_key(
        current_app.config["SECRET_KEY"], g.user["_id"], survey["_id"]
    )
    return get_db()[RESPONSES].count_documents(
        {
            "survey_id": survey["_id"],
            "status": "submitted",
            "$or": [{"respondent_id": g.user["_id"]}, {"respondent_key": key}],
        }
    )


def _find_answer(draft, question_id):
    for answer in draft.get("answers", []):
        if answer["survey_question_id"] == question_id:
            return answer
    return None


def _opt_id_to_key(question, opt_id):
    for opt in question.get("options", []):
        if opt.get("opt_id") == opt_id:
            return opt["key"]
    return opt_id


def _current_question(draft, questions):
    for question in questions:
        if question["_id"] == draft.get("current_question_id"):
            return question
    return None


def _open_or_redirect(survey):
    available, reason = is_survey_open(survey)
    if not available:
        flash(reason, "error")
        return False
    return True


@bp.get("/survey/<public_id>")
@login_required
def intro(public_id):
    survey = _survey_or_404(public_id)
    available, reason = is_survey_open(survey)
    questions = _questions(survey["_id"])
    draft = _get_draft(survey)
    completed = _completed_count(survey)
    blocked = (
        survey["status"] == "published"
        and not survey.get("allow_multiple", True)
        and completed > 0
        and draft is None
    )
    return render_template(
        "fill/intro.html",
        survey=survey,
        question_count=len(questions),
        available=available,
        unavailable_reason=reason,
        draft=draft,
        completed_count=completed,
        blocked=blocked,
    )


@bp.post("/survey/<public_id>/start")
@login_required
def start(public_id):
    survey = _survey_or_404(public_id)
    db = get_db()

    if not _open_or_redirect(survey):
        return redirect(url_for("fill.intro", public_id=public_id))

    questions = _questions(survey["_id"])
    if not questions:
        flash("该问卷还没有题目。", "error")
        return redirect(url_for("fill.intro", public_id=public_id))

    if not survey.get("allow_multiple", True) and _completed_count(survey) > 0:
        flash("该问卷不允许重复提交，你已经提交过了。", "error")
        return redirect(url_for("fill.intro", public_id=public_id))

    draft = _get_draft(survey)
    if draft is not None:
        return redirect(url_for("fill.fill", public_id=public_id))

    anonymous = bool(survey.get("allow_anonymous")) and bool(
        request.form.get("anonymous")
    )
    now = utcnow()
    try:
        result = db[RESPONSES].insert_one(
            {
                "survey_id": survey["_id"],
                "respondent_id": g.user["_id"],
                "anonymous": anonymous,
                "status": "in_progress",
                "answers": [],
                "path": [],
                "current_question_id": questions[0]["_id"],
                "started_at": now,
                "updated_at": now,
                "submitted_at": None,
            }
        )
    except DuplicateKeyError:
        draft = _get_draft(survey)
        if draft is not None:
            return redirect(url_for("fill.fill", public_id=public_id))
        flash("开始填写失败，请重试。", "error")
        return redirect(url_for("fill.intro", public_id=public_id))

    session[_draft_session_key(public_id)] = str(result.inserted_id)
    return redirect(url_for("fill.fill", public_id=public_id))


@bp.get("/survey/<public_id>/fill")
@login_required
def fill(public_id):
    survey = _survey_or_404(public_id)
    if not _open_or_redirect(survey):
        return redirect(url_for("fill.intro", public_id=public_id))

    draft = _get_draft(survey)
    if draft is None:
        flash("请先开始填写问卷。", "warning")
        return redirect(url_for("fill.intro", public_id=public_id))

    questions = _questions(survey["_id"])
    question = _current_question(draft, questions)
    if question is None:
        if draft.get("current_question_id") is None:
            return redirect(url_for("fill.review", public_id=public_id))
        flash("题目状态异常，请重新开始填写。", "error")
        return redirect(url_for("fill.intro", public_id=public_id))

    saved = _find_answer(draft, question["_id"])
    return _render_question(survey, draft, question, questions, saved=saved)


def _render_question(
    survey, draft, question, questions, saved=None, form_values=None, error=None, status=200
):
    if question["type"] == "multiple":
        if form_values is not None:
            selected = list(form_values)
        else:
            selected = [
                _opt_id_to_key(question, opt_id)
                for opt_id in ((saved or {}).get("value") or [])
            ]
    else:
        if form_values is not None:
            selected = form_values
        else:
            value = (saved or {}).get("value")
            if question["type"] == "single":
                selected = _opt_id_to_key(question, value) if value else ""
            else:
                selected = "" if value is None else str(value)

    return (
        render_template(
            "fill/question.html",
            survey=survey,
            draft=draft,
            question=question,
            questions=questions,
            selected=selected,
            error=error,
            answered_count=len(draft.get("path", [])),
            total_questions=len(questions),
        ),
        status,
    )


@bp.post("/survey/<public_id>/fill/answer")
@login_required
def answer(public_id):
    survey = _survey_or_404(public_id)
    if not _open_or_redirect(survey):
        return redirect(url_for("fill.intro", public_id=public_id))

    draft = _get_draft(survey)
    if draft is None:
        flash("填写会话已失效，请重新开始。", "warning")
        return redirect(url_for("fill.intro", public_id=public_id))

    questions = _questions(survey["_id"])
    question = _current_question(draft, questions)
    if question is None:
        return redirect(url_for("fill.review", public_id=public_id))

    if request.form.get("question_id") != str(question["_id"]):
        return redirect(url_for("fill.fill", public_id=public_id))

    if question["type"] == "multiple":
        raw = request.form.getlist("value")
    else:
        raw = request.form.get("value", "")

    value, error = normalize_answer(question, raw)
    if error:
        return _render_question(
            survey,
            draft,
            question,
            questions,
            form_values=raw,
            error=error,
            status=400,
        )

    now = utcnow()
    answers = [
        item
        for item in draft.get("answers", [])
        if item["survey_question_id"] != question["_id"]
    ]
    answers.append(
        {
            "survey_question_id": question["_id"],
            "question_id": question["question_id"],
            "version_id": question["version_id"],
            "type": question["type"],
            "value": value,
            "answered_at": now,
        }
    )
    path = list(draft.get("path", []))
    if question["_id"] not in path:
        path.append(question["_id"])

    next_question = evaluate_next_question(question, value, questions)
    next_id = next_question["_id"] if next_question is not None else None

    get_db()[RESPONSES].update_one(
        {"_id": draft["_id"], "status": "in_progress"},
        {
            "$set": {
                "answers": answers,
                "path": path,
                "current_question_id": next_id,
                "updated_at": now,
            }
        },
    )
    if next_id is None:
        return redirect(url_for("fill.review", public_id=public_id))
    return redirect(url_for("fill.fill", public_id=public_id))


@bp.get("/survey/<public_id>/review")
@login_required
def review(public_id):
    survey = _survey_or_404(public_id)
    if not _open_or_redirect(survey):
        return redirect(url_for("fill.intro", public_id=public_id))

    draft = _get_draft(survey)
    if draft is None:
        flash("填写会话已失效，请重新开始。", "warning")
        return redirect(url_for("fill.intro", public_id=public_id))
    if draft.get("current_question_id") is not None:
        return redirect(url_for("fill.fill", public_id=public_id))

    questions = _questions(survey["_id"])
    by_id = {question["_id"]: question for question in questions}
    rows = []
    for question_id in draft.get("path", []):
        question = by_id.get(question_id)
        if question is None:
            continue
        answer = _find_answer(draft, question_id)
        rows.append(
            {
                "question": question,
                "display": answer_to_text(question, answer["value"] if answer else None),
            }
        )
    if not rows:
        return redirect(url_for("fill.fill", public_id=public_id))

    return render_template("fill/review.html", survey=survey, draft=draft, rows=rows)


@bp.post("/survey/<public_id>/review/submit")
@login_required
def submit(public_id):
    survey = _survey_or_404(public_id)
    if not _open_or_redirect(survey):
        return redirect(url_for("fill.intro", public_id=public_id))

    draft = _get_draft(survey)
    if draft is None:
        flash("填写会话已失效，请重新开始。", "warning")
        return redirect(url_for("fill.intro", public_id=public_id))
    if draft.get("current_question_id") is not None:
        return redirect(url_for("fill.fill", public_id=public_id))

    questions = _questions(survey["_id"])
    by_id = {question["_id"]: question for question in questions}
    errors = []
    for question_id in draft.get("path", []):
        question = by_id.get(question_id)
        if question is None:
            errors.append("答卷中包含已不存在的题目，无法提交。")
            continue
        answer = _find_answer(draft, question_id)
        # 答案在作答时已按冻结快照校验并规范化；发布后快照不可变，这里只检查必答是否漏答
        if answer is None:
            if question.get("required"):
                errors.append(f"第 {question['order']} 题为必答题，尚未作答。")
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("fill.review", public_id=public_id))

    now = utcnow()
    update = {
        "$set": {
            "status": "submitted",
            "submitted_at": now,
            "updated_at": now,
            "current_question_id": None,
        },
        "$unset": {},
    }
    if draft.get("anonymous") and survey.get("allow_anonymous"):
        update["$set"]["anonymous"] = True
        update["$set"]["respondent_key"] = respondent_key(
            current_app.config["SECRET_KEY"], g.user["_id"], survey["_id"]
        )
        update["$unset"]["respondent_id"] = ""
    else:
        update["$set"]["anonymous"] = False
        update["$unset"]["respondent_key"] = ""

    result = get_db()[RESPONSES].update_one(
        {"_id": draft["_id"], "status": "in_progress"}, update
    )
    if result.modified_count == 0:
        flash("该答卷已经提交过了。", "warning")
    else:
        flash("问卷提交成功，感谢你的参与！", "success")
    session.pop(_draft_session_key(public_id), None)
    return redirect(url_for("fill.done", public_id=public_id))


@bp.get("/survey/<public_id>/done")
@login_required
def done(public_id):
    survey = _survey_or_404(public_id)
    return render_template("fill/done.html", survey=survey)


@bp.post("/survey/<public_id>/abandon")
@login_required
def abandon(public_id):
    survey = _survey_or_404(public_id)
    draft = _get_draft(survey)
    if draft is not None:
        get_db()[RESPONSES].delete_one({"_id": draft["_id"], "status": "in_progress"})
    session.pop(_draft_session_key(public_id), None)
    flash("已放弃本次填写，可以重新开始。", "success")
    return redirect(url_for("fill.intro", public_id=public_id))
