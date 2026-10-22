"""注册、登录、退出与会话恢复。"""

import functools

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from db import USERS, get_db
from utils import safe_redirect_target, to_object_id, utcnow

bp = Blueprint("auth", __name__)


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None
    if not user_id:
        return
    oid = to_object_id(user_id)
    if oid is None:
        session.pop("user_id", None)
        return
    user = get_db()[USERS].find_one({"_id": oid})
    if user is None:
        session.pop("user_id", None)
        return
    g.user = user


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("请先登录后再继续。", "warning")
            if request.method == "GET":
                next_url = request.full_path.rstrip("?")
            else:
                next_url = url_for("surveys.dashboard")
            return redirect(url_for("auth.login", next=next_url))
        return view(*args, **kwargs)

    return wrapped_view


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(url_for("surveys.dashboard"))

    form = {"username": ""}
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        form["username"] = username

        errors = []
        if not (3 <= len(username) <= 30):
            errors.append("用户名长度需为 3~30 个字符。")
        if any(ch.isspace() for ch in username):
            errors.append("用户名不能包含空白字符。")
        if len(password) < 6:
            errors.append("密码至少需要 6 个字符。")
        if len(password) > 128:
            errors.append("密码过长。")
        if password != confirm:
            errors.append("两次输入的密码不一致。")

        if not errors:
            try:
                result = get_db()[USERS].insert_one(
                    {
                        "username": username,
                        "password_hash": generate_password_hash(password),
                        "created_at": utcnow(),
                    }
                )
            except DuplicateKeyError:
                errors.append("该用户名已被注册，请换一个。")
            else:
                session.clear()
                session["user_id"] = str(result.inserted_id)
                session.permanent = True
                flash("注册成功，欢迎使用！", "success")
                return redirect(url_for("surveys.dashboard"))

        for error in errors:
            flash(error, "error")

    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("surveys.dashboard"))

    form = {"username": ""}
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        form["username"] = username

        user = get_db()[USERS].find_one({"username": username})
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码错误。", "error")
        else:
            next_url = safe_redirect_target(
                request.form.get("next") or request.args.get("next")
            )
            session.clear()
            session["user_id"] = str(user["_id"])
            session.permanent = True
            flash(f"欢迎回来，{user['username']}！", "success")
            return redirect(next_url or url_for("surveys.dashboard"))

    return render_template(
        "auth/login.html",
        form=form,
        next_url=safe_redirect_target(request.args.get("next")) or "",
    )


@bp.post("/logout")
def logout():
    session.clear()
    flash("已退出登录。", "success")
    return redirect(url_for("auth.login"))
