"""第二阶段需求测试：题目库、版本、共享、题库、跨问卷统计、发布冻结。"""

from bson import ObjectId

from db import (
    QUESTION_BANK_ITEMS,
    QUESTION_BANKS,
    QUESTION_SHARES,
    QUESTION_VERSIONS,
    QUESTIONS,
    RESPONSES,
    SURVEY_QUESTIONS,
    SURVEYS,
    USERS,
)
from stats_service import cross_question_stats
from tests.helpers import (
    answer,
    create_survey,
    login,
    logout,
    publish,
    register,
    start,
    submit,
)


def library_question(client, db, title, qtype="single", options="A\nB", **fields):
    """通过题目库创建题目，返回 (family, current_version)。"""
    data = {"type": qtype, "title": title}
    if qtype in ("single", "multiple"):
        data["options"] = options
    if fields.get("required"):
        data["required"] = "1"
    response = client.post("/library/questions/new", data=data, follow_redirects=True)
    assert response.status_code == 200, response.get_data(as_text=True)
    family = db[QUESTIONS].find_one({"title": title, "deleted_at": None})
    assert family is not None, response.get_data(as_text=True)
    version = db[QUESTION_VERSIONS].find_one({"_id": family["current_version_id"]})
    return family, version


def edit_library_question(client, db, family, title, options):
    """编辑题目 = 生成新版本，返回新版本。"""
    response = client.post(
        f"/library/questions/{family['_id']}/versions/new",
        data={"title": title, "options": options},
        follow_redirects=True,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    refreshed = db[QUESTIONS].find_one({"_id": family["_id"]})
    version = db[QUESTION_VERSIONS].find_one({"_id": refreshed["current_version_id"]})
    return version


def add_from_library(client, db, survey, family, version):
    response = client.post(
        f"/surveys/{survey['_id']}/questions",
        data={"question_id": str(family["_id"]), "version_id": str(version["_id"])},
        follow_redirects=True,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    ref = db[SURVEY_QUESTIONS].find_one(
        {"survey_id": survey["_id"], "question_id": family["_id"]}
    )
    assert ref is not None, response.get_data(as_text=True)
    return ref


def versions_of(db, family):
    return list(
        db[QUESTION_VERSIONS].find({"question_id": family["_id"]}).sort("version_no", 1)
    )


def test_save_and_reuse_question_across_surveys(client, db):
    """需求一：题目可保存并复用到多个问卷；需求六：可查看被哪些问卷使用。"""
    register(client, "alice")
    family, version = library_question(client, db, "常用性别题", options="男\n女\n其他")

    survey1 = create_survey(client, db, title="问卷一")
    survey2 = create_survey(client, db, title="问卷二")
    add_from_library(client, db, survey1, family, version)
    add_from_library(client, db, survey2, family, version)

    assert db[SURVEY_QUESTIONS].count_documents({"question_id": family["_id"]}) == 2

    detail = client.get(f"/library/questions/{family['_id']}")
    text = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "问卷一" in text and "问卷二" in text


def test_share_question_and_grantee_uses_it(client, db):
    """需求二：共享题目给其他用户，被共享方可在自己问卷中使用。"""
    register(client, "alice")
    family, version = library_question(client, db, "共享单选题", options="A\nB")

    logout(client)
    register(client, "bob")
    logout(client)
    login(client, "alice")
    response = client.post(
        f"/library/questions/{family['_id']}/share",
        data={"username": "bob"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    share = db[QUESTION_SHARES].find_one({"question_id": family["_id"]})
    assert share is not None
    assert share["can_use"] is True
    assert share["status"] == "active"

    logout(client)
    login(client, "bob")
    survey = create_survey(client, db, title="bob 的问卷")
    ref = add_from_library(client, db, survey, family, version)
    assert ref["question_id"] == family["_id"]


def test_edit_does_not_affect_published_survey(client, db):
    """需求三：已发布问卷里的题目不被修改；改题生成新版本，旧问卷保持原内容。"""
    register(client, "alice")
    family, v1 = library_question(client, db, "版本题", options="旧选项A\n旧选项B")

    survey = create_survey(client, db)
    ref = add_from_library(client, db, survey, family, v1)
    publish(client, survey)

    v2 = edit_library_question(client, db, family, "版本题（改）", "新选项A\n新选项B")

    locked_ref = db[SURVEY_QUESTIONS].find_one({"_id": ref["_id"]})
    assert locked_ref["locked"] is True
    assert locked_ref["version_id"] == v1["_id"]
    assert locked_ref["title"] == "版本题"
    assert locked_ref["options"][0]["text"] == "旧选项A"

    family_refreshed = db[QUESTIONS].find_one({"_id": family["_id"]})
    assert family_refreshed["current_version_id"] == v2["_id"]


def test_version_history_and_restore(client, db):
    """需求四：记录修改历史，可知新题从哪来，可恢复旧版本。"""
    register(client, "alice")
    family, v1 = library_question(client, db, "历史题", options="A\nB")
    v2 = edit_library_question(client, db, family, "历史题 v2", "A\nB\nC")
    v3 = edit_library_question(client, db, family, "历史题 v3", "A\nB\nC\nD")

    versions = versions_of(db, family)
    assert [v["version_no"] for v in versions] == [1, 2, 3]
    assert v2["derived_from_version_id"] == v1["_id"]
    assert v3["derived_from_version_id"] == v2["_id"]

    # 恢复 v1 → 生成 v4，内容指纹与 v1 一致
    response = client.post(
        f"/library/questions/{family['_id']}/versions/{v1['_id']}/restore",
        follow_redirects=True,
    )
    assert response.status_code == 200
    versions = versions_of(db, family)
    assert [v["version_no"] for v in versions] == [1, 2, 3, 4]
    v4 = versions[-1]
    assert v4["derived_from_version_id"] == v1["_id"]
    assert v4["content_hash"] == v1["content_hash"]


def test_fork_creates_independent_coexisting_family(client, db):
    """需求五：可基于一题派生新题，新版本可单独修改，不同问卷可用不同版本。"""
    register(client, "alice")
    family, v1 = library_question(client, db, "派生源题", options="A\nB")

    response = client.post(
        f"/library/questions/{family['_id']}/fork", follow_redirects=True
    )
    assert response.status_code == 200

    fork = db[QUESTIONS].find_one({"derived_from_question_id": family["_id"]})
    assert fork is not None
    assert fork["_id"] != family["_id"]
    fork_versions = versions_of(db, fork)
    assert len(fork_versions) == 1

    # 原题与派生题独立编辑，互不影响
    original_v2 = edit_library_question(client, db, family, "原题 v2", "A\nB\nC")
    assert db[QUESTIONS].find_one({"_id": family["_id"]})["current_version_id"] == original_v2["_id"]
    assert db[QUESTIONS].find_one({"_id": fork["_id"]})["current_version_id"] == fork_versions[0]["_id"]


def test_question_bank_manage_and_update_version(client, db):
    """需求七：题库——建库、加题、更新版本。"""
    register(client, "alice")
    family, v1 = library_question(client, db, "题库题", options="A\nB")
    v2 = edit_library_question(client, db, family, "题库题 v2", "A\nB\nC")

    response = client.post("/banks/new", data={"name": "我的题库"}, follow_redirects=True)
    assert response.status_code == 200
    bank = db[QUESTION_BANKS].find_one({"name": "我的题库"})
    assert bank is not None

    response = client.post(
        f"/banks/{bank['_id']}/items/new",
        data={"question_id": str(family["_id"])},
        follow_redirects=True,
    )
    assert response.status_code == 200
    item = db[QUESTION_BANK_ITEMS].find_one({"question_bank_id": bank["_id"]})
    assert item is not None
    assert item["current_version_id"] == v2["_id"]  # 入库存当前版本

    # 更新到旧版本 v1
    response = client.post(
        f"/banks/{bank['_id']}/items/{item['_id']}/version",
        data={"version_id": str(v1["_id"])},
        follow_redirects=True,
    )
    assert response.status_code == 200
    item = db[QUESTION_BANK_ITEMS].find_one({"_id": item["_id"]})
    assert item["current_version_id"] == v1["_id"]


def test_pinned_share_cannot_swap_to_other_version(client, db):
    """共享固定版本时，被共享方只能使用该版本，换版会被拒绝。"""
    register(client, "alice")
    family, v1 = library_question(client, db, "固定共享题", options="A\nB")

    logout(client)
    register(client, "bob")
    logout(client)
    login(client, "alice")
    # 不勾选 can_pull_latest → 固定当前版本 v1
    client.post(
        f"/library/questions/{family['_id']}/share",
        data={"username": "bob"},
        follow_redirects=True,
    )
    v2 = edit_library_question(client, db, family, "固定共享题 v2", "A\nB\nC")

    logout(client)
    login(client, "bob")
    survey = create_survey(client, db, title="bob 固定版问卷")
    ref = add_from_library(client, db, survey, family, v1)

    # 尝试换到 v2 应被拒绝，快照保持 v1
    response = client.post(
        f"/surveys/{survey['_id']}/questions/{ref['_id']}/version",
        data={"version_id": str(v2["_id"])},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "所选版本不可用" in response.get_data(as_text=True)
    unchanged = db[SURVEY_QUESTIONS].find_one({"_id": ref["_id"]})
    assert unchanged["version_id"] == v1["_id"]

    # 换版页面只列出被固定的 v1
    form = client.get(
        f"/surveys/{survey['_id']}/questions/{ref['_id']}/version"
    ).get_data(as_text=True)
    assert "v1" in form
    assert "v2" not in form


def test_revoke_grandfathers_existing_reference(client, db):
    """撤销共享只阻止新使用，已引用该题的问卷继续可用。"""
    register(client, "alice")
    family, version = library_question(client, db, "祖父条款题", options="A\nB")

    logout(client)
    register(client, "bob")
    logout(client)
    login(client, "alice")
    client.post(
        f"/library/questions/{family['_id']}/share",
        data={"username": "bob", "can_pull_latest": "1"},
        follow_redirects=True,
    )

    logout(client)
    login(client, "bob")
    survey1 = create_survey(client, db, title="已有引用问卷")
    ref = add_from_library(client, db, survey1, family, version)

    # alice 撤销共享
    logout(client)
    login(client, "alice")
    bob = db[USERS].find_one({"username": "bob"})
    client.post(
        f"/library/questions/{family['_id']}/share/revoke",
        data={"grantee_user_id": str(bob["_id"])},
        follow_redirects=True,
    )
    share = db[QUESTION_SHARES].find_one({"question_id": family["_id"]})
    assert share["status"] == "revoked"

    # 已存在的引用仍可发布、填写
    logout(client)
    login(client, "bob")
    publish(client, survey1)
    start(client, survey1)
    assert answer(client, survey1, ref, "a").status_code == 302

    # 但不能再把该题加入新问卷
    survey2 = create_survey(client, db, title="新增问卷")
    response = client.post(
        f"/surveys/{survey2['_id']}/questions",
        data={"question_id": str(family["_id"]), "version_id": str(version["_id"])},
        follow_redirects=True,
    )
    assert "没有使用权限" in response.get_data(as_text=True)
    assert db[SURVEY_QUESTIONS].count_documents({"survey_id": survey2["_id"]}) == 0


def test_cross_survey_question_stats(client, db):
    """需求八：单题跨问卷统计——同一题在多份问卷中的回答汇总。"""
    register(client, "alice")
    family, version = library_question(client, db, "跨卷统计题", options="男\n女")

    survey1 = create_survey(client, db, title="问卷一")
    survey2 = create_survey(client, db, title="问卷二")
    q1 = add_from_library(client, db, survey1, family, version)
    q2 = add_from_library(client, db, survey2, family, version)
    publish(client, survey1)
    publish(client, survey2)

    # 三个不同用户分别作答
    for username, survey, ref, key in [
        ("bob", survey1, q1, "a"),
        ("carol", survey1, q1, "b"),
        ("dave", survey2, q2, "a"),
    ]:
        logout(client)
        register(client, username)
        start(client, survey)
        answer(client, survey, ref, key)
        submit(client, survey)

    stats = cross_question_stats(db, family["_id"])
    assert stats["total_answered"] == 3
    merged = {entry["text"]: entry["count"] for entry in stats["merged"]}
    assert merged["男"] == 2
    assert merged["女"] == 1

    logout(client)
    login(client, "alice")
    response = client.get(f"/library/questions/{family['_id']}/stats")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "共 3 条回答" in text
    assert "男" in text and "女" in text
