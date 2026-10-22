"""问卷与题目管理（创建者侧）。"""

import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from pymongo.errors import DuplicateKeyError

from auth import login_required
from db import QUESTIONS, RESPONSES, SURVEYS, get_db
from logic import describe_rule, validate_all_rules, validate_jump_rule
from stats_service import count_submissions
from utils import (
    COUNT_VALUE_OPERATORS,
    JUMP_OPERATORS,
    OPTION_VALUE_OPERATORS,
    QUESTION_TYPES,
    is_survey_open,
    parse_local_datetime,
    parse_optional_float,
    parse_optional_int,
    question_settings_summary,
    status_label,
    to_object_id,
    utcnow,
)

bp = Blueprint("surveys", __name__)


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


def _question_or_404(survey_id, question_id):
    qid = to_object_id(question_id)
    if qid is None:
        abort(404)
    question = get_db()[QUESTIONS].find_one({"_id": qid, "survey_id": survey_id})
    if question is None:
        abort(404)
    return question


def _new_public_id():
    return secrets.token_urlsafe(8)


def _parse_survey_form():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    allow_anonymous = bool(request.form.get("allow_anonymous"))
    allow_multiple = bool(request.form.get("allow_multiple"))
    deadline, deadline_error = parse_local_datetime(request.form.get("deadline"))

    errors = []
    if not title:
        errors.append("请填写问卷标题。")
    elif len(title) > 120:
        errors.append("问卷标题最多 120 个字符。")
    if len(description) > 5000:
        errors.append("问卷说明最多 5000 个字符。")
    if deadline_error:
        errors.append("截止时间格式不正确。")

    return {
        "title": title,
        "description": description,
        "allow_anonymous": allow_anonymous,
        "allow_multiple": allow_multiple,
        "deadline": deadline,
    }, errors


def _next_option_key(used):
    for index in range(26):
        key = chr(ord("a") + index)
        if key not in used:
            return key
    return f"o{len(used) + 1}"


def _parse_question_form(existing=None):
    """解析题目表单；编辑时题型保持不变。"""
    qtype = request.form.get("type") if existing is None else existing["type"]
    if qtype not in QUESTION_TYPES:
        return None, ["请选择合法的题型。"]

    title = request.form.get("title", "").strip()
    errors = []
    if not title:
        errors.append("请填写题目内容。")
    elif len(title) > 500:
        errors.append("题目内容最多 500 个字符。")
    required = bool(request.form.get("required"))

    options = []
    settings = {}

    if qtype in ("single", "multiple"):
        lines = [
            line.strip()
            for line in request.form.get("options", "").splitlines()
            if line.strip()
        ]
        if len(lines) < 2:
            errors.append("选择题至少需要 2 个选项。")
        elif len(lines) > 20:
            errors.append("选择题最多支持 20 个选项。")
        elif len(set(lines)) != len(lines):
            errors.append("选项内容不能重复。")

        old_key_by_text = {
            opt["text"]: opt["key"] for opt in (existing or {}).get("options", [])
        }
        used = set()
        for text in lines:
            key = old_key_by_text.get(text)
            if key is None or key in used:
                key = _next_option_key(used)
            used.add(key)
            options.append({"key": key, "text": text})

        if qtype == "multiple":
            low, low_error = parse_optional_int(request.form.get("min_selections"))
            high, high_error = parse_optional_int(request.form.get("max_selections"))
            if low_error:
                errors.append("最少选择数量" + low_error + "。")
            if high_error:
                errors.append("最多选择数量" + high_error + "。")
            if low is not None and low < 1:
                errors.append("最少选择数量必须大于等于 1。")
            if high is not None and high < 1:
                errors.append("最多选择数量必须大于等于 1。")
            if low is not None and high is not None and low > high:
                errors.append("最少选择数量不能大于最多选择数量。")
            if high is not None and len(options) and high > len(options):
                errors.append("最多选择数量不能超过选项总数。")
            # 重做时补充的边界校验
            if low is not None and len(options) and low > len(options):
                errors.append("最少选择数量不能超过选项总数。")
            settings = {"min_selections": low, "max_selections": high}

    elif qtype == "text":
        low, low_error = parse_optional_int(request.form.get("min_length"))
        high, high_error = parse_optional_int(request.form.get("max_length"))
        if low_error:
            errors.append("最少字数" + low_error + "。")
        if high_error:
            errors.append("最多字数" + high_error + "。")
        if low is not None and low < 0:
            errors.append("最少字数不能为负数。")
        if high is not None and high < 0:
            errors.append("最多字数不能为负数。")
        if low is not None and high is not None and low > high:
            errors.append("最少字数不能大于最多字数。")
        if high is not None and high > 5000:
            errors.append("最多字数不能超过 5000。")
        settings = {"min_length": low, "max_length": high}

    elif qtype == "number":
        low, low_error = parse_optional_float(request.form.get("min_value"))
        high, high_error = parse_optional_float(request.form.get("max_value"))
        if low_error:
            errors.append("最小值" + low_error + "。")
        if high_error:
            errors.append("最大值" + high_error + "。")
        if low is not None and high is not None and low > high:
            errors.append("最小值不能大于最大值。")
        settings = {
            "min": low,
            "max": high,
            "integer_only": bool(request.form.get("integer_only")),
        }

    return {
        "type": qtype,
        "title": title,
        "required": required,
        "options": options,
        "settings": settings,
    }, errors


def _question_values(question=None):
    if question is None:
        return {
            "type": "single",
            "title": "",
            "required": False,
            "options": "",
            "min_selections": "",
            "max_selections": "",
            "min_length": "",
            "max_length": "",
            "min_value": "",
            "max_value": "",
            "integer_only": False,
        }
    settings = question.get("settings") or {}
    return {
        "type": question["type"],
        "title": question["title"],
        "required": question.get("required", False),
        "options": "\n".join(opt["text"] for opt in question.get("options", [])),
        "min_selections": _display_number(settings.get("min_selections")),
        "max_selections": _display_number(settings.get("max_selections")),
        "min_length": _display_number(settings.get("min_length")),
        "max_length": _display_number(settings.get("max_length")),
        "min_value": _display_number(settings.get("min")),
        "max_value": _display_number(settings.get("max")),
        "integer_only": bool(settings.get("integer_only")),
    }


def _form_question_values():
    return {
        "type": request.form.get("type", "single"),
        "title": request.form.get("title", ""),
        "required": bool(request.form.get("required")),
        "options": request.form.get("options", ""),
        "min_selections": request.form.get("min_selections", ""),
        "max_selections": request.form.get("max_selections", ""),
        "min_length": request.form.get("min_length", ""),
        "max_length": request.form.get("max_length", ""),
        "min_value": request.form.get("min_value", ""),
        "max_value": request.form.get("max_value", ""),
        "integer_only": bool(request.form.get("integer_only")),
    }


def _display_number(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _prune_rules(question, options):
    keys = [opt["key"] for opt in options]
    kept = []
    for rule in question.get("jump_rules", []):
        condition = rule.get("condition") or {}
        operator = condition.get("operator")
        value = condition.get("value")
        if operator in OPTION_VALUE_OPERATORS and value not in keys:
            continue
        if operator in COUNT_VALUE_OPERATORS:
            try:
                if int(value) > len(options):
                    continue
            except (TypeError, ValueError):
                continue
        kept.append(rule)
    return kept


def _renumber_questions(survey_id):
    db = get_db()
    questions = _questions(survey_id)
    for question in questions:
        db[QUESTIONS].update_one(
            {"_id": question["_id"]},
            {"$set": {"order": question["order"] + 10000}},
        )
    for index, question in enumerate(questions, start=1):
        db[QUESTIONS].update_one(
            {"_id": question["_id"]}, {"$set": {"order": index}}
        )


@bp.get("/")
@login_required
def dashboard():
    db = get_db()
    surveys = list(
        db[SURVEYS].find({"owner_id": g.user["_id"]}).sort("created_at", -1)
    )
    ids = [survey["_id"] for survey in surveys]
    counts = {}
    if ids:
        rows = db[RESPONSES].aggregate(
            [
                {"$match": {"survey_id": {"$in": ids}, "status": "submitted"}},
                {"$group": {"_id": "$survey_id", "n": {"$sum": 1}}},
            ]
        )
        counts = {row["_id"]: row["n"] for row in rows}
    for survey in surveys:
        survey["display_status"] = status_label(survey)
        survey["submission_count"] = counts.get(survey["_id"], 0)
    return render_template("surveys/dashboard.html", surveys=surveys)


@bp.route("/surveys/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        data, errors = _parse_survey_form()
        if not errors:
            db = get_db()
            now = utcnow()
            for _ in range(5):
                doc = {
                    "owner_id": g.user["_id"],
                    "public_id": _new_public_id(),
                    "title": data["title"],
                    "description": data["description"],
                    "allow_anonymous": data["allow_anonymous"],
                    "allow_multiple": data["allow_multiple"],
                    "deadline": data["deadline"],
                    "status": "draft",
                    "created_at": now,
                    "updated_at": now,
                    "published_at": None,
                    "closed_at": None,
                }
                try:
                    result = db[SURVEYS].insert_one(doc)
                except DuplicateKeyError:
                    continue
                flash("问卷已创建，请添加题目。", "success")
                return redirect(url_for("surveys.detail", survey_id=result.inserted_id))
            errors.append("生成问卷链接失败，请重试。")
        for error in errors:
            flash(error, "error")
        return render_template(
            "surveys/survey_form.html", values=request.form, survey=None
        )
    return render_template("surveys/survey_form.html", values=None, survey=None)


@bp.route("/surveys/<survey_id>/edit", methods=["GET", "POST"])
@login_required
def edit(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后只能修改截止时间。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if request.method == "POST":
        data, errors = _parse_survey_form()
        if not errors:
            get_db()[SURVEYS].update_one(
                {"_id": survey["_id"]},
                {"$set": {**data, "updated_at": utcnow()}},
            )
            flash("问卷信息已更新。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
        for error in errors:
            flash(error, "error")
        return render_template(
            "surveys/survey_form.html", values=request.form, survey=survey
        )
    return render_template("surveys/survey_form.html", values=None, survey=survey)


@bp.post("/surveys/<survey_id>/deadline")
@login_required
def update_deadline(survey_id):
    survey = _owned_survey_or_404(survey_id)
    deadline, error = parse_local_datetime(request.form.get("deadline"))
    if error:
        flash("截止时间格式不正确。", "error")
    else:
        get_db()[SURVEYS].update_one(
            {"_id": survey["_id"]},
            {"$set": {"deadline": deadline, "updated_at": utcnow()}},
        )
        flash("截止时间已更新。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.get("/surveys/<survey_id>")
@login_required
def detail(survey_id):
    survey = _owned_survey_or_404(survey_id)
    db = get_db()
    questions = _questions(survey["_id"])
    by_id = {question["_id"]: question for question in questions}
    for question in questions:
        question["settings_summary"] = question_settings_summary(question)
        question["rules_view"] = [
            describe_rule(rule, question, by_id)
            for rule in question.get("jump_rules", [])
        ]
        question["target_options"] = [
            other for other in questions if other["order"] > question["order"]
        ]
        operator_options = []
        for operator, label in JUMP_OPERATORS.get(question["type"], {}).items():
            if operator in OPTION_VALUE_OPERATORS:
                kind = "option"
            elif operator in COUNT_VALUE_OPERATORS:
                kind = "count"
            else:
                kind = "number"
            operator_options.append({"value": operator, "label": label, "kind": kind})
        question["jump_operator_options"] = operator_options

    available, unavailable_reason = is_survey_open(survey)
    return render_template(
        "surveys/detail.html",
        survey=survey,
        questions=questions,
        can_edit=survey["status"] == "draft",
        display_status=status_label(survey),
        public_url=url_for("fill.intro", public_id=survey["public_id"], _external=True),
        submission_count=count_submissions(db, survey["_id"]),
        available=available,
        unavailable_reason=unavailable_reason,
        question_types=QUESTION_TYPES,
    )


@bp.post("/surveys/<survey_id>/publish")
@login_required
def publish(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("只有草稿可以发布。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    questions = _questions(survey["_id"])
    if not questions:
        flash("请至少添加一道题目后再发布。", "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    errors = validate_all_rules(questions)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    now = utcnow()
    get_db()[SURVEYS].update_one(
        {"_id": survey["_id"]},
        {"$set": {"status": "published", "published_at": now, "updated_at": now}},
    )
    flash("问卷已发布，可以分享链接了。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/close")
@login_required
def close(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "published":
        flash("只有已发布的问卷可以关闭。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    now = utcnow()
    get_db()[SURVEYS].update_one(
        {"_id": survey["_id"]},
        {"$set": {"status": "closed", "closed_at": now, "updated_at": now}},
    )
    flash("问卷已关闭，填写链接将不可用。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.route("/surveys/<survey_id>/questions/new", methods=["GET", "POST"])
@login_required
def question_new(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能增加题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if request.method == "POST":
        fields, errors = _parse_question_form()
        if not errors:
            questions = _questions(survey["_id"])
            order = questions[-1]["order"] + 1 if questions else 1
            now = utcnow()
            get_db()[QUESTIONS].insert_one(
                {
                    "survey_id": survey["_id"],
                    "order": order,
                    "type": fields["type"],
                    "title": fields["title"],
                    "required": fields["required"],
                    "options": fields["options"],
                    "settings": fields["settings"],
                    "jump_rules": [],
                    "created_at": now,
                    "updated_at": now,
                }
            )
            flash("题目已添加。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
        for error in errors:
            flash(error, "error")
        return render_template(
            "surveys/question_form.html",
            survey=survey,
            question=None,
            values=_form_question_values(),
            question_types=QUESTION_TYPES,
        )

    return render_template(
        "surveys/question_form.html",
        survey=survey,
        question=None,
        values=_question_values(),
        question_types=QUESTION_TYPES,
    )


@bp.route(
    "/surveys/<survey_id>/questions/<question_id>/edit", methods=["GET", "POST"]
)
@login_required
def question_edit(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if request.method == "POST":
        fields, errors = _parse_question_form(existing=question)
        if not errors:
            get_db()[QUESTIONS].update_one(
                {"_id": question["_id"]},
                {
                    "$set": {
                        "title": fields["title"],
                        "required": fields["required"],
                        "options": fields["options"],
                        "settings": fields["settings"],
                        "jump_rules": _prune_rules(question, fields["options"]),
                        "updated_at": utcnow(),
                    }
                },
            )
            flash("题目已更新。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
        for error in errors:
            flash(error, "error")
        values = _form_question_values()
        values["type"] = question["type"]
        return render_template(
            "surveys/question_form.html",
            survey=survey,
            question=question,
            values=values,
            question_types=QUESTION_TYPES,
        )

    return render_template(
        "surveys/question_form.html",
        survey=survey,
        question=question,
        values=_question_values(question),
        question_types=QUESTION_TYPES,
    )


@bp.post("/surveys/<survey_id>/questions/<question_id>/delete")
@login_required
def question_delete(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能删除题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    db = get_db()
    db[QUESTIONS].delete_one({"_id": question["_id"]})
    db[QUESTIONS].update_many(
        {"survey_id": survey["_id"]},
        {"$pull": {"jump_rules": {"target": question["_id"]}}},
    )
    _renumber_questions(survey["_id"])
    flash("题目已删除，相关跳转规则已清理。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions/<question_id>/rules")
@login_required
def rule_add(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改跳转规则。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if len(question.get("jump_rules", [])) >= 20:
        flash("每道题最多设置 20 条跳转规则。", "error")
        return redirect(
            url_for(
                "surveys.detail",
                survey_id=survey["_id"],
                _anchor=f"q-{question['_id']}",
            )
        )

    questions = _questions(survey["_id"])
    rule, error = validate_jump_rule(
        question,
        request.form.get("operator"),
        request.form.get("value"),
        request.form.get("target"),
        questions,
    )
    if error:
        flash(error, "error")
    else:
        get_db()[QUESTIONS].update_one(
            {"_id": question["_id"]}, {"$push": {"jump_rules": rule}}
        )
        flash("跳转规则已添加。", "success")
    return redirect(
        url_for(
            "surveys.detail",
            survey_id=survey["_id"],
            _anchor=f"q-{question['_id']}",
        )
    )


@bp.post(
    "/surveys/<survey_id>/questions/<question_id>/rules/<int:rule_index>/delete"
)
@login_required
def rule_delete(survey_id, question_id, rule_index):
    survey = _owned_survey_or_404(survey_id)
    question = _question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改跳转规则。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    rules = question.get("jump_rules", [])
    if 0 <= rule_index < len(rules):
        rules.pop(rule_index)
        get_db()[QUESTIONS].update_one(
            {"_id": question["_id"]}, {"$set": {"jump_rules": rules}}
        )
        flash("跳转规则已删除。", "success")
    return redirect(
        url_for(
            "surveys.detail",
            survey_id=survey["_id"],
            _anchor=f"q-{question['_id']}",
        )
    )
