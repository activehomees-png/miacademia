from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

post_likes = db.Table('post_likes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('post_id', db.Integer, db.ForeignKey('post.id'), primary_key=True),
)


class User(UserMixin, db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(80),  unique=True, nullable=False)
    email        = db.Column(db.String(120), unique=True, nullable=False)
    password_hash= db.Column(db.String(256))
    role         = db.Column(db.String(20), default='student')   # 'student' | 'admin'
    bio          = db.Column(db.Text, default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen    = db.Column(db.DateTime, nullable=True)
    avatar_data  = db.Column(db.LargeBinary, nullable=True)
    avatar_mime  = db.Column(db.String(50), default='image/jpeg')
    status       = db.Column(db.String(20), default='active')  # 'pending' | 'active' | 'rejected'

    posts        = db.relationship('Post',    backref='author', lazy=True)
    comments     = db.relationship('Comment', backref='author', lazy=True)
    enrollments  = db.relationship('Enrollment', backref='user', lazy=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def is_enrolled(self, course_id):
        return Enrollment.query.filter_by(user_id=self.id, course_id=course_id).first() is not None

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def initials(self):
        return self.username[0].upper()


class Category(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(20), default='#6366f1')
    emoji = db.Column(db.String(10), default='💬')
    posts = db.relationship('Post', backref='category', lazy=True)


class Post(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    title       = db.Column(db.String(200), nullable=False)
    content     = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    pinned      = db.Column(db.Boolean, default=False)

    likes    = db.relationship('User', secondary=post_likes,
                               backref=db.backref('liked_posts', lazy=True))
    comments = db.relationship('Comment', backref='post', lazy=True,
                               cascade='all, delete-orphan',
                               order_by='Comment.created_at')


class Comment(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Course(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    subtitle     = db.Column(db.String(300), default='')
    description  = db.Column(db.Text, default='')
    image        = db.Column(db.String(500), default='')
    price        = db.Column(db.Float, default=0.0)
    is_published = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    sections    = db.relationship('Section', backref='course', lazy=True,
                                  order_by='Section.order',
                                  cascade='all, delete-orphan')
    enrollments = db.relationship('Enrollment', backref='course', lazy=True)

    @property
    def is_free(self):
        return self.price == 0.0

    @property
    def lesson_count(self):
        return sum(len(s.lessons) for s in self.sections)

    @property
    def total_duration(self):
        return sum(l.duration_min for s in self.sections for l in s.lessons)


class Section(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    title     = db.Column(db.String(200), nullable=False)
    order     = db.Column(db.Integer, default=0)
    lessons   = db.relationship('Lesson', backref='section', lazy=True,
                                order_by='Lesson.order',
                                cascade='all, delete-orphan')


class Lesson(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    section_id   = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    video_url    = db.Column(db.String(500), default='')
    description  = db.Column(db.Text, default='')
    order        = db.Column(db.Integer, default=0)
    duration_min = db.Column(db.Integer, default=0)
    files        = db.relationship('LessonFile', backref='lesson', lazy=True,
                                   cascade='all, delete-orphan')


class LessonFile(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    name       = db.Column(db.String(200), nullable=False)
    mimetype   = db.Column(db.String(100), default='application/octet-stream')
    size       = db.Column(db.Integer, default=0)
    data       = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LessonImage(db.Model):
    """Inline images embedded in lesson descriptions via TinyMCE editor."""
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    mimetype   = db.Column(db.String(100), default='image/jpeg')
    data       = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Enrollment(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id         = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    enrolled_at       = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_session_id = db.Column(db.String(200), default='')


class LessonProgress(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)


class SiteSettings(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    academy_name          = db.Column(db.String(100), default='Marca Atractora')
    community_image       = db.Column(db.String(500), default='')
    community_image_data  = db.Column(db.LargeBinary, nullable=True)
    community_image_mime  = db.Column(db.String(50), default='image/jpeg')
    community_description = db.Column(db.Text, default='')
    link_url              = db.Column(db.String(500), default='')
    link_text             = db.Column(db.String(200), default='¡Empieza por aquí!')


class PointEvent(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    points     = db.Column(db.Integer, default=0)
    reason     = db.Column(db.String(50))   # 'lesson' | 'comment' | 'like'
    ref_id     = db.Column(db.Integer)      # id of lesson/comment/post
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type       = db.Column(db.String(30))  # 'new_class' | 'class_reminder' | 'comment'
    message    = db.Column(db.String(300))
    link       = db.Column(db.String(200), default='')
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LiveClass(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text, default='')
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration_min = db.Column(db.Integer, default=60)
    meet_url     = db.Column(db.String(500), default='')
    instructor   = db.Column(db.String(100), default='')
    recurrence   = db.Column(db.String(10), default='none')  # 'none' | 'weekly' | 'monthly'
    parent_id    = db.Column(db.Integer, db.ForeignKey('live_class.id'), nullable=True)
