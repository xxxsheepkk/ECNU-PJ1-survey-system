"""问卷与题目引用管理（创建者侧）。

第二阶段：题目统一存在于题目库（questions 族 + question_versions 版本），
问卷通过 survey_questions 引用某个版本并冻结快照；发布后锁定引用。
"""

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
from db import (
    QUESTION_SHARES,
    QUESTION_VERSIONS,
    QUESTIONS,
    RESPONSES,
    SURVEY_QUESTIONS,
    SURVEYS,
    get_db,
)
from library import create_version, versions_of
from logic import describe_rule, validate_all_rules, validate_jump_rule
from question_forms import (
    form_question_values,
    parse_question_form,
    question_values,
)
from stats_service import count_submissions
from utils import (
    COUNT_VALUE_OPERATORS,
    JUMP_OPERATORS,
    OPTION_VALUE_OPERATORS,
    QUESTION_TYPES,
    assign_opt_ids,
    is_survey_open,
    parse_local_datetime,
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


def _survey_questions(survey_id):
    return list(
        get_db()[SURVEY_QUESTIONS].find({"survey_id": survey_id}).sort("order", 1)
    )


def _survey_question_or_404(survey_id, question_id):
    qid = to_object_id(question_id)
    if qid is None:
        abort(404)
    question = get_db()[SURVEY_QUESTIONS].find_one(
        {"_id": qid, "survey_id": survey_id}
    )
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


def _make_survey_question(survey_id, question, version, order):
    """由题目族 + 版本构造问卷引用（冻结快照平铺）。"""
    return {
        "survey_id": survey_id,
        "question_id": question["_id"],
        "version_id": version["_id"],
        "order": order,
        "title": version["title"],
        "type": version["type"],
        "required": version["required"],
        "options": version["options"],
        "settings": version["settings"],
        "jump_rules": [],
        "locked": False,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }


def _refresh_snapshot(db, survey_question, version):
    db[SURVEY_QUESTIONS].update_one(
        {"_id": survey_question["_id"]},
        {
            "$set": {
                "version_id": version["_id"],
                "title": version["title"],
                "type": version["type"],
                "required": version["required"],
                "options": version["options"],
                "settings": version["settings"],
                "jump_rules": [],
                "updated_at": utcnow(),
            }
        },
    )


def _prune_rules(question, options):
    keys = [opt["key"] for opt in options]
    kept = []
    for rule in question.get("jump_rules", []):
        condition = rule.get("condition") or {}
        operator = condition.get("operator")
        value = condition.get("value")
        if operator in OPTION_VALUE_OPERATORS and value not in keys and value not in [
            opt["opt_id"] for opt in options
        ]:
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
    questions = _survey_questions(survey_id)
    for question in questions:
        db[SURVEY_QUESTIONS].update_one(
            {"_id": question["_id"]},
            {"$set": {"order": question["order"] + 10000}},
        )
    for index, question in enumerate(questions, start=1):
        db[SURVEY_QUESTIONS].update_one(
            {"_id": question["_id"]}, {"$set": {"order": index}}
        )


def _attach_meta(db, survey_question):
    """附加题目族与版本号，供页面展示。"""
    version = db[QUESTION_VERSIONS].find_one({"_id": survey_question["version_id"]})
    survey_question["version_no"] = version["version_no"] if version else None
    return survey_question


def _question_access(db, question_id, user_id):
    """返回 (question, share)；share 为 None 表示所有者；无权限返回 (None, None)。"""
    question = db[QUESTIONS].find_one({"_id": question_id, "deleted_at": None})
    if question is None:
        return None, None
    if question["owner_id"] == user_id:
        return question, None
    share = db[QUESTION_SHARES].find_one(
        {
            "question_id": question_id,
            "grantee_user_id": user_id,
            "status": "active",
            "can_use": True,
        }
    )
    if share is None:
        return None, None
    return question, share


def _allowed_versions(db, family, question_id):
    """可换版本范围：所有者看全部；共享题目按共享约定（跟随最新 / 固定版本）。

    返回 (versions, error)。error 非空表示当前用户无权使用该题目。
    """
    if family is None:
        return [], "题目不存在。"
    if family["owner_id"] == g.user["_id"]:
        return versions_of(db, question_id), None

    share = db[QUESTION_SHARES].find_one(
        {
            "question_id": question_id,
            "grantee_user_id": g.user["_id"],
            "status": "active",
            "can_use": True,
        }
    )
    if share is None:
        return [], "没有使用该题目的权限。"
    if share.get("can_pull_latest"):
        return versions_of(db, question_id), None
    pinned = db[QUESTION_VERSIONS].find_one({"_id": share.get("pinned_version_id")})
    if pinned is None:
        return [], "共享题目已锁定版本，但该版本不可用。"
    return [pinned], None


def _pickable_questions(db, user_id):
    """可选题列表：我的题目 + 共享给我的题目。返回 [{question, source, share, versions}]。"""
    entries = []
    owned = list(db[QUESTIONS].find({"owner_id": user_id, "deleted_at": None}))
    for question in owned:
        entries.append(
            {
                "question": question,
                "source": "我的题目",
                "share": None,
                "versions": versions_of(db, question["_id"]),
            }
        )
    shares = list(
        db[QUESTION_SHARES].find(
            {"grantee_user_id": user_id, "status": "active", "can_use": True}
        )
    )
    question_ids = [s["question_id"] for s in shares]
    questions = {
        q["_id"]: q
        for q in db[QUESTIONS].find({"_id": {"$in": question_ids}, "deleted_at": None})
    }
    for share in shares:
        question = questions.get(share["question_id"])
        if question is None:
            continue
        if share.get("can_pull_latest"):
            versions = versions_of(db, question["_id"])
        else:
            pinned = db[QUESTION_VERSIONS].find_one(
                {"_id": share.get("pinned_version_id")}
            )
            versions = [pinned] if pinned else []
        entries.append(
            {"question": question, "source": "共享给我", "share": share, "versions": versions}
        )
    return entries


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
    questions = [_attach_meta(db, q) for q in _survey_questions(survey["_id"])]
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
    questions = _survey_questions(survey["_id"])
    if not questions:
        flash("请至少添加一道题目后再发布。", "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    errors = validate_all_rules(questions)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    now = utcnow()
    db = get_db()
    db[SURVEYS].update_one(
        {"_id": survey["_id"]},
        {"$set": {"status": "published", "published_at": now, "updated_at": now}},
    )
    db[SURVEY_QUESTIONS].update_many(
        {"survey_id": survey["_id"], "locked": False}, {"$set": {"locked": True}}
    )
    flash("问卷已发布，题目已锁定，可以分享链接了。", "success")
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


# --------------------------------------------------------------------------
# 添加题目：从题库/题目库选择
# --------------------------------------------------------------------------

@bp.get("/surveys/<survey_id>/questions/pick")
@login_required
def question_pick(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能增加题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    db = get_db()
    entries = _pickable_questions(db, g.user["_id"])
    existing = {
        q["question_id"] for q in db[SURVEY_QUESTIONS].find({"survey_id": survey["_id"]})
    }
    for entry in entries:
        entry["already_added"] = entry["question"]["_id"] in existing
        entry["current_version_id"] = entry["question"]["current_version_id"]
    return render_template(
        "surveys/question_pick.html", survey=survey, entries=entries
    )


@bp.post("/surveys/<survey_id>/questions")
@login_required
def question_add(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能增加题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    db = get_db()
    question_id = to_object_id(request.form.get("question_id"))
    version_id = to_object_id(request.form.get("version_id"))
    question, share = _question_access(db, question_id, g.user["_id"])
    if question is None:
        flash("题目不存在或没有使用权限。", "error")
        return redirect(url_for("surveys.question_pick", survey_id=survey["_id"]))

    version = db[QUESTION_VERSIONS].find_one(
        {"_id": version_id, "question_id": question["_id"]}
    )
    if version is None:
        flash("所选版本不属于该题目。", "error")
        return redirect(url_for("surveys.question_pick", survey_id=survey["_id"]))

    if share is not None and not share.get("can_pull_latest"):
        if version_id != share.get("pinned_version_id"):
            flash("该共享题目已锁定版本，不能选择其他版本。", "error")
            return redirect(url_for("surveys.question_pick", survey_id=survey["_id"]))

    questions = _survey_questions(survey["_id"])
    order = questions[-1]["order"] + 1 if questions else 1
    doc = _make_survey_question(survey["_id"], question, version, order)
    try:
        db[SURVEY_QUESTIONS].insert_one(doc)
    except DuplicateKeyError:
        flash("该题目已在本问卷中。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    flash("题目已添加。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.route("/surveys/<survey_id>/questions/new", methods=["GET", "POST"])
@login_required
def question_new(survey_id):
    survey = _owned_survey_or_404(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能增加题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if request.method == "POST":
        fields, errors = parse_question_form()
        if not errors:
            db = get_db()
            now = utcnow()
            result = db[QUESTIONS].insert_one(
                {
                    "owner_id": g.user["_id"],
                    "title": fields["title"],
                    "type": fields["type"],
                    "current_version_id": None,
                    "derived_from_question_id": None,
                    "derived_from_version_id": None,
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            question = db[QUESTIONS].find_one({"_id": result.inserted_id})
            fields["options"] = assign_opt_ids(fields["options"])
            version = create_version(db, question, fields)

            questions = _survey_questions(survey["_id"])
            order = questions[-1]["order"] + 1 if questions else 1
            db[SURVEY_QUESTIONS].insert_one(
                _make_survey_question(survey["_id"], question, version, order)
            )
            flash("题目已添加（并保存到题目库）。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
        for error in errors:
            flash(error, "error")
        return render_template(
            "surveys/question_form.html",
            survey=survey,
            question=None,
            values=form_question_values(),
            question_types=QUESTION_TYPES,
        )

    return render_template(
        "surveys/question_form.html",
        survey=survey,
        question=None,
        values=question_values(),
        question_types=QUESTION_TYPES,
    )


# --------------------------------------------------------------------------
# 编辑 / 换版 / 删除（草稿态）
# --------------------------------------------------------------------------

@bp.route(
    "/surveys/<survey_id>/questions/<question_id>/edit", methods=["GET", "POST"]
)
@login_required
def question_edit(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _survey_question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    db = get_db()
    family = db[QUESTIONS].find_one({"_id": question["question_id"]})
    if family is None or family["owner_id"] != g.user["_id"]:
        flash("共享题目不可直接编辑，请在题目库中由所有者更新后换版。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    if request.method == "POST":
        fields, errors = parse_question_form(existing=question)
        if not errors:
            fields["options"] = assign_opt_ids(fields["options"], question["options"])
            version = create_version(db, family, fields, base_version_id=question["version_id"])
            db[SURVEY_QUESTIONS].update_one(
                {"_id": question["_id"]},
                {
                    "$set": {
                        "version_id": version["_id"],
                        "title": version["title"],
                        "type": version["type"],
                        "required": version["required"],
                        "options": version["options"],
                        "settings": version["settings"],
                        "jump_rules": _prune_rules(question, version["options"]),
                        "updated_at": utcnow(),
                    }
                },
            )
            flash("已生成新版本并更新本题。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
        for error in errors:
            flash(error, "error")
        values = form_question_values()
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
        values=question_values(question),
        question_types=QUESTION_TYPES,
    )


@bp.get("/surveys/<survey_id>/questions/<question_id>/version")
@login_required
def question_version_form(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _survey_question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft" or question.get("locked"):
        flash("题目已锁定，不能换版。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    db = get_db()
    family = db[QUESTIONS].find_one({"_id": question["question_id"]})
    versions, error = _allowed_versions(db, family, question["question_id"])
    if error:
        flash(error, "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))
    return render_template(
        "surveys/question_version.html",
        survey=survey,
        question=question,
        family=family,
        versions=versions,
    )


@bp.post("/surveys/<survey_id>/questions/<question_id>/version")
@login_required
def question_version(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _survey_question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft" or question.get("locked"):
        flash("题目已锁定，不能换版。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    db = get_db()
    family = db[QUESTIONS].find_one({"_id": question["question_id"]})
    allowed, error = _allowed_versions(db, family, question["question_id"])
    if error:
        flash(error, "error")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    version_id = to_object_id(request.form.get("version_id"))
    version = next((v for v in allowed if v["_id"] == version_id), None)
    if version is None:
        flash("所选版本不可用（共享题目可能已锁定版本）。", "error")
        return redirect(
            url_for(
                "surveys.question_version_form",
                survey_id=survey["_id"],
                question_id=question["_id"],
            )
        )
    _refresh_snapshot(db, question, version)
    flash(f"已换到版本 v{version['version_no']}。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/questions/<question_id>/delete")
@login_required
def question_delete(survey_id, question_id):
    survey = _owned_survey_or_404(survey_id)
    question = _survey_question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能删除题目。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    db = get_db()
    db[SURVEY_QUESTIONS].delete_one({"_id": question["_id"]})
    db[SURVEY_QUESTIONS].update_many(
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
    question = _survey_question_or_404(survey["_id"], question_id)
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

    questions = _survey_questions(survey["_id"])
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
        get_db()[SURVEY_QUESTIONS].update_one(
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
    question = _survey_question_or_404(survey["_id"], question_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能修改跳转规则。", "warning")
        return redirect(url_for("surveys.detail", survey_id=survey["_id"]))

    rules = question.get("jump_rules", [])
    if 0 <= rule_index < len(rules):
        rules.pop(rule_index)
        get_db()[SURVEY_QUESTIONS].update_one(
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
