from __future__ import annotations

from flask import Flask, render_template, request, url_for, redirect, flash, send_from_directory, abort
from flask_bootstrap import Bootstrap5
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_login import LoginManager, UserMixin, login_required, logout_user, current_user, login_user
from flask_ckeditor import CKEditor, CKEditorField
from flask_bcrypt import Bcrypt
from flask_migrate import Migrate
# WTForms imports
from wtforms import StringField, SubmitField, PasswordField, TextAreaField
from wtforms.validators import DataRequired, URL, Length, Email
# Sqlalchemy imports
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, Boolean, DateTime, Text
from sqlalchemy import ForeignKey, String, Text, DateTime
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime as dt
import os
from functools import wraps
from typing import List
from datetime import datetime

##CONNECT TO DB
bcrypt = Bcrypt()

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("SQLALCHEMY_DATABASE_URI")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.config['CKEDITOR_PKG_TYPE'] = 'full'
ckeditor = CKEditor(app)
bootstrap = Bootstrap5(app)
#
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please login to access this."
login_manager.login_message_category = 'info'

migrate = Migrate(app, db)

@login_manager.user_loader
def load_user(user_id):
    return Users.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if not current_user.is_authenticated or current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrap

class Users(UserMixin, db.Model):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Relationships
    posts: Mapped[List["BlogPost"]] = relationship(back_populates='writer', cascade='all, delete-orphan')
    comments: Mapped[List["Comment"]] = relationship(back_populates='user', cascade='all, delete-orphan')

    def set_password_hash(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256', salt_length=8)

    def check_password_hash(self, password):
        return check_password_hash(self.password_hash, password)


class BlogPost(db.Model):
    __tablename__ = 'blog_post'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    subtitle: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    img_url: Mapped[str] = mapped_column(String)
    # Foreign key
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    # Relationships
    writer: Mapped["Users"] = relationship(back_populates='posts')
    comments: Mapped[List["Comment"]] = relationship(back_populates='post', cascade='all, delete-orphan')


class Comment(db.Model):
    __tablename__ = 'comments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[str] = mapped_column(DateTime, default=dt.now)
    # Foreign keys
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    post_id: Mapped[int] = mapped_column(ForeignKey('blog_post.id'))
    # Relationships
    user: Mapped["Users"] = relationship(back_populates='comments')
    post: Mapped["BlogPost"] = relationship(back_populates='comments')

##WTForm
class PostForm(FlaskForm):
    title = StringField(label="Blog Post Title", validators=[DataRequired()])
    subtitle = StringField(label="Subtitle", validators=[DataRequired()])
    author = StringField(label="Your Name", validators=[DataRequired()])
    img_url = StringField(label="Blog Image URL", validators=[DataRequired(), URL()])
    body = CKEditorField(label="Blog Content", validators=[DataRequired(), Length(min=20)])
    submit = SubmitField(label="Submit Post")

class Login(FlaskForm):
    email = StringField(label="Email",
                        validators=[DataRequired(), Email()],
                        render_kw={"placeholder": "Johndoe@gmail.com", 'autocomplete': "off"}
                        )
    password = PasswordField(label="Password",
                             validators=[DataRequired(), Length(min=8)],
                             render_kw={'placeholder': '$%Pass089word', 'autocomplete': 'off'}
                             )
    submit = SubmitField(label="Login")

class Register(FlaskForm):
    name = StringField(label="Your Name", validators=[DataRequired()], render_kw={"placeholder": "John Doe"})
    email = StringField(label="Your Email",
                        validators=[DataRequired(), Email()],
                        render_kw={"placeholder": "Johndoe@gmail.com", 'autocomplete': 'off'}
                        )
    password = PasswordField(label="Password",
                             validators=[DataRequired(), Length(min=8)],
                             render_kw={'placeholder': '$%Pass089word', 'autocomplete': 'new-password'}
                             )
    submit = SubmitField(label="Register")

class CommentForm(FlaskForm):
    comment_text = TextAreaField(label="Comment", validators=[DataRequired()])
    submit = SubmitField(label="Submit")

with app.app_context():
    db.create_all()

# Routes
@app.route('/')
def get_all_posts():
    posts = db.session.execute(db.select(BlogPost).order_by(BlogPost.id)).scalars()
    return render_template("index.html", all_posts=posts)


@app.route("/post/<int:index>", methods=["GET", "POST"])
def show_post(index):
    post_id = index
    blog_post = db.get_or_404(BlogPost, index)
    form = CommentForm()
    if form.validate_on_submit():
        post_object = db.get_or_404(BlogPost, post_id)
        if not current_user.is_authenticated:
            flash("You must be logged in to comment on this post.")
            return redirect(url_for('login'))
        new_comment = Comment(
            text=form.comment_text.data,
            user_id = current_user.id,
            post_id = post_id
        )
        db.session.add(new_comment)
        db.session.commit()

    return render_template("post.html", post=blog_post, post_id=post_id, form=form)

@app.route("/new_post", methods=['POST', 'GET'])
@login_required
def new_post():
    post_form = PostForm()
    today = dt.now()
    date = f"{today.strftime('%B')} {today.strftime('%d')}, {today.strftime('%Y')}"
    stat = 'New Post'
    if request.method == 'POST':
        if post_form.validate_on_submit():
            post = BlogPost(
                body=post_form.body.data,
                title=post_form.title.data,
                subtitle=post_form.subtitle.data,
                date=date,
                author=post_form.author.data,
                img_url=post_form.img_url.data,
                user_id=current_user.id
            )
            db.session.add(post)
            db.session.commit()
            print(post_form.title.data)
            return redirect(url_for('get_all_posts'))
    return render_template("make-post.html", form=post_form, status=stat)

@app.route("/edit/<int:index>", methods=['GET', 'POST'])  # Added int converter
@login_required
def edit_post(index):
    post = db.get_or_404(BlogPost, index)
    # Pass the existing post to the form
    edit_form = PostForm(
        title=post.title,
        subtitle=post.subtitle,
        img_url=post.img_url,
        author=post.author,
        body=post.body
    )
    if edit_form.validate_on_submit():
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = edit_form.img_url.data
        post.author = edit_form.author.data
        post.body = edit_form.body.data
        db.session.commit()
        return redirect(url_for("show_post", index=post.id))

    return render_template("make-post.html", form=edit_form, is_edit=True)

@app.route('/delete/<post_id>', methods=['POST', 'GET'])
@login_required
@admin_required
def delete_post(post_id):
    post = db.session.query(BlogPost).filter_by(id=post_id).first()
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for('get_all_posts'))

@app.route('/login', methods=['POST', 'GET'])
def login():
    users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
    # print(users.email)
    for user in users:
        print(user.email)
    form = Login()
    if current_user.is_authenticated:
        return redirect(url_for('get_all_posts'))

    if request.method == 'POST' and form.validate_on_submit():
        user_email = request.form.get('email')
        print(f"{user_email=}")
        user_password = request.form.get('password')
        print(f"{user_password=}")

        # remember = True if request.form.get('remember') else False
        user = Users.query.filter_by(email=user_email).first()
        if user:
            if user.check_password_hash(user_password):
                login_user(user)
                # flash('You are logged in')
                return redirect(url_for('get_all_posts'))
            else:
                flash('Invalid credentials')
        else:
            flash('Invalid credentials')
    return render_template("login.html", form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('get_all_posts'))

@app.route('/register', methods=['POST', 'GET'])
def register():
    user_form = Register()

    # # Check if user already exists
    if request.method == 'POST':
        users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
        user_email = user_form.email.data
        user_exists = Users.query.filter_by(email=user_email).first()
        if user_exists:
            flash('Email already taken', 'danger')
            return redirect(url_for('login'))

        # emails = [user.email for user in users]
        # passwords = [user.password_hash for user in users]
        # print(emails)
        # print(passwords)

        if user_form.validate_on_submit():

            # Create new user
            new_user = Users(
                name=user_form.name.data,
                email=user_form.email.data,
            )
            new_user.set_password_hash(user_form.password.data)
            db.session.add(new_user)
            db.session.commit()
            print(user_form.name.data)
            print(user_form.email.data)
            users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
            print((user.email, user.id) for user in users)

            return redirect(url_for('get_all_posts'))

    return render_template("register.html", form=user_form)

@app.route('/users', methods=['GET'])
def get_users():
    users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
    emails_1 = [user.email for user in users]
    # with app.app_context():
    #     db.session.execute(db.text("DROP TABLE IF EXISTS users"))
    #     db.session.commit()
    #     print('Table users deleted')
    # if db.session.execute(db.select(Users).order_by(Users.id)).scalars():
    #     users = db.session.execute(db.select(Users).order_by(Users.id)).scalars()
    #     emails_2 = [user.email for user in users]
    #     return emails_1, emails_2
    return emails_1

@app.route('/delete/user/<user_id>', methods=['POST', 'GET'])
def delete_user(user_id):
    user = db.session.query(BlogPost).filter_by(id=user_id).first()
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('register'))

@app.route("/edit/user/<int:index>", methods=['GET', 'POST'])  # Added int converter
def edit_user(index):
    user = db.get_or_404(Users, index)
    # Pass the existing post to the form
    user.is_admin = True
    db.session.commit()
    return redirect(url_for("register"))

    return render_template("make-post.html", form=edit_form, is_edit=True)


@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")


if __name__ == "__main__":
    app.run(debug=True)

