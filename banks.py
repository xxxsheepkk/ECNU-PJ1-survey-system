"""题库：question_banks 与 question_bank_items（N:M 关联题目族）。"""

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
    QUESTION_BANK_ITEMS,
    QUESTION_BANKS,
    QUESTION_VERSIONS,
    QUESTIONS,
    get_db,
)
from utils import to_object_id, utcnow

bp = Blueprint("banks", __name__)


def _owned_bank_or_404(bank_id):
    oid = to_object_id(bank_id)
    if oid is None:
        abort(404)
    bank = get_db()[QUESTION_BANKS].find_one({"_id": oid, "owner_id": g.user["_id"]})
    if bank is None:
        abort(404)
    return bank


def _item_or_404(bank_id, item_id):
    iid = to_object_id(item_id)
    if iid is None:
        abort(404)
    item = get_db()[QUESTION_BANK_ITEMS].find_one(
        {"_id": iid, "question_bank_id": to_object_id(bank_id)}
    )
    if item is None:
        abort(404)
    return item


@bp.get("/banks")
@login_required
def index():
    db = get_db()
    banks = list(db[QUESTION_BANKS].find({"owner_id": g.user["_id"]}).sort("created_at", -1))
    for bank in banks:
        bank["item_count"] = db[QUESTION_BANK_ITEMS].count_documents(
            {"question_bank_id": bank["_id"]}
        )
    return render_template("banks/index.html", banks=banks)


@bp.post("/banks/new")
@login_required
def create():
    name = request.form.get("name", "").strip()
    if not name:
        flash("请填写题库名称。", "error")
    elif len(name) > 80:
        flash("题库名称最多 80 个字符。", "error")
    else:
        now = utcnow()
        result = get_db()[QUESTION_BANKS].insert_one(
            {"owner_id": g.user["_id"], "name": name, "created_at": now, "updated_at": now}
        )
        flash("题库已创建。", "success")
        return redirect(url_for("banks.detail", bank_id=result.inserted_id))
    return redirect(url_for("banks.index"))


@bp.get("/banks/<bank_id>")
@login_required
def detail(bank_id):
    bank = _owned_bank_or_404(bank_id)
    db = get_db()
    items = list(db[QUESTION_BANK_ITEMS].find({"question_bank_id": bank["_id"]}))
    question_ids = [i["question_id"] for i in items]
    questions = {
        q["_id"]: q
        for q in db[QUESTIONS].find({"_id": {"$in": question_ids}})
        if question_ids
    }
    version_ids = [i["current_version_id"] for i in items] + [
        q.get("current_version_id") for q in questions.values()
    ]
    versions = {
        v["_id"]: v
        for v in db[QUESTION_VERSIONS].find({"_id": {"$in": version_ids}})
        if version_ids
    }
    all_versions = {}
    if question_ids:
        for v in db[QUESTION_VERSIONS].find(
            {"question_id": {"$in": question_ids}}
        ).sort("version_no", -1):
            all_versions.setdefault(v["question_id"], []).append(v)
    for item in items:
        item["question"] = questions.get(item["question_id"])
        item["current_version"] = versions.get(item["current_version_id"])
        item["version_options"] = all_versions.get(item["question_id"], [])

    # 供“加入题库”选择：我的题目
    candidates = list(db[QUESTIONS].find({"owner_id": g.user["_id"], "deleted_at": None}))
    for candidate in candidates:
        candidate["current_version"] = versions.get(candidate["current_version_id"])

    return render_template(
        "banks/detail.html",
        bank=bank,
        items=items,
        candidates=candidates,
    )


@bp.post("/banks/<bank_id>/items/new")
@login_required
def item_add(bank_id):
    bank = _owned_bank_or_404(bank_id)
    db = get_db()
    question_id = to_object_id(request.form.get("question_id"))
    question = db[QUESTIONS].find_one(
        {"_id": question_id, "owner_id": g.user["_id"], "deleted_at": None}
    )
    if question is None:
        flash("题目不存在或无权加入题库。", "error")
    else:
        now = utcnow()
        try:
            db[QUESTION_BANK_ITEMS].insert_one(
                {
                    "question_bank_id": bank["_id"],
                    "question_id": question["_id"],
                    "current_version_id": question["current_version_id"],
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except DuplicateKeyError:
            flash("该题目已在此题库中。", "warning")
        else:
            flash("题目已加入题库。", "success")
    return redirect(url_for("banks.detail", bank_id=bank["_id"]))


@bp.post("/banks/<bank_id>/items/<item_id>/version")
@login_required
def item_version(bank_id, item_id):
    bank = _owned_bank_or_404(bank_id)
    item = _item_or_404(bank_id, item_id)
    db = get_db()
    version_id = to_object_id(request.form.get("version_id"))
    version = db[QUESTION_VERSIONS].find_one(
        {"_id": version_id, "question_id": item["question_id"]}
    )
    if version is None:
        flash("版本不属于该题目。", "error")
    else:
        db[QUESTION_BANK_ITEMS].update_one(
            {"_id": item["_id"]},
            {"$set": {"current_version_id": version["_id"], "updated_at": utcnow()}},
        )
        flash("题库条目已更新版本（已引用该题的问卷不受影响）。", "success")
    return redirect(url_for("banks.detail", bank_id=bank["_id"]))


@bp.post("/banks/<bank_id>/items/<item_id>/delete")
@login_required
def item_delete(bank_id, item_id):
    bank = _owned_bank_or_404(bank_id)
    item = _item_or_404(bank_id, item_id)
    get_db()[QUESTION_BANK_ITEMS].delete_one({"_id": item["_id"]})
    flash("已从题库移除该题。", "success")
    return redirect(url_for("banks.detail", bank_id=bank["_id"]))
