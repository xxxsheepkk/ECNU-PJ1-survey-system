"""真实 HTTP 冒烟测试：注册 → 建卷 → 加题 → 跳转 → 发布 → 实名/匿名填写 → 统计。

运行前先启动应用，例如：
    $env:PORT = "5001"; python app.py
然后：
    python tools/e2e_smoke.py
"""

import os
import re
import secrets
import sys
from pathlib import Path

import requests
from bson import ObjectId
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from db import (  # noqa: E402
    QUESTION_SHARES,
    QUESTION_VERSIONS,
    QUESTIONS,
    RESPONSES,
    SURVEY_QUESTIONS,
    SURVEYS,
    USERS,
)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5001").rstrip("/")
CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
PUBLIC_ID_RE = re.compile(r"/survey/([A-Za-z0-9_-]+)")
QUESTION_ID_RE = re.compile(r"/questions/([0-9a-f]{24})/edit")
CURRENT_QID_RE = re.compile(r'name="question_id" value="([0-9a-f]{24})"')

passed = 0


def check(condition, label, detail=""):
    global passed
    if not condition:
        raise AssertionError(f"FAIL  {label}\n{detail}")
    passed += 1
    print(f"  PASS  {label}")


def csrf(session, url):
    response = session.get(url)
    response.raise_for_status()
    match = CSRF_RE.search(response.text)
    if not match:
        raise AssertionError(f"页面没有 CSRF token：{url}")
    return match.group(1)


def register(session, username):
    token = csrf(session, f"{BASE_URL}/register")
    response = session.post(
        f"{BASE_URL}/register",
        data={
            "_csrf": token,
            "username": username,
            "password": "e2e-pass-123",
            "confirm_password": "e2e-pass-123",
        },
    )
    response.raise_for_status()
    check("注册成功" in response.text or "我的问卷" in response.text, f"注册 {username}")


def main():
    suffix = secrets.token_hex(3)
    owner_name = f"e2e_owner_{suffix}"
    user1_name = f"e2e_user1_{suffix}"
    user2_name = f"e2e_user2_{suffix}"
    survey_id = None

    owner = requests.Session()
    try:
        print("\n1. 创建者注册与问卷管理")
        register(owner, owner_name)

        token = csrf(owner, f"{BASE_URL}/surveys/new")
        response = owner.post(
            f"{BASE_URL}/surveys/new",
            data={
                "_csrf": token,
                "title": f"E2E 问卷 {suffix}",
                "description": "真实 HTTP 冒烟测试",
                "allow_anonymous": "1",
                "allow_multiple": "1",
                "deadline": "",
            },
            allow_redirects=False,
        )
        check(response.status_code in (302, 303), "创建问卷")
        survey_id = response.headers["Location"].rstrip("/").split("/")[-1]

        detail_url = f"{BASE_URL}/surveys/{survey_id}"
        for title, qtype, options in [
            ("第一题（跳转条件）", "single", "A\nB"),
            ("第二题（应被跳过）", "text", ""),
            ("第三题（跳转目标）", "text", ""),
        ]:
            token = csrf(owner, detail_url)
            data = {"_csrf": token, "type": qtype, "title": title}
            if options:
                data["options"] = options
            response = owner.post(
                f"{detail_url}/questions/new", data=data, allow_redirects=True
            )
            response.raise_for_status()
            check(title in response.text, f"添加题目：{title}")

        detail = owner.get(detail_url)
        detail.raise_for_status()
        question_ids = QUESTION_ID_RE.findall(detail.text)
        check(len(question_ids) == 3, "详情页包含 3 道题目", detail.text[:500])

        token = CSRF_RE.search(detail.text).group(1)
        response = owner.post(
            f"{detail_url}/questions/{question_ids[0]}/rules",
            data={
                "_csrf": token,
                "operator": "equals",
                "value": "a",
                "target": question_ids[2],
            },
            allow_redirects=True,
        )
        response.raise_for_status()
        check("跳转规则已添加" in response.text, "添加单选跳转规则")

        token = CSRF_RE.search(response.text).group(1)
        response = owner.post(
            f"{detail_url}/publish",
            data={"_csrf": token},
            allow_redirects=True,
        )
        response.raise_for_status()
        check("问卷已发布" in response.text, "发布问卷")

        match = PUBLIC_ID_RE.search(response.text)
        check(match is not None, "解析公开链接")
        public_id = match.group(1)

        print("\n2. 实名用户填写（含跳转）")
        user1 = requests.Session()
        register(user1, user1_name)
        token = csrf(user1, f"{BASE_URL}/survey/{public_id}")
        response = user1.post(
            f"{BASE_URL}/survey/{public_id}/start",
            data={"_csrf": token},
        )
        response.raise_for_status()
        check("第一题" in response.text, "开始填写并进入答题页")

        qid = CURRENT_QID_RE.search(response.text).group(1)
        token = CSRF_RE.search(response.text).group(1)
        response = user1.post(
            f"{BASE_URL}/survey/{public_id}/fill/answer",
            data={"_csrf": token, "question_id": qid, "value": "a"},
        )
        response.raise_for_status()
        check("第三题" in response.text, "选择 A 后跳到第三题")
        check("第二题" not in response.text, "第二题被跳过")

        qid = CURRENT_QID_RE.search(response.text).group(1)
        token = CSRF_RE.search(response.text).group(1)
        response = user1.post(
            f"{BASE_URL}/survey/{public_id}/fill/answer",
            data={"_csrf": token, "question_id": qid, "value": "实名答案"},
        )
        response.raise_for_status()
        check("检查并提交" in response.text, "答完后进入检查页")

        token = CSRF_RE.search(response.text).group(1)
        response = user1.post(
            f"{BASE_URL}/survey/{public_id}/review/submit",
            data={"_csrf": token},
        )
        response.raise_for_status()
        check("提交成功" in response.text, "实名提交成功")

        print("\n3. 匿名用户填写")
        user2 = requests.Session()
        register(user2, user2_name)
        token = csrf(user2, f"{BASE_URL}/survey/{public_id}")
        response = user2.post(
            f"{BASE_URL}/survey/{public_id}/start",
            data={"_csrf": token, "anonymous": "1"},
        )
        response.raise_for_status()
        check("第一题" in response.text, "匿名开始填写")

        qid = CURRENT_QID_RE.search(response.text).group(1)
        token = CSRF_RE.search(response.text).group(1)
        response = user2.post(
            f"{BASE_URL}/survey/{public_id}/fill/answer",
            data={"_csrf": token, "question_id": qid, "value": "a"},
        )
        response.raise_for_status()
        check("第三题" in response.text, "匿名选择 A 后跳到第三题")

        qid = CURRENT_QID_RE.search(response.text).group(1)
        token = CSRF_RE.search(response.text).group(1)
        response = user2.post(
            f"{BASE_URL}/survey/{public_id}/fill/answer",
            data={"_csrf": token, "question_id": qid, "value": "匿名答案"},
        )
        response.raise_for_status()
        token = CSRF_RE.search(response.text).group(1)
        response = user2.post(
            f"{BASE_URL}/survey/{public_id}/review/submit",
            data={"_csrf": token},
        )
        response.raise_for_status()
        check("提交成功" in response.text, "匿名提交成功")

        print("\n4. 查看统计")
        response = owner.get(f"{BASE_URL}/surveys/{survey_id}/stats")
        response.raise_for_status()
        check(response.status_code == 200, "统计页返回 200")
        check("实名答案" in response.text, "统计页包含实名文本答案")
        check("匿名答案" in response.text, "统计页包含匿名文本答案")
        check("已提交 <strong>2</strong> 份" in response.text, "统计页显示 2 份已提交答卷")

        print(f"\nE2E 冒烟测试全部通过（{passed} 项）。")
    finally:
        cleanup(survey_id, [owner_name, user1_name, user2_name])
        print("测试数据已清理。")


def cleanup(survey_id, usernames):
    client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[Config.MONGO_DB]
    if survey_id:
        oid = ObjectId(survey_id)
        db[RESPONSES].delete_many({"survey_id": oid})
        db[SURVEY_QUESTIONS].delete_many({"survey_id": oid})
        db[SURVEYS].delete_one({"_id": oid})
    # 清理本次测试用户创建的题目族及其版本/共享
    user_ids = [u["_id"] for u in db[USERS].find({"username": {"$in": usernames}})]
    if user_ids:
        qids = [q["_id"] for q in db[QUESTIONS].find({"owner_id": {"$in": user_ids}})]
        if qids:
            db[QUESTION_VERSIONS].delete_many({"question_id": {"$in": qids}})
            db[QUESTION_SHARES].delete_many({"question_id": {"$in": qids}})
            db[QUESTIONS].delete_many({"_id": {"$in": qids}})
    db[USERS].delete_many({"username": {"$in": usernames}})
    client.close()


if __name__ == "__main__":
    main()
