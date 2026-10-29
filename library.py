"""题目库：题目族（question）、版本（question_version）、共享（share）。

题目族是逻辑身份；版本是不可变内容快照；问卷通过 survey_questions 引用某个版本。
编辑题目 = 生成新版本（同族升级）或派生新族（fork）；恢复旧版本 = 复制旧版本内容生成新版本。
"""

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
    SURVEY_QUESTIONS,
    SURVEYS,
    USERS,
    get_db,
)
from question_forms import (
    form_question_values,
    parse_question_form,
    question_values,
)
from utils import (
    assign_opt_ids,
    content_hash,
    fmt_dt,
    option_map_by_opt_id,
    question_settings_summary,
    to_object_id,
    utcnow,
)

bp = Blueprint("library", __name__)


# --------------------------------------------------------------------------
# 领域辅助函数（供本模块与 surveys 复用）
# --------------------------------------------------------------------------

def next_version_no(db, question_id):
    last = db[QUESTION_VERSIONS].find_one(
        {"question_id": question_id}, sort=[("version_no", -1)]
    )
    return (last["version_no"] + 1) if last else 1


def create_version(db, question, content, base_version_id=None):
    """在题目族内新建不可变版本，更新族 current_version_id，返回版本文档。"""
    version_no = next_version_no(db, question["_id"])
    doc = {
        "question_id": question["_id"],
        "version_no": version_no,
        "title": content["title"],
        "type": content["type"],
        "required": content["required"],
        "options": content["options"],
        "settings": content["settings"],
        "content_hash": content_hash(
            content["title"],
            content["type"],
            content["required"],
            content["options"],
            content["settings"],
        ),
        "derived_from_version_id": base_version_id,
        "created_at": utcnow(),
        "deleted_at": None,
    }
    result = db[QUESTION_VERSIONS].insert_one(doc)
    version = db[QUESTION_VERSIONS].find_one({"_id": result.inserted_id})
    db[QUESTIONS].update_one(
        {"_id": question["_id"]},
        {
            "$set": {
                "current_version_id": version["_id"],
                "title": version["title"],
                "type": version["type"],
                "updated_at": utcnow(),
            }
        },
    )
    return version


def versions_of(db, question_id):
    return list(
        db[QUESTION_VERSIONS].find({"question_id": question_id}).sort("version_no", 1)
    )


def resolve_share_version(db, question, share):
    """解析共享版本：can_pull_latest 取当前版本，否则取 pinned 版本。"""
    version_id = (
        question["current_version_id"]
        if share.get("can_pull_latest")
        else share.get("pinned_version_id")
    )
    return db[QUESTION_VERSIONS].find_one({"_id": version_id}) if version_id else None


def available_question_ids(db, user_id):
    """当前用户可用题目族：自己的 + 被共享且 active 且 can_use 的。"""
    owned = {q["_id"] for q in db[QUESTIONS].find({"owner_id": user_id})}
    shared = {
        s["question_id"]
        for s in db[QUESTION_SHARES].find(
            {"grantee_user_id": user_id, "status": "active", "can_use": True}
        )
    }
    return owned | shared


def _owned_question_or_404(question_id):
    oid = to_object_id(question_id)
    if oid is None:
        abort(404)
    question = get_db()[QUESTIONS].find_one({"_id": oid, "owner_id": g.user["_id"]})
    if question is None or question.get("deleted_at") is not None:
        abort(404)
    return question


def _version_or_404(question_id, version_id):
    vid = to_object_id(version_id)
    if vid is None:
        abort(404)
    version = get_db()[QUESTION_VERSIONS].find_one(
        {"_id": vid, "question_id": question_id}
    )
    if version is None:
        abort(404)
    return version


def _edge_type(db, version):
    """沿派生指针的边类型：根 / 升级 / 恢复（按 content_hash 相等推断）。"""
    parent_id = version.get("derived_from_version_id")
    if parent_id is None:
        return "root"
    parent = db[QUESTION_VERSIONS].find_one({"_id": parent_id})
    if parent is not None and parent.get("content_hash") == version.get("content_hash"):
        return "restored"
    return "derived"


def _usage_of(db, question_id):
    """题目被哪些问卷使用（区分草稿/发布/关闭）。"""
    refs = list(db[SURVEY_QUESTIONS].find({"question_id": question_id}))
    survey_ids = [r["survey_id"] for r in refs]
    surveys = {
        s["_id"]: s
        for s in db[SURVEYS].find({"_id": {"$in": survey_ids}})
        if survey_ids
    }
    rows = []
    for ref in refs:
        survey = surveys.get(ref["survey_id"])
        if survey is None:
            continue
        rows.append(
            {
                "survey": survey,
                "survey_question": ref,
                "version": db[QUESTION_VERSIONS].find_one({"_id": ref["version_id"]}),
            }
        )
    return rows


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------

@bp.get("/library")
@login_required
def index():
    db = get_db()
    questions = list(
        db[QUESTIONS].find({"owner_id": g.user["_id"], "deleted_at": None}).sort(
            "updated_at", -1
        )
    )
    for question in questions:
        question["current_version"] = db[QUESTION_VERSIONS].find_one(
            {"_id": question["current_version_id"]}
        )
        question["usage_count"] = db[SURVEY_QUESTIONS].count_documents(
            {"question_id": question["_id"]}
        )
    return render_template("library/index.html", questions=questions)


@bp.route("/library/questions/new", methods=["GET", "POST"])
@login_required
def question_new():
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
            create_version(db, question, fields)
            flash("题目已保存到题目库。", "success")
            return redirect(url_for("library.question_detail", question_id=question["_id"]))
        for error in errors:
            flash(error, "error")
        return render_template(
            "library/question_form.html",
            question=None,
            values=form_question_values(),
            mode="new",
        )
    return render_template(
        "library/question_form.html", question=None, values=question_values(), mode="new"
    )


@bp.get("/library/questions/<question_id>")
@login_required
def question_detail(question_id):
    question = _owned_question_or_404(question_id)
    db = get_db()
    versions = versions_of(db, question["_id"])
    by_id = {v["_id"]: v for v in versions}
    for version in versions:
        version["edge_type"] = _edge_type(db, version)
        parent_id = version.get("derived_from_version_id")
        version["derived_from_no"] = (
            by_id[parent_id]["version_no"] if parent_id in by_id else None
        )
        version["hash_short"] = (version["content_hash"] or "")[:10]
        version["settings_summary"] = question_settings_summary(version)

    current = by_id.get(question["current_version_id"])

    shares = list(db[QUESTION_SHARES].find({"question_id": question["_id"]}))
    grantee_ids = [s["grantee_user_id"] for s in shares]
    users = {u["_id"]: u for u in db[USERS].find({"_id": {"$in": grantee_ids}})}
    for share in shares:
        share["grantee"] = users.get(share["grantee_user_id"], {})
        if share.get("pinned_version_id"):
            pinned = by_id.get(share["pinned_version_id"])
            share["pinned_no"] = pinned["version_no"] if pinned else None
        else:
            share["pinned_no"] = None

    return render_template(
        "library/question_detail.html",
        question=question,
        current=current,
        versions=versions,
        usage=_usage_of(db, question["_id"]),
        shares=shares,
    )


@bp.route("/library/questions/<question_id>/versions/new", methods=["GET", "POST"])
@login_required
def version_new(question_id):
    question = _owned_question_or_404(question_id)
    db = get_db()
    current = db[QUESTION_VERSIONS].find_one({"_id": question["current_version_id"]})

    if request.method == "POST":
        fields, errors = parse_question_form(existing=current)
        if not errors:
            fields["options"] = assign_opt_ids(fields["options"], current["options"])
            create_version(db, question, fields, base_version_id=current["_id"])
            flash("已生成新版本。", "success")
            return redirect(url_for("library.question_detail", question_id=question["_id"]))
        for error in errors:
            flash(error, "error")
        return render_template(
            "library/question_form.html",
            question=question,
            values=form_question_values(),
            mode="version",
        )
    return render_template(
        "library/question_form.html",
        question=question,
        values=question_values(current),
        mode="version",
    )


@bp.post("/library/questions/<question_id>/fork")
@login_required
def fork(question_id):
    question = _owned_question_or_404(question_id)
    db = get_db()
    current = db[QUESTION_VERSIONS].find_one({"_id": question["current_version_id"]})
    if current is None:
        flash("当前版本不存在，无法派生。", "error")
        return redirect(url_for("library.question_detail", question_id=question["_id"]))

    now = utcnow()
    result = db[QUESTIONS].insert_one(
        {
            "owner_id": g.user["_id"],
            "title": current["title"],
            "type": current["type"],
            "current_version_id": None,
            "derived_from_question_id": question["_id"],
            "derived_from_version_id": current["_id"],
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    fork_question = db[QUESTIONS].find_one({"_id": result.inserted_id})
    content = {
        "title": current["title"],
        "type": current["type"],
        "required": current["required"],
        # fork 复制内容并沿用 opt_id；统计聚合键是 question_id，不会跨族误合并
        "options": [dict(o) for o in current.get("options", [])],
        "settings": current.get("settings") or {},
    }
    create_version(db, fork_question, content)
    flash("已派生为新的独立题目，可在题库中继续编辑。", "success")
    return redirect(url_for("library.question_detail", question_id=fork_question["_id"]))


@bp.post("/library/questions/<question_id>/versions/<version_id>/restore")
@login_required
def restore(question_id, version_id):
    question = _owned_question_or_404(question_id)
    db = get_db()
    target = _version_or_404(question["_id"], version_id)
    if target["_id"] == question.get("current_version_id"):
        flash("该版本已是当前版本，无需恢复。", "warning")
        return redirect(url_for("library.question_detail", question_id=question["_id"]))

    content = {
        "title": target["title"],
        "type": target["type"],
        "required": target["required"],
        "options": [dict(o) for o in target.get("options", [])],
        "settings": target.get("settings") or {},
    }
    create_version(db, question, content, base_version_id=target["_id"])
    flash(f"已恢复 v{target['version_no']} 的内容并生成新版本。", "success")
    return redirect(url_for("library.question_detail", question_id=question["_id"]))


@bp.post("/library/questions/<question_id>/delete")
@login_required
def delete(question_id):
    question = _owned_question_or_404(question_id)
    get_db()[QUESTIONS].update_one(
        {"_id": question["_id"]}, {"$set": {"deleted_at": utcnow()}}
    )
    flash("题目已删除（已引用它的问卷不受影响）。", "success")
    return redirect(url_for("library.index"))


@bp.route("/library/questions/<question_id>/share", methods=["GET", "POST"])
@login_required
def share(question_id):
    question = _owned_question_or_404(question_id)
    db = get_db()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        can_pull_latest = bool(request.form.get("can_pull_latest"))
        grantee = db[USERS].find_one({"username": username})
        if grantee is None:
            flash("用户不存在。", "error")
        elif grantee["_id"] == g.user["_id"]:
            flash("不能把题目共享给自己。", "error")
        else:
            now = utcnow()
            doc = {
                "question_id": question["_id"],
                "grantor_user_id": g.user["_id"],
                "grantee_user_id": grantee["_id"],
                "can_use": True,
                "can_pull_latest": can_pull_latest,
                "pinned_version_id": (
                    None if can_pull_latest else question["current_version_id"]
                ),
                "status": "active",
                "granted_at": now,
                "revoked_at": None,
            }
            try:
                db[QUESTION_SHARES].insert_one(doc)
            except DuplicateKeyError:
                flash("已共享给该用户，如需调整请先撤销后重新共享。", "warning")
            else:
                flash(f"已共享给 {username}。", "success")
            return redirect(url_for("library.question_detail", question_id=question["_id"]))

    shares = list(db[QUESTION_SHARES].find({"question_id": question["_id"]}))
    grantee_ids = [s["grantee_user_id"] for s in shares]
    users = {u["_id"]: u for u in db[USERS].find({"_id": {"$in": grantee_ids}})}
    for share in shares:
        share["grantee"] = users.get(share["grantee_user_id"], {})
    return render_template(
        "library/share_form.html", question=question, shares=shares
    )


@bp.post("/library/questions/<question_id>/share/revoke")
@login_required
def share_revoke(question_id):
    question = _owned_question_or_404(question_id)
    grantee_id = to_object_id(request.form.get("grantee_user_id"))
    if grantee_id is not None:
        get_db()[QUESTION_SHARES].update_one(
            {"question_id": question["_id"], "grantee_user_id": grantee_id},
            {"$set": {"status": "revoked", "revoked_at": utcnow()}},
        )
        flash("共享已撤销。", "success")
    return redirect(url_for("library.share", question_id=question["_id"]))


@bp.get("/library/shares")
@login_required
def received_shares():
    db = get_db()
    shares = list(
        db[QUESTION_SHARES].find(
            {"grantee_user_id": g.user["_id"], "status": "active"}
        )
    )
    question_ids = [s["question_id"] for s in shares]
    questions = {
        q["_id"]: q
        for q in db[QUESTIONS].find({"_id": {"$in": question_ids}})
        if question_ids
    }
    grantor_ids = [s["grantor_user_id"] for s in shares]
    users = {u["_id"]: u for u in db[USERS].find({"_id": {"$in": grantor_ids}})}
    for share in shares:
        question = questions.get(share["question_id"])
        share["question"] = question
        share["grantor"] = users.get(share["grantor_user_id"], {})
        if question:
            share["resolved_version"] = resolve_share_version(db, question, share)
    return render_template("library/shares.html", shares=shares)


# 跨问卷统计：复用 stats_service
@bp.get("/library/questions/<question_id>/stats")
@login_required
def question_stats(question_id):
    question = _owned_question_or_404(question_id)
    from stats_service import cross_question_stats

    result = cross_question_stats(get_db(), question["_id"])
    return render_template(
        "library/question_stats.html", question=question, stats=result
    )
