from __future__ import annotations

import os
from datetime import datetime as dt
from functools import wraps
from typing import List

import bleach
import click
from flask import (Flask, abort, flash, redirect, render_template, request,
                   url_for)
from flask_bcrypt import Bcrypt
from flask_bootstrap import Bootstrap5
from flask_caching import Cache
from flask_ckeditor import CKEditor, CKEditorField
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column,
                            relationship)
from wtforms import PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import URL, DataRequired, Email, Length

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


bcrypt = Bcrypt()
db = SQLAlchemy(model_class=Base)
cache = Cache()
ckeditor = CKEditor()
bootstrap = Bootstrap5()
login_manager = LoginManager()
migrate = Migrate()
limiter = Limiter(key_func=get_remote_address)

# Tags CKEditor may legitimately produce. Everything else is stripped.
ALLOWED_TAGS = [
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "sub", "sup",
    "blockquote", "code", "pre", "ul", "ol", "li", "a", "img",
    "h1", "h2", "h3", "h4", "h5", "h6", "span", "div", "table", "thead",
    "tbody", "tr", "th", "td", "figure", "figcaption",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "*": ["class"],
}


def sanitize(html: str) -> str:
    """Strip script/handler payloads from user-submitted rich text."""
    return bleach.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def create_app() -> Flask:
    app = Flask(__name__)

    secret = os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        if os.environ.get("FLASK_ENV") == "development":
            secret = "dev-only-insecure-key"
        else:
            raise RuntimeError("FLASK_SECRET_KEY must be set")
    app.config["SECRET_KEY"] = secret

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "SQLALCHEMY_DATABASE_URI", "sqlite:///posts.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["CKEDITOR_PKG_TYPE"] = "full"

    # Session cookie hardening
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    )

    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        app.config["CACHE_TYPE"] = "RedisCache"
        app.config["CACHE_REDIS_URL"] = redis_url
    else:
        app.config["CACHE_TYPE"] = "SimpleCache"
    app.config["CACHE_DEFAULT_TIMEOUT"] = 300
    app.config["RATELIMIT_STORAGE_URI"] = redis_url or "memory://"

    db.init_app(app)
    bcrypt.init_app(app)
    cache.init_app(app)
    ckeditor.init_app(app)
    bootstrap.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = "Please login to access this."
    login_manager.login_message_category = "info"

    register_routes(app)
    register_cli(app)
    return app


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Users(UserMixin, db.Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    posts: Mapped[List["BlogPost"]] = relationship(
        back_populates="writer", cascade="all, delete-orphan"
    )
    comments: Mapped[List["Comment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def set_password_hash(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password_hash(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)


class BlogPost(db.Model):
    __tablename__ = "blog_post"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subtitle: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    img_url: Mapped[str] = mapped_column(String(1024))

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    writer: Mapped["Users"] = relationship(back_populates="posts")
    comments: Mapped[List["Comment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class Comment(db.Model):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[dt] = mapped_column(DateTime, default=dt.utcnow)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("blog_post.id"), index=True)
    user: Mapped["Users"] = relationship(back_populates="comments")
    post: Mapped["BlogPost"] = relationship(back_populates="comments")


# --------------------------------------------------------------------------
# Cache invalidation (declared AFTER BlogPost exists)
# --------------------------------------------------------------------------


def clear_blog_cache() -> None:
    cache.delete_memoized(_cached_posts)


@event.listens_for(BlogPost, "after_insert")
@event.listens_for(BlogPost, "after_update")
@event.listens_for(BlogPost, "after_delete")
def _invalidate_post_cache(mapper, connection, target) -> None:
    clear_blog_cache()


@cache.memoize(timeout=300)
def _cached_posts():
    rows = db.session.execute(
        db.select(BlogPost).order_by(BlogPost.id.desc())
    ).scalars().all()
    return [
        {
            "id": p.id,
            "title": p.title,
            "subtitle": p.subtitle,
            "author": p.author,
            "date": p.date,
        }
        for p in rows
    ]


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------


class PostForm(FlaskForm):
    title = StringField("Blog Post Title", validators=[DataRequired(), Length(max=255)])
    subtitle = StringField("Subtitle", validators=[DataRequired(), Length(max=255)])
    author = StringField("Your Name", validators=[DataRequired(), Length(max=120)])
    img_url = StringField("Blog Image URL", validators=[DataRequired(), URL()])
    body = CKEditorField("Blog Content", validators=[DataRequired(), Length(min=20)])
    submit = SubmitField("Submit Post")


class Login(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "you@example.com", "autocomplete": "username"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8)],
        render_kw={"autocomplete": "current-password"},
    )
    submit = SubmitField("Login")


class Register(FlaskForm):
    name = StringField("Your Name", validators=[DataRequired(), Length(max=120)])
    email = StringField(
        "Your Email",
        validators=[DataRequired(), Email()],
        render_kw={"autocomplete": "username"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=72)],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Register")


class CommentForm(FlaskForm):
    comment_text = TextAreaField("Comment", validators=[DataRequired(), Length(max=5000)])
    submit = SubmitField("Submit")


class DeleteForm(FlaskForm):
    """CSRF token carrier for destructive POST actions."""
    submit = SubmitField("Delete")


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Users, int(user_id))


def admin_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrap


def can_modify(post: BlogPost) -> bool:
    return current_user.is_authenticated and (
        current_user.is_admin or post.user_id == current_user.id
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def register_routes(app: Flask) -> None:

    @app.get("/healthz")
    @limiter.exempt
    def healthz():
        db.session.execute(db.text("SELECT 1"))
        return {"status": "ok"}, 200

    @app.route("/")
    def get_all_posts():
        return render_template(
            "index.html", all_posts=_cached_posts(), delete_form=DeleteForm()
        )

    @app.route("/post/<int:index>", methods=["GET", "POST"])
    def show_post(index):
        blog_post = db.get_or_404(BlogPost, index)
        form = CommentForm()
        if form.validate_on_submit():
            if not current_user.is_authenticated:
                flash("You must be logged in to comment on this post.")
                return redirect(url_for("login"))
            db.session.add(
                Comment(
                    text=sanitize(form.comment_text.data),
                    user_id=current_user.id,
                    post_id=blog_post.id,
                )
            )
            db.session.commit()
            # Redirect after POST so a refresh doesn't resubmit the comment.
            return redirect(url_for("show_post", index=blog_post.id))

        return render_template(
            "post.html",
            post=blog_post,
            post_id=blog_post.id,
            form=form,
            can_modify=can_modify(blog_post),
            delete_form=DeleteForm(),
        )

    @app.route("/new_post", methods=["POST", "GET"])
    @login_required
    def new_post():
        post_form = PostForm()
        if post_form.validate_on_submit():
            today = dt.now()
            post = BlogPost(
                body=sanitize(post_form.body.data),
                title=post_form.title.data,
                subtitle=post_form.subtitle.data,
                date=today.strftime("%B %d, %Y"),
                author=post_form.author.data,
                img_url=post_form.img_url.data,
                user_id=current_user.id,
            )
            db.session.add(post)
            db.session.commit()
            clear_blog_cache()
            return redirect(url_for("show_post", index=post.id))
        return render_template("make-post.html", form=post_form, status="New Post")

    @app.route("/edit/<int:index>", methods=["GET", "POST"])
    @login_required
    def edit_post(index):
        post = db.get_or_404(BlogPost, index)
        if not can_modify(post):
            abort(403)

        edit_form = PostForm(obj=post)
        if edit_form.validate_on_submit():
            post.title = edit_form.title.data
            post.subtitle = edit_form.subtitle.data
            post.img_url = edit_form.img_url.data
            post.author = edit_form.author.data
            post.body = sanitize(edit_form.body.data)
            db.session.commit()
            clear_blog_cache()
            return redirect(url_for("show_post", index=post.id))

        return render_template("make-post.html", form=edit_form, is_edit=True)

    @app.post("/delete/<int:post_id>")
    @login_required
    def delete_post(post_id):
        if not DeleteForm().validate_on_submit():
            abort(400)
        post = db.get_or_404(BlogPost, post_id)
        if not can_modify(post):
            abort(403)
        db.session.delete(post)
        db.session.commit()
        clear_blog_cache()
        return redirect(url_for("get_all_posts"))

    @app.route("/login", methods=["POST", "GET"])
    @limiter.limit("10 per minute; 50 per hour", methods=["POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("get_all_posts"))

        form = Login()
        if form.validate_on_submit():
            user = db.session.execute(
                db.select(Users).filter_by(email=form.email.data.lower().strip())
            ).scalar_one_or_none()
            # Same message either way — don't leak which emails are registered.
            if user and user.check_password_hash(form.password.data):
                login_user(user)
                return redirect(url_for("get_all_posts"))
            flash("Invalid credentials", "danger")
        return render_template("login.html", form=form)

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("get_all_posts"))

    @app.route("/register", methods=["POST", "GET"])
    @limiter.limit("5 per hour", methods=["POST"])
    def register():
        user_form = Register()
        if user_form.validate_on_submit():
            email = user_form.email.data.lower().strip()
            exists = db.session.execute(
                db.select(Users).filter_by(email=email)
            ).scalar_one_or_none()
            if exists:
                flash("Email already taken", "danger")
                return redirect(url_for("login"))

            new_user = Users(name=user_form.name.data, email=email)
            new_user.set_password_hash(user_form.password.data)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for("get_all_posts"))
        return render_template("register.html", form=user_form)

    @app.get("/admin/users")
    @login_required
    @admin_required
    def get_users():
        users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
        return {"users": [{"id": u.id, "name": u.name, "email": u.email,
                           "is_admin": u.is_admin} for u in users]}

    @app.post("/admin/users/<int:user_id>/delete")
    @login_required
    @admin_required
    def delete_user(user_id):
        if not DeleteForm().validate_on_submit():
            abort(400)
        if user_id == current_user.id:
            flash("You cannot delete your own account here.", "danger")
            return redirect(url_for("get_users"))
        user = db.get_or_404(Users, user_id)  # was querying BlogPost
        db.session.delete(user)
        db.session.commit()
        clear_blog_cache()
        return redirect(url_for("get_users"))

    @app.post("/admin/users/<int:user_id>/promote")
    @login_required
    @admin_required
    def promote_user(user_id):
        if not DeleteForm().validate_on_submit():
            abort(400)
        user = db.get_or_404(Users, user_id)
        user.is_admin = True
        db.session.commit()
        return redirect(url_for("get_users"))

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/contact")
    def contact():
        return render_template("contact.html")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def register_cli(app: Flask) -> None:

    @app.cli.command("init-db")
    def init_db():
        """Create tables directly (use `flask db upgrade` once migrations exist)."""
        db.create_all()
        click.echo("Tables created.")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("name")
    @click.password_option()
    def create_admin(email, name, password):
        email = email.lower().strip()
        user = db.session.execute(
            db.select(Users).filter_by(email=email)
        ).scalar_one_or_none()
        if user is None:
            user = Users(name=name, email=email)
            db.session.add(user)
        user.set_password_hash(password)
        user.is_admin = True
        db.session.commit()
        click.echo(f"Admin ready: {email}")


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
