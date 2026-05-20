import os
import io
from functools import wraps
from datetime import datetime, timedelta

from flask import (Flask, render_template, redirect, url_for,
                   request, flash, jsonify, abort, send_file)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_mail import Mail, Message as MailMessage
from sqlalchemy import text

from models import (db, User, Category, Post, Comment,
                    Course, Section, Lesson, LessonFile, LessonImage, Enrollment, LessonProgress, LiveClass,
                    SiteSettings, PointEvent, Notification)

app = Flask(__name__)
app.config.from_pyfile('config.py')

db.init_app(app)
mail = Mail(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Inicia sesión para continuar.'

# ── Jinja helpers ─────────────────────────────────────────────────────────────

def youtube_embed(url: str) -> str:
    if not url:
        return ''
    # Vimeo
    if 'vimeo.com' in url:
        if 'player.vimeo.com' in url:
            return url
        # strip query string, split path after vimeo.com/
        path = url.split('vimeo.com/')[1].split('?')[0]
        parts = path.split('/')
        vid = parts[0]
        # si hay hash (vimeo.com/ID/HASH) lo añadimos como parámetro h=
        if len(parts) > 1 and parts[1]:
            return f'https://player.vimeo.com/video/{vid}?h={parts[1]}'
        return f'https://player.vimeo.com/video/{vid}'
    # YouTube
    if 'youtu.be/' in url:
        vid = url.split('youtu.be/')[1].split('?')[0]
    elif 'v=' in url:
        vid = url.split('v=')[1].split('&')[0]
    elif 'embed/' in url:
        return url
    else:
        return url
    return f'https://www.youtube.com/embed/{vid}'

app.jinja_env.filters['youtube_embed'] = youtube_embed

def timeago(dt: datetime) -> str:
    diff = datetime.utcnow() - dt
    s = diff.total_seconds()
    if s < 60:    return 'ahora mismo'
    if s < 3600:  return f'hace {int(s//60)} min'
    if s < 86400: return f'hace {int(s//3600)} h'
    return f'hace {int(s//86400)} d'

app.jinja_env.filters['timeago'] = timeago
app.jinja_env.globals['get_level']  = lambda pts: get_level(pts)  # set after get_level is defined

def notify(user_id, type_, message, link=''):
    db.session.add(Notification(user_id=user_id, type=type_, message=message, link=link))

@app.before_request
def update_last_seen():
    if current_user.is_authenticated:
        now = datetime.utcnow()
        if not current_user.last_seen or (now - current_user.last_seen).total_seconds() > 60:
            current_user.last_seen = now
            # Check for classes starting in the next 24h without a reminder yet
            try:
                window_start = now + timedelta(hours=1)
                window_end   = now + timedelta(hours=25)
                upcoming = LiveClass.query.filter(
                    LiveClass.scheduled_at >= window_start,
                    LiveClass.scheduled_at <= window_end
                ).all()
                for lc in upcoming:
                    exists = Notification.query.filter_by(
                        user_id=current_user.id, type='class_reminder', link=f'/calendario'
                    ).filter(Notification.message.contains(lc.title)).first()
                    if not exists:
                        notify(current_user.id, 'class_reminder',
                               f'🔔 "{lc.title}" empieza en menos de 24 horas', '/calendario')
            except Exception:
                pass
            db.session.commit()

def award_points(user_id, reason, ref_id, pts):
    if not PointEvent.query.filter_by(user_id=user_id, reason=reason, ref_id=ref_id).first():
        db.session.add(PointEvent(user_id=user_id, points=pts, reason=reason, ref_id=ref_id))
        db.session.commit()

# ── LEVEL SYSTEM ──────────────────────────────────────────────────────────────
_LEVELS = [
    # (threshold, name, emoji, color_hex)
    (0,       'Principiante', '🌱', '#6b7280'),
    (250,     'Aprendiz',     '⭐', '#d97706'),
    (750,     'Explorador',   '🔥', '#ea580c'),
    (2000,    'Comprometido', '💪', '#2563eb'),
    (5000,    'Avanzado',     '🚀', '#7c3aed'),
    (12000,   'Experto',      '💎', '#0891b2'),
    (30000,   'Élite',        '👑', '#b45309'),
    (75000,   'Maestro',      '⚡', '#dc2626'),
    (200000,  'Leyenda',      '🌟', '#db2777'),
    (500000,  'Inmortal',     '🏆', '#111827'),
]

def get_level(pts):
    """Return dict with level info for a given points total."""
    current = 0
    for i, (threshold, name, emoji, color) in enumerate(_LEVELS):
        if pts >= threshold:
            current = i
        else:
            break
    level_num   = current + 1
    _, name, emoji, color = _LEVELS[current]
    next_thresh = _LEVELS[current + 1][0] if current + 1 < len(_LEVELS) else None
    prev_thresh = _LEVELS[current][0]
    if next_thresh is not None:
        span = next_thresh - prev_thresh
        progress = min(100, round((pts - prev_thresh) * 100 / span))
        pts_to_next = next_thresh - pts
    else:
        progress    = 100
        pts_to_next = 0
    return {
        'num':        level_num,
        'name':       name,
        'emoji':      emoji,
        'color':      color,
        'progress':   progress,
        'pts_to_next': pts_to_next,
        'next_thresh': next_thresh,
        'is_max':     next_thresh is None,
    }

def get_leaderboard(since=None):
    q = PointEvent.query
    if since:
        q = q.filter(PointEvent.created_at >= since)
    rows = q.all()
    totals = {}
    for e in rows:
        totals[e.user_id] = totals.get(e.user_id, 0) + e.points
    result = []
    for uid, pts in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]:
        u = User.query.get(uid)
        if u:
            result.append((u, pts))
    return result

def get_settings():
    s = SiteSettings.query.first()
    if not s:
        s = SiteSettings()
        db.session.add(s)
        db.session.commit()
    return s

@app.context_processor
def inject_settings():
    try:
        return {'site': get_settings()}
    except Exception:
        return {'site': SiteSettings()}

# ── Auth helpers ──────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('community'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        user  = User.query.filter_by(email=email).first()
        if user and user.check_password(pw):
            # Force activate admins regardless of status
            if user.role == 'admin' and getattr(user, 'status', 'active') != 'active':
                user.status = 'active'
                db.session.commit()
            if getattr(user, 'status', 'active') == 'pending':
                flash('Tu cuenta está pendiente de aprobación por un administrador. Te avisaremos pronto.', 'error')
                return render_template('auth/login.html')
            if getattr(user, 'status', 'active') == 'rejected':
                flash('Tu solicitud de acceso ha sido denegada. Contacta con el administrador.', 'error')
                return render_template('auth/login.html')
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('community'))
        flash('Email o contraseña incorrectos.', 'error')
    return render_template('auth/login.html')

@app.route('/registro', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('community'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip().lower()
        pw       = request.form.get('password', '')
        bio      = request.form.get('bio', '').strip()
        avatar   = request.files.get('avatar')
        if len(pw) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'error')
        elif not bio:
            flash('Por favor escribe una breve descripción sobre ti.', 'error')
        elif not avatar or not avatar.filename:
            flash('La foto de perfil es obligatoria.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Ese email ya está registrado.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Ese nombre de usuario ya existe.', 'error')
        else:
            avatar_data = avatar.read()
            if len(avatar_data) > 4 * 1024 * 1024:
                flash('La imagen no puede superar 4 MB.', 'error')
                return render_template('auth/register.html')
            user = User(username=username, email=email, bio=bio,
                        avatar_data=avatar_data,
                        avatar_mime=avatar.mimetype or 'image/jpeg',
                        status='pending')
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            # Notify all admins
            admins = User.query.filter_by(role='admin').all()
            for admin in admins:
                notify(admin.id, 'new_user',
                       f'🙋 Nueva solicitud de acceso de {username} ({email})',
                       '/admin/usuarios')
            db.session.commit()
            return render_template('auth/pending.html')
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/notificaciones/datos')
@login_required
def notifications_data():
    notifs = Notification.query.filter_by(user_id=current_user.id)\
        .order_by(Notification.created_at.desc()).limit(20).all()
    unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({
        'unread': unread,
        'items': [{'id': n.id, 'message': n.message, 'link': n.link,
                   'is_read': n.is_read, 'created_at': n.created_at.strftime('%d %b %H:%M')}
                  for n in notifs]
    })

@app.route('/notificaciones/leer', methods=['POST'])
@login_required
def notifications_read():
    nid = request.json.get('id')
    if nid:
        n = Notification.query.filter_by(id=nid, user_id=current_user.id).first()
        if n: n.is_read = True
    else:
        Notification.query.filter_by(user_id=current_user.id, is_read=False)\
            .update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/cuenta', methods=['GET', 'POST'])
@login_required
def account_settings():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'profile':
            new_username = request.form.get('username', '').strip()
            new_email    = request.form.get('email', '').strip()
            new_bio      = request.form.get('bio', '').strip()
            if new_username and new_username != current_user.username:
                if User.query.filter_by(username=new_username).first():
                    flash('Ese nombre de usuario ya está en uso.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.username = new_username
            if new_email and new_email != current_user.email:
                if User.query.filter_by(email=new_email).first():
                    flash('Ese email ya está en uso.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.email = new_email
            current_user.bio = new_bio
            db.session.commit()
            flash('Perfil actualizado.', 'success')

        elif action == 'avatar':
            file = request.files.get('avatar')
            if file and file.filename:
                data = file.read()
                if len(data) > 4 * 1024 * 1024:
                    flash('La imagen no puede superar 4 MB.', 'error')
                    return redirect(url_for('account_settings'))
                current_user.avatar_data = data
                current_user.avatar_mime = file.mimetype or 'image/jpeg'
                db.session.commit()
                flash('Foto de perfil actualizada.', 'success')

        elif action == 'password':
            current_pw = request.form.get('current_password', '')
            new_pw     = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')
            if not current_user.check_password(current_pw):
                flash('La contraseña actual no es correcta.', 'error')
                return redirect(url_for('account_settings'))
            if len(new_pw) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.', 'error')
                return redirect(url_for('account_settings'))
            if new_pw != confirm_pw:
                flash('Las contraseñas no coinciden.', 'error')
                return redirect(url_for('account_settings'))
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Contraseña actualizada.', 'success')

        return redirect(url_for('account_settings'))
    return render_template('account_settings.html')

# ── COMMUNITY ─────────────────────────────────────────────────────────────────

@app.route('/')
@app.route('/comunidad')
@login_required
def community():
    cat_id = request.args.get('cat', type=int)
    q = Post.query.order_by(Post.pinned.desc(), Post.created_at.desc())
    if cat_id:
        q = q.filter_by(category_id=cat_id)
    posts      = q.limit(50).all()
    categories = Category.query.all()
    five_min_ago  = datetime.utcnow() - timedelta(minutes=5)
    month_start   = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    member_count  = User.query.count()
    admin_count   = User.query.filter_by(role='admin').count()
    admins        = User.query.filter_by(role='admin').limit(5).all()
    online_users  = User.query.filter(User.last_seen >= five_min_ago).order_by(User.last_seen.desc()).limit(20).all()
    top_month     = get_leaderboard(since=month_start)[:5]
    now = datetime.utcnow()
    # Clase en directo ahora mismo (empezó hace menos de duration_min)
    from sqlalchemy import and_
    live_now = (LiveClass.query
                .filter(LiveClass.scheduled_at <= now)
                .all())
    live_now = next((lc for lc in live_now
                     if (now - lc.scheduled_at).total_seconds() / 60 < lc.duration_min), None)
    # Próxima clase
    next_class = (LiveClass.query
                  .filter(LiveClass.scheduled_at > now)
                  .order_by(LiveClass.scheduled_at.asc())
                  .first())
    return render_template('community/feed.html',
                           posts=posts, categories=categories, active_cat=cat_id,
                           member_count=member_count, admin_count=admin_count,
                           admins=admins, online_users=online_users, top_month=top_month,
                           live_now=live_now, next_class=next_class)

@app.route('/comunidad/nuevo', methods=['GET', 'POST'])
@login_required
def new_post():
    categories = Category.query.all()
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        content     = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int)
        if not title or not content:
            flash('Título y contenido son obligatorios.', 'error')
        else:
            post = Post(user_id=current_user.id, title=title,
                        content=content, category_id=category_id)
            db.session.add(post)
            db.session.commit()
            award_points(current_user.id, 'post', post.id, 4)
            return redirect(url_for('community'))
    return render_template('community/new_post.html', categories=categories)

@app.route('/comunidad/post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if content:
            c = Comment(post_id=post_id, user_id=current_user.id, content=content)
            db.session.add(c)
            db.session.commit()
        return redirect(url_for('post_detail', post_id=post_id) + '#comments')
    return render_template('community/post.html', post=post)

@app.route('/comunidad/post/<int:post_id>/comentar', methods=['POST'])
@login_required
def add_comment_ajax(post_id):
    post    = Post.query.get_or_404(post_id)
    content = request.json.get('content', '').strip() if request.is_json else request.form.get('content', '').strip()
    if not content:
        return jsonify({'ok': False}), 400
    c = Comment(post_id=post_id, user_id=current_user.id, content=content)
    db.session.add(c)
    db.session.commit()
    award_points(current_user.id, 'comment', c.id, 2)
    if post.user_id != current_user.id:
        notify(post.user_id, 'comment',
               f'💬 {current_user.username} comentó en tu post "{post.title[:50]}"',
               f'/comunidad')
        db.session.commit()
    return jsonify({'ok': True, 'comment_id': c.id,
                    'username': current_user.username,
                    'initials': current_user.initials, 'content': content,
                    'timeago': 'ahora mismo',
                    'has_avatar': bool(current_user.avatar_data),
                    'user_id': current_user.id})

@app.route('/comunidad/post/<int:post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user in post.likes:
        post.likes.remove(current_user)
        liked = False
    else:
        post.likes.append(current_user)
        liked = True
        award_points(current_user.id, 'like', post.id, 1)
    db.session.commit()
    return jsonify({'likes': len(post.likes), 'liked': liked})

@app.route('/comunidad/post/<int:post_id>/pin', methods=['POST'])
@login_required
@admin_required
def pin_post(post_id):
    post = Post.query.get_or_404(post_id)
    post.pinned = not post.pinned
    db.session.commit()
    return redirect(url_for('post_detail', post_id=post_id))

@app.route('/comunidad/post/<int:post_id>/borrar', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not current_user.is_admin and post.user_id != current_user.id:
        abort(403)
    db.session.delete(post)
    db.session.commit()
    if request.is_json:
        return jsonify({'ok': True})
    return redirect(url_for('community'))

@app.route('/comunidad/post/<int:post_id>/editar', methods=['POST'])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not current_user.is_admin and post.user_id != current_user.id:
        abort(403)
    data = request.json if request.is_json else request.form
    title       = (data.get('title', '') or '').strip()
    content     = (data.get('content', '') or '').strip()
    category_id = data.get('category_id', None)
    if category_id:
        try:
            category_id = int(category_id)
        except (ValueError, TypeError):
            category_id = None
    if title and content:
        post.title       = title
        post.content     = content
        post.category_id = category_id or None
        db.session.commit()
    if request.is_json:
        return jsonify({'ok': True, 'title': post.title, 'content': post.content})
    return redirect(url_for('community'))

@app.route('/comunidad/comentario/<int:comment_id>/borrar', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not current_user.is_admin and comment.user_id != current_user.id:
        abort(403)
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/comunidad/comentario/<int:comment_id>/editar', methods=['POST'])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if not current_user.is_admin and comment.user_id != current_user.id:
        abort(403)
    content = (request.json.get('content', '') if request.is_json else request.form.get('content', '')).strip()
    if content:
        comment.content = content
        db.session.commit()
    return jsonify({'ok': True, 'content': comment.content})

@app.route('/comunidad/comentario/<int:comment_id>/like', methods=['POST'])
@login_required
def like_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if current_user in comment.likes:
        comment.likes.remove(current_user)
        liked = False
    else:
        comment.likes.append(current_user)
        liked = True
    db.session.commit()
    return jsonify({'likes': len(comment.likes), 'liked': liked})

# ── COURSES ───────────────────────────────────────────────────────────────────

@app.route('/cursos')
@login_required
def courses():
    all_courses  = Course.query.filter_by(is_published=True).order_by(Course.order.asc(), Course.created_at.asc()).all()
    enrolled_ids = {e.course_id for e in current_user.enrollments}
    # Progreso por curso
    completed_ids = {lp.lesson_id for lp in LessonProgress.query.filter_by(user_id=current_user.id).all()}
    progress = {}
    for c in all_courses:
        total = c.lesson_count
        if total == 0:
            progress[c.id] = 0
        else:
            done = sum(1 for s in c.sections for l in s.lessons if l.id in completed_ids)
            progress[c.id] = round(done * 100 / total)
    return render_template('courses/catalog.html',
                           courses=all_courses, enrolled_ids=enrolled_ids, progress=progress)

@app.route('/cursos/<int:course_id>')
@login_required
def course_detail(course_id):
    course   = Course.query.get_or_404(course_id)
    if not course.is_published and not current_user.is_admin:
        abort(404)
    enrolled = current_user.is_enrolled(course_id)
    return render_template('courses/detail.html', course=course, enrolled=enrolled)

@app.route('/cursos/<int:course_id>/inscribir', methods=['POST'])
@login_required
def enroll_free(course_id):
    course = Course.query.get_or_404(course_id)
    if not course.is_free:
        return redirect(url_for('checkout', course_id=course_id))
    if not current_user.is_enrolled(course_id):
        db.session.add(Enrollment(user_id=current_user.id, course_id=course_id))
        db.session.commit()
        flash('¡Inscrito correctamente!', 'success')
    return redirect(url_for('learn', course_id=course_id))

@app.route('/cursos/<int:course_id>/aprender')
@login_required
def learn(course_id):
    course = Course.query.get_or_404(course_id)
    if not current_user.is_enrolled(course_id) and not current_user.is_admin:
        if course.is_free:
            db.session.add(Enrollment(user_id=current_user.id, course_id=course_id))
            db.session.commit()
        else:
            return redirect(url_for('course_detail', course_id=course_id))

    lesson_id      = request.args.get('leccion', type=int)
    current_lesson = Lesson.query.get(lesson_id) if lesson_id else None
    if not current_lesson:
        for section in course.sections:
            if section.lessons:
                current_lesson = section.lessons[0]
                break

    completed_ids = {p.lesson_id for p in
                     LessonProgress.query.filter_by(user_id=current_user.id).all()}
    return render_template('courses/learn.html',
                           course=course,
                           current_lesson=current_lesson,
                           completed_ids=completed_ids)

@app.route('/cursos/<int:course_id>/completar/<int:lesson_id>', methods=['POST'])
@login_required
def complete_lesson(course_id, lesson_id):
    if not LessonProgress.query.filter_by(user_id=current_user.id, lesson_id=lesson_id).first():
        db.session.add(LessonProgress(user_id=current_user.id, lesson_id=lesson_id))
        db.session.commit()
        award_points(current_user.id, 'lesson', lesson_id, 3)
    return jsonify({'ok': True})

# ── LEADERBOARD ───────────────────────────────────────────────────────────────

@app.route('/clasificacion')
@login_required
def leaderboard():
    now = datetime.utcnow()
    period = request.args.get('periodo', 'mensual')
    if period == 'semanal':
        since = now - timedelta(weeks=1)
    elif period == 'anual':
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0)
    else:
        since = now.replace(day=1, hour=0, minute=0, second=0)
    ranking = get_leaderboard(since=since)
    my_pts  = sum(e.points for e in PointEvent.query.filter_by(user_id=current_user.id).filter(PointEvent.created_at >= since).all())
    # Total pts (all time) for level calculation
    my_total_pts = db.session.query(db.func.sum(PointEvent.points)).filter_by(user_id=current_user.id).scalar() or 0
    my_level     = get_level(my_total_pts)
    # Add level info to each ranking entry
    ranking_with_levels = []
    for user, pts in ranking:
        user_total = db.session.query(db.func.sum(PointEvent.points)).filter_by(user_id=user.id).scalar() or 0
        ranking_with_levels.append((user, pts, get_level(user_total)))
    return render_template('leaderboard.html', ranking=ranking_with_levels, period=period,
                           my_pts=my_pts, my_total_pts=my_total_pts, my_level=my_level)

# ── CALENDAR ──────────────────────────────────────────────────────────────────

@app.route('/calendario')
@login_required
def calendar():
    upcoming = LiveClass.query.filter(
        LiveClass.scheduled_at >= datetime.utcnow()
    ).order_by(LiveClass.scheduled_at).limit(5).all()
    return render_template('calendar/index.html', upcoming=upcoming)

@app.route('/calendario/data')
@login_required
def calendar_data():
    classes = LiveClass.query.all()
    events  = []
    for c in classes:
        end = c.scheduled_at + timedelta(minutes=c.duration_min) if c.duration_min else None
        events.append({
            'id':    c.id,
            'title': ('🔁 ' if c.recurrence != 'none' else '') + c.title,
            'start': c.scheduled_at.isoformat(),
            'end':   end.isoformat() if end else None,
            'extendedProps': {
                'description': c.description,
                'meet_url':    c.meet_url,
                'instructor':  c.instructor,
                'duration':    c.duration_min,
            },
            'backgroundColor': '#6366f1',
            'borderColor':     '#4f46e5',
        })
    return jsonify(events)

# ── PAYMENTS ──────────────────────────────────────────────────────────────────

@app.route('/checkout/<int:course_id>', methods=['POST'])
@login_required
def checkout(course_id):
    course = Course.query.get_or_404(course_id)
    if current_user.is_enrolled(course_id):
        return redirect(url_for('learn', course_id=course_id))

    stripe_key = app.config.get('STRIPE_SECRET_KEY', '')
    if not stripe_key:
        flash('Los pagos no están configurados aún. Contacta al administrador.', 'error')
        return redirect(url_for('course_detail', course_id=course_id))

    try:
        import stripe
        stripe.api_key = stripe_key
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': course.title, 'description': course.subtitle},
                    'unit_amount': int(course.price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('checkout_success', _external=True)
                        + f'?course_id={course_id}&session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=url_for('course_detail', course_id=course_id, _external=True),
        )
        return redirect(session.url)
    except Exception as e:
        flash(f'Error al procesar el pago: {e}', 'error')
        return redirect(url_for('course_detail', course_id=course_id))

@app.route('/checkout/exito')
@login_required
def checkout_success():
    course_id  = request.args.get('course_id', type=int)
    session_id = request.args.get('session_id', '')
    if course_id and not current_user.is_enrolled(course_id):
        db.session.add(Enrollment(user_id=current_user.id,
                                  course_id=course_id,
                                  stripe_session_id=session_id))
        db.session.commit()
    flash('¡Pago completado! Ya tienes acceso al curso.', 'success')
    return redirect(url_for('learn', course_id=course_id))

# ── ADMIN ─────────────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    stats = {
        'users':       User.query.count(),
        'courses':     Course.query.count(),
        'posts':       Post.query.count(),
        'enrollments': Enrollment.query.count(),
    }
    categories = Category.query.all()
    return render_template('admin/dashboard.html', stats=stats, categories=categories)

@app.route('/avatar/<int:user_id>')
def serve_avatar(user_id):
    user = User.query.get_or_404(user_id)
    if user.avatar_data:
        return send_file(io.BytesIO(user.avatar_data), mimetype=user.avatar_mime)
    abort(404)

@app.route('/curso/<int:course_id>/portada')
def serve_course_cover(course_id):
    course = Course.query.get_or_404(course_id)
    if course.cover_data:
        return send_file(io.BytesIO(course.cover_data), mimetype=course.cover_mime)
    abort(404)

@app.route('/comunidad/banner')
def serve_banner():
    s = get_settings()
    if s.community_image_data:
        return send_file(io.BytesIO(s.community_image_data), mimetype=s.community_image_mime)
    abort(404)

@app.route('/admin/ajustes', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    s = get_settings()
    if request.method == 'POST':
        s.academy_name          = request.form.get('academy_name', s.academy_name).strip()
        s.community_description = request.form.get('community_description', '').strip()
        s.link_url              = request.form.get('link_url', '').strip()
        s.link_text             = request.form.get('link_text', '').strip()
        img = request.files.get('community_image_file')
        if img and img.filename:
            data = img.read()
            s.community_image_data = data
            s.community_image_mime = img.mimetype or 'image/jpeg'
            s.community_image      = ''
        db.session.commit()
        flash('Ajustes guardados.', 'success')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html', s=s)

@app.route('/admin/categorias/nueva', methods=['POST'])
@login_required
@admin_required
def admin_new_category():
    name  = request.form.get('name', '').strip()
    color = request.form.get('color', '#6366f1')
    emoji = request.form.get('emoji', '💬')
    if name and not Category.query.filter_by(name=name).first():
        db.session.add(Category(name=name, color=color, emoji=emoji))
        db.session.commit()
        flash('Categoría creada.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categorias/<int:cat_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash('Categoría eliminada.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cursos')
@login_required
@admin_required
def admin_courses():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template('admin/courses.html', courses=courses)

@app.route('/admin/cursos/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_course():
    if request.method == 'POST':
        course = Course(
            title       = request.form.get('title', '').strip(),
            subtitle    = request.form.get('subtitle', '').strip(),
            description = request.form.get('description', '').strip(),
            price       = float(request.form.get('price', 0) or 0),
            is_published= 'published' in request.form,
            image       = request.form.get('image_url', '').strip(),
        )
        db.session.add(course)
        db.session.commit()
        flash('Curso creado. Ahora añade secciones y lecciones.', 'success')
        return redirect(url_for('admin_edit_course', course_id=course.id))
    return render_template('admin/new_course.html')

@app.route('/admin/cursos/<int:course_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_course(course_id):
    course = Course.query.get_or_404(course_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update':
            course.title        = request.form.get('title', course.title).strip()
            course.subtitle     = request.form.get('subtitle', course.subtitle).strip()
            course.description  = request.form.get('description', course.description).strip()
            course.price        = float(request.form.get('price', course.price) or 0)
            course.is_published = 'published' in request.form
            course.image        = request.form.get('image_url', course.image).strip()
            cover_file = request.files.get('cover_image')
            if cover_file and cover_file.filename:
                course.cover_data = cover_file.read()
                course.cover_mime = cover_file.mimetype or 'image/jpeg'
            db.session.commit()
            flash('Curso actualizado.', 'success')
        elif action == 'add_section':
            t = request.form.get('section_title', '').strip()
            if t:
                db.session.add(Section(course_id=course_id,
                                       title=t, order=len(course.sections)))
                db.session.commit()
                flash('Sección añadida.', 'success')
    return render_template('admin/edit_course.html', course=course)

@app.route('/admin/cursos/reordenar', methods=['POST'])
@login_required
@admin_required
def admin_reorder_courses():
    order = request.json.get('order', [])
    for i, course_id in enumerate(order):
        Course.query.filter_by(id=course_id).update({'order': i})
    db.session.commit()
    return ('', 204)

@app.route('/admin/secciones/reordenar', methods=['POST'])
@login_required
@admin_required
def admin_reorder_sections():
    order = request.json.get('order', [])
    for i, section_id in enumerate(order):
        Section.query.filter_by(id=section_id).update({'order': i})
    db.session.commit()
    return ('', 204)

@app.route('/admin/cursos/<int:course_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    try:
        # 1. Borrar LessonProgress y LessonImage de todas las lecciones
        for section in course.sections:
            for lesson in section.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
                db.session.execute(
                    text('DELETE FROM lesson_image WHERE lesson_id = :lid'),
                    {'lid': lesson.id}
                )
        db.session.flush()
        # 2. Borrar Enrollments del curso
        Enrollment.query.filter_by(course_id=course_id).delete()
        db.session.flush()
        # 3. Borrar el curso (cascade elimina sections → lessons → files)
        db.session.delete(course)
        db.session.commit()
        flash('Formación eliminada.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar: {e}', 'error')
    return redirect(url_for('courses'))

@app.route('/admin/seccion/<int:section_id>/leccion', methods=['POST'])
@login_required
@admin_required
def admin_add_lesson(section_id):
    section = Section.query.get_or_404(section_id)
    title   = request.form.get('title', '').strip()
    if title:
        db.session.add(Lesson(
            section_id   = section_id,
            title        = title,
            video_url    = request.form.get('video_url', '').strip(),
            description  = request.form.get('description', '').strip(),
            duration_min = int(request.form.get('duration', 0) or 0),
            order        = len(section.lessons),
            group_label  = request.form.get('group_label', '').strip() or None,
        ))
        db.session.commit()
        flash('Lección añadida.', 'success')
    return redirect(url_for('admin_edit_course', course_id=section.course_id))

@app.route('/admin/seccion/<int:section_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_section(section_id):
    section = Section.query.get_or_404(section_id)
    course_id = section.course_id
    db.session.delete(section)
    db.session.commit()
    flash('Sección eliminada.', 'success')
    return redirect(url_for('admin_edit_course', course_id=course_id))

@app.route('/admin/leccion/<int:lesson_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_lesson(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    course_id = lesson.section.course_id
    db.session.delete(lesson)
    db.session.commit()
    flash('Lección eliminada.', 'success')
    return redirect(url_for('admin_edit_course', course_id=course_id))

@app.route('/admin/leccion/<int:lesson_id>/archivo', methods=['POST'])
@login_required
@admin_required
def admin_add_lesson_file(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    name = request.form.get('name', '').strip()
    f    = request.files.get('file')
    if name and f and f.filename:
        data = f.read()
        db.session.add(LessonFile(
            lesson_id = lesson_id,
            name      = name,
            mimetype  = f.mimetype or 'application/octet-stream',
            size      = len(data),
            data      = data,
        ))
        db.session.commit()
        flash('Archivo subido correctamente.', 'success')
    return redirect(url_for('admin_edit_course', course_id=lesson.section.course_id))

@app.route('/archivo/<int:file_id>')
@login_required
def serve_lesson_file(file_id):
    f = LessonFile.query.get_or_404(file_id)
    return send_file(
        io.BytesIO(f.data),
        mimetype=f.mimetype,
        as_attachment=True,
        download_name=f.name,
    )

@app.route('/admin/archivo/<int:file_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_lesson_file(file_id):
    f = LessonFile.query.get_or_404(file_id)
    course_id = f.lesson.section.course_id
    db.session.delete(f)
    db.session.commit()
    flash('Archivo eliminado.', 'success')
    return redirect(url_for('admin_edit_course', course_id=course_id))


# ── Lesson rich-text description ──────────────────────────────────────────────

@app.route('/admin/leccion/<int:lesson_id>/descripcion', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_description(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson.description = request.form.get('description', '')
    db.session.commit()
    flash('Descripción guardada.', 'success')
    return redirect(url_for('learn', course_id=lesson.section.course_id,
                            leccion=lesson_id))


@app.route('/admin/leccion/<int:lesson_id>/video', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_video(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson.video_url = request.form.get('video_url', '').strip()
    db.session.commit()
    return ('', 204)   # AJAX — no redirect needed


@app.route('/admin/leccion/<int:lesson_id>/grupo', methods=['POST'])
@login_required
@admin_required
def admin_save_lesson_group(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson.group_label = request.form.get('group_label', '').strip() or None
    db.session.commit()
    return ('', 204)

@app.route('/admin/leccion/<int:lesson_id>/imagen', methods=['POST'])
@login_required
@admin_required
def admin_upload_lesson_image(lesson_id):
    """TinyMCE images_upload_url handler — returns JSON with image location."""
    Lesson.query.get_or_404(lesson_id)   # ensure lesson exists
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'no file'}), 400
    data = f.read()
    img = LessonImage(lesson_id=lesson_id, mimetype=f.mimetype or 'image/jpeg', data=data)
    db.session.add(img)
    db.session.commit()
    return jsonify({'location': url_for('serve_lesson_image', image_id=img.id)})


@app.route('/leccion-imagen/<int:image_id>')
@login_required
def serve_lesson_image(image_id):
    img = LessonImage.query.get_or_404(image_id)
    return send_file(io.BytesIO(img.data), mimetype=img.mimetype)


@app.route('/admin/clases')
@login_required
@admin_required
def admin_live_classes():
    classes = LiveClass.query.order_by(LiveClass.scheduled_at.desc()).all()
    return render_template('admin/live_classes.html', classes=classes)

@app.route('/admin/clases/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_live_class():
    if request.method == 'POST':
        date_str   = request.form.get('scheduled_at', '')
        recurrence = request.form.get('recurrence', 'none')
        try:
            scheduled_at = datetime.fromisoformat(date_str)
        except Exception:
            scheduled_at = datetime.utcnow()
        lc = LiveClass(
            title        = request.form.get('title', '').strip(),
            description  = request.form.get('description', '').strip(),
            scheduled_at = scheduled_at,
            duration_min = int(request.form.get('duration', 60) or 60),
            meet_url     = request.form.get('meet_url', '').strip(),
            instructor   = request.form.get('instructor', '').strip(),
            recurrence   = recurrence,
        )
        db.session.add(lc)
        db.session.flush()  # get lc.id before commit

        if recurrence in ('weekly', 'monthly'):
            iterations = 104 if recurrence == 'weekly' else 24
            for i in range(1, iterations + 1):
                if recurrence == 'weekly':
                    next_dt = scheduled_at + timedelta(weeks=i)
                else:
                    month = scheduled_at.month - 1 + i
                    year  = scheduled_at.year + month // 12
                    month = month % 12 + 1
                    import calendar
                    day = min(scheduled_at.day, calendar.monthrange(year, month)[1])
                    next_dt = scheduled_at.replace(year=year, month=month, day=day)
                db.session.add(LiveClass(
                    title        = lc.title,
                    description  = lc.description,
                    scheduled_at = next_dt,
                    duration_min = lc.duration_min,
                    meet_url     = lc.meet_url,
                    instructor   = lc.instructor,
                    recurrence   = recurrence,
                    parent_id    = lc.id,
                ))

        db.session.commit()
        # Notify all users about the new class
        all_users = User.query.filter_by(role='student').all()
        for u in all_users:
            notify(u.id, 'new_class',
                   f'📅 Nueva clase programada: "{lc.title}" el {lc.scheduled_at.strftime("%d %b a las %H:%M")}',
                   '/calendario')
        db.session.commit()
        label = {'weekly': 'semanal', 'monthly': 'mensual'}.get(recurrence, '')
        flash(f'Clase programada{"  (recurrencia " + label + ")" if label else ""}.', 'success')
        return redirect(url_for('admin_live_classes'))
    return render_template('admin/new_live_class.html')

@app.route('/admin/clases/<int:class_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_live_class(class_id):
    lc = LiveClass.query.get_or_404(class_id)
    if request.method == 'POST':
        lc.title        = request.form.get('title', '').strip()
        lc.description  = request.form.get('description', '').strip()
        lc.meet_url     = request.form.get('meet_url', '').strip()
        lc.instructor   = request.form.get('instructor', '').strip()
        lc.duration_min = int(request.form.get('duration', 60) or 60)
        try:
            lc.scheduled_at = datetime.fromisoformat(request.form.get('scheduled_at', ''))
        except Exception:
            pass
        update_all = request.form.get('update_all') == '1'
        if update_all and lc.parent_id is None:
            children = LiveClass.query.filter_by(parent_id=lc.id).all()
            for child in children:
                child.title        = lc.title
                child.description  = lc.description
                child.meet_url     = lc.meet_url
                child.instructor   = lc.instructor
                child.duration_min = lc.duration_min
        db.session.commit()
        flash('Clase actualizada.', 'success')
        return redirect(url_for('calendar'))
    scheduled_str = lc.scheduled_at.strftime('%Y-%m-%dT%H:%M')
    return render_template('admin/edit_live_class.html', lc=lc, scheduled_str=scheduled_str)

@app.route('/admin/clases/<int:class_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_live_class(class_id):
    lc = LiveClass.query.get_or_404(class_id)
    delete_all = request.form.get('delete_all') == '1'
    if delete_all or (lc.parent_id is None and lc.recurrence != 'none'):
        # Delete parent + all children
        LiveClass.query.filter(
            (LiveClass.id == class_id) | (LiveClass.parent_id == class_id)
        ).delete(synchronize_session=False)
    else:
        db.session.delete(lc)
    db.session.commit()
    flash('Clase eliminada.', 'success')
    return redirect(url_for('admin_live_classes'))

@app.route('/admin/usuarios')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending').order_by(User.created_at.desc()).all()
    active  = User.query.filter(User.status != 'pending').order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', pending=pending, active=active)

@app.route('/admin/usuarios/<int:user_id>/aprobar', methods=['POST'])
@login_required
@admin_required
def admin_approve_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'active'
    notify(user.id, 'approved',
           '✅ Tu acceso a la plataforma ha sido aprobado. ¡Ya puedes entrar!', '/')
    db.session.commit()
    flash(f'{user.username} ha sido aprobado.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/rechazar', methods=['POST'])
@login_required
@admin_required
def admin_reject_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = 'rejected'
    db.session.commit()
    flash(f'{user.username} ha sido rechazado.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/email', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_email():
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()
        target  = request.form.get('target', 'students')
        if not subject or not body:
            flash('El asunto y el mensaje son obligatorios.', 'error')
            return redirect(url_for('admin_email'))
        if not app.config.get('MAIL_USERNAME'):
            flash('Email no configurado. Añade MAIL_USERNAME y MAIL_PASSWORD en las variables de entorno de Railway.', 'error')
            return redirect(url_for('admin_email'))
        if target == 'all':
            users = User.query.filter_by(status='active').all()
        else:
            users = User.query.filter_by(status='active', role='student').all()
        try:
            for user in users:
                msg = MailMessage(
                    subject=subject,
                    recipients=[user.email],
                    html=f"""
                    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
                      <div style="background:#7c3aed;padding:24px;border-radius:12px 12px 0 0;text-align:center">
                        <h1 style="color:#fff;margin:0;font-size:20px">🎓 Marca Atractora</h1>
                      </div>
                      <div style="background:#fff;padding:32px;border:1px solid #e4e4e7;border-top:none;border-radius:0 0 12px 12px">
                        <h2 style="color:#18181b;margin-top:0">{subject}</h2>
                        <div style="color:#52525b;line-height:1.7;white-space:pre-wrap">{body}</div>
                        <hr style="border:none;border-top:1px solid #f4f4f5;margin:24px 0"/>
                        <p style="color:#a1a1aa;font-size:12px;margin:0">
                          Estás recibiendo este email porque eres miembro de Marca Atractora.
                        </p>
                      </div>
                    </div>
                    """
                )
                mail.send(msg)
            flash(f'✅ Email enviado a {len(users)} persona{"s" if len(users) != 1 else ""}.', 'success')
        except Exception as e:
            flash(f'Error al enviar el email: {str(e)}', 'error')
        return redirect(url_for('admin_email'))
    total_students = User.query.filter_by(status='active', role='student').count()
    total_all      = User.query.filter_by(status='active').count()
    return render_template('admin/email.html', total_students=total_students, total_all=total_all)

@app.route('/admin/usuarios/<int:user_id>/rol', methods=['POST'])
@login_required
@admin_required
def admin_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.role = 'admin' if user.role == 'student' else 'student'
        db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/suspender', methods=['POST'])
@login_required
@admin_required
def admin_toggle_status(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.status = 'active' if user.status != 'active' else 'suspended'
        db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/<int:user_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('No puedes eliminar tu propia cuenta.', 'error')
        return redirect(url_for('admin_users'))
    # Delete related data
    from models import Post, Comment, Enrollment, LessonProgress, Notification, PointEvent
    LessonProgress.query.filter_by(user_id=user.id).delete()
    Enrollment.query.filter_by(user_id=user.id).delete()
    Notification.query.filter_by(user_id=user.id).delete()
    PointEvent.query.filter_by(user_id=user.id).delete()
    for post in Post.query.filter_by(user_id=user.id).all():
        Comment.query.filter_by(post_id=post.id).delete()
        db.session.delete(post)
    Comment.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario eliminado correctamente.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/usuarios/nuevo', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    role     = request.form.get('role', 'student')

    if not username or not email or not password:
        flash('Todos los campos son obligatorios.', 'error')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(username=username).first():
        flash(f'El nombre de usuario "{username}" ya está en uso.', 'error')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(email=email).first():
        flash(f'El email "{email}" ya está registrado.', 'error')
        return redirect(url_for('admin_users'))
    if role not in ('student', 'admin'):
        role = 'student'

    new_user = User(username=username, email=email, role=role, status='active')
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    flash(f'✅ Usuario "{username}" creado correctamente como {"admin" if role == "admin" else "alumno"}.', 'success')
    return redirect(url_for('admin_users'))

# ── PÁGINA PÚBLICA DE MIEMBROS ────────────────────────────────────────────────

@app.route('/miembros')
@login_required
def members():
    users = (User.query
             .filter(User.status == 'active')
             .order_by(User.created_at.asc())
             .all())
    # Compute total pts and level for each member
    members_data = []
    for u in users:
        pts = db.session.query(db.func.sum(PointEvent.points)).filter_by(user_id=u.id).scalar() or 0
        members_data.append({'user': u, 'pts': pts, 'level': get_level(pts)})
    return render_template('members.html', members=members_data)

@app.route('/miembros/<int:user_id>/rol', methods=['POST'])
@login_required
@admin_required
def members_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.role = 'admin' if user.role == 'student' else 'student'
        db.session.commit()
        flash(f'{"⚙️ " + user.username + " ahora es admin." if user.role == "admin" else "🎓 " + user.username + " ya no es admin."}', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/expulsar', methods=['POST'])
@login_required
@admin_required
def members_suspend(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.status = 'suspended' if user.status == 'active' else 'active'
        db.session.commit()
        action = 'suspendido' if user.status == 'suspended' else 'reactivado'
        flash(f'Usuario {user.username} {action}.', 'success')
    return redirect(url_for('members'))

@app.route('/miembros/<int:user_id>/actividad')
@login_required
def member_activity(user_id):
    member = User.query.get_or_404(user_id)
    # Solo el propio usuario o un admin puede ver la actividad
    if not current_user.is_admin and current_user.id != user_id:
        abort(403)

    # Lecciones completadas
    completed = (db.session.query(LessonProgress, Lesson, Course)
                 .join(Lesson, LessonProgress.lesson_id == Lesson.id)
                 .join(Section, Lesson.section_id == Section.id)
                 .join(Course, Section.course_id == Course.id)
                 .filter(LessonProgress.user_id == user_id)
                 .order_by(LessonProgress.completed_at.desc())
                 .all())

    # Posts creados
    posts = Post.query.filter_by(user_id=user_id).order_by(Post.created_at.desc()).all()

    # Comentarios
    comments = (Comment.query.filter_by(user_id=user_id)
                .order_by(Comment.created_at.desc()).all())

    # Total de puntos
    total_pts = db.session.query(db.func.sum(PointEvent.points))\
                          .filter_by(user_id=user_id).scalar() or 0

    # Construir timeline unificado
    timeline = []
    for lp, lesson, course in completed:
        timeline.append({
            'date': lp.completed_at,
            'type': 'lesson',
            'icon': '📚',
            'text': f'Completó <strong>{lesson.title}</strong>',
            'sub':  course.title,
            'pts':  3,
        })
    for p in posts:
        timeline.append({
            'date': p.created_at,
            'type': 'post',
            'icon': '📝',
            'text': f'Publicó <strong>{p.title}</strong>',
            'sub':  None,
            'pts':  4,
        })
    for c in comments:
        timeline.append({
            'date': c.created_at,
            'type': 'comment',
            'icon': '💬',
            'text': 'Comentó en un post',
            'sub':  (c.content[:60] + '…') if len(c.content) > 60 else c.content,
            'pts':  2,
        })
    timeline.sort(key=lambda x: x['date'], reverse=True)

    # Estadísticas rápidas
    stats = {
        'lessons':  len(completed),
        'posts':    len(posts),
        'comments': len(comments),
        'points':   total_pts,
    }

    user_level = get_level(total_pts)
    return render_template('member_activity.html',
                           member=member, timeline=timeline, stats=stats,
                           user_level=user_level, total_pts=total_pts)

# ── ERROR PAGES ───────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


# ── INIT ──────────────────────────────────────────────────────────────────────

def seed_db():
    # SiteSettings — solo crea si no existe, nunca sobreescribe
    s = SiteSettings.query.first()
    if not s:
        s = SiteSettings(academy_name='Marca Atractora')
        db.session.add(s)
        db.session.commit()

    # Samuel — solo actualiza role/status, NUNCA resetea contraseña
    samuel = User.query.filter_by(email='samuelgavilant@gmail.com').first()
    if not samuel:
        samuel = User(username='samuel', email='samuelgavilant@gmail.com',
                      role='admin', status='active')
        samuel.set_password('Admin1234!')  # solo la primera vez
        db.session.add(samuel)
    else:
        # Solo garantizar que sea admin/activo, sin tocar contraseña ni otros datos
        if samuel.role != 'admin':
            samuel.role = 'admin'
        if samuel.status != 'active':
            samuel.status = 'active'
    db.session.commit()

    # Alumno de prueba — solo crea si no existe
    if not User.query.filter_by(email='alumno@prueba.com').first():
        test = User(username='alumno_prueba', email='alumno@prueba.com',
                    role='student', status='active')
        test.set_password('Prueba1234!')
        db.session.add(test)
        db.session.commit()

    # Categorías por defecto — solo si no hay ninguna
    if not Category.query.first():
        for name, color, emoji in [
            ('General',   '#6366f1', '💬'),
            ('Anuncios',  '#f59e0b', '📢'),
            ('Preguntas', '#10b981', '❓'),
            ('Recursos',  '#3b82f6', '📚'),
        ]:
            db.session.add(Category(name=name, color=color, emoji=emoji))
    db.session.commit()

    # ── FASE 1 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 1 Crea tu Marca Personal').first():
        fase1 = Course(
            title='FASE 1 Crea tu Marca Personal',
            subtitle='Branding, mensaje, storytelling y confianza personal',
            description='Creamos la marca personal desde los cimientos, el branding, el mensaje, el storytelling... y ganamos confianza en nosotros mismos.',
            is_published=True,
            price=0.0,
        )
        db.session.add(fase1)
        db.session.flush()  # get fase1.id

        _sections = [
            ('1. ¡Empieza aquí!', [
                ('1.1 Bienvenida.', 'https://vimeo.com/946336180', 8, ''),
            ]),
            ('2. Empezando a crear tu Marca Personal', [
                ('2. ¿Por qué algunas marcas personales no funcionan?', 'https://vimeo.com/923682632', 8, ''),
                ('2.1 Definiendo bien a tu cliente ideal.', 'https://vimeo.com/923683543', 8, ''),
                ('2.3 ¿Qué problemas tiene mi cliente ideal?', 'https://vimeo.com/923689838', 10, ''),
                ('2.4 Creando tu producto.', 'https://vimeo.com/1100556159', 19, ''),
            ]),
            ('3. Mentalidad', [
                ('3.1 Perder el miedo a la cámara y vencer el SDI', 'https://vimeo.com/952659718', 16, ''),
                ('3.2 Vencer la procrastinación y tener energía.', 'https://vimeo.com/952662836', 12, ''),
                ('3.1 Conócete a ti mismo, define tu identidad.', 'https://vimeo.com/1111044925', 49,
                 'Descubre quién eres realmente, cuáles son tus valores y cómo construir una identidad sólida que te diferencie.'),
                ('3.2 Aumenta tu autoestima y sé magnético.', 'https://vimeo.com/1111323128', 46, ''),
            ]),
            ('4. Empezando a comunicar', [
                ('4.1 Branding', 'https://vimeo.com/1111346889', 16, ''),
                ('4.2 Características de tu discurso', 'https://vimeo.com/1111358958', 17, ''),
                ('4.3 Mejorando tu oratoria.', 'https://vimeo.com/941893844', 25, ''),
                ('4.4 Perfeccionando tu oratoria.', 'https://vimeo.com/945886893', 15, ''),
                ('4.5 Aumenta tu carisma.', 'https://vimeo.com/1006883420', 15, ''),
                ('4.6 Storytelling', 'https://vimeo.com/1118503138', 16, ''),
            ]),
            ('PREGUNTAS FRECUENTES', [
                ('¿Tengo que salir siempre guapo en los vídeos?', 'https://vimeo.com/923693122', 3, ''),
                ('¿Cómo puedo ayudar a mi familia con mis vídeos?', 'https://vimeo.com/924536418', 2, ''),
                ('¿Cómo identifico qué quiere mi público objetivo?', 'https://vimeo.com/924539791', 1, ''),
                ('¿Tengo que tener prisa por monetizar?', 'https://vimeo.com/924547779', 2, ''),
                ('¿Cómo encontramos a nuestro enemigo?', 'https://vimeo.com/932504096', 2, ''),
                ('¿Varios buyer persona para un mismo producto?', 'https://vimeo.com/932507919', 1, ''),
                ('¿Hacer el vídeo de pie o sentado?', 'https://vimeo.com/941899689', 2, ''),
                ('Ritual antes de grabar un vídeo.', 'https://vimeo.com/941902712', 4, ''),
                ('¿Cómo descargar vídeo de Artgrid? Videos de stock.', 'https://vimeo.com/948304344', 1, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections, 1):
            sec = Section(course_id=fase1.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(
                    section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur,
                    description=l_desc, order=l_order,
                ))
        db.session.commit()
        print('[seed] FASE 1 course created with all sections and lessons.')

    # ── FASE 2 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 2. Creación del contenido.').first():
        fase2 = Course(
            title='FASE 2. Creación del contenido.',
            subtitle='Equipo, cámara, iluminación, guion y edición',
            description='Crea tu contenido de forma profesional aunque no sepas por donde empezar.',
            is_published=True,
            price=0.0,
        )
        db.session.add(fase2)
        db.session.flush()

        _sections2 = [
            ('¡Empezamos!', [
                ('Introducción.', 'https://vimeo.com/930832256', 1, ''),
            ]),
            ('1. ¿Qué equipo necesito?', [
                ('1.1 Equipo Básico.', 'https://vimeo.com/930832331', 3, ''),
                ('1.2 Equipo intermedio.', 'https://vimeo.com/930832438', 4, ''),
                ('1.3 Equipo avanzado.', 'https://vimeo.com/930832564', 3, ''),
            ]),
            ('2. Cómo funciona una cámara.', [
                ('2.1 Fundamentos básicos de la fotografía.', 'https://vimeo.com/933627600', 6, ''),
                ('2.2 Cómo funciona la cámara del móvil.', 'https://vimeo.com/935942415', 3, ''),
                ('2.3 Partes de una cámara.', 'https://vimeo.com/939183805', 6, ''),
                ('2.4 Todo lo que tienes que saber sobre el audio.', 'https://vimeo.com/944079519', 8, ''),
            ]),
            ('3. La iluminación.', [
                ('3.2 Esquema básico de iluminación.', 'https://vimeo.com/1005879073', 10, ''),
            ]),
            ('4. Creación del guion.', [
                ('4.1 Empezando a crear nuestro guion.', 'https://vimeo.com/1043141856', 18, ''),
            ]),
            ('5. Vamos a grabarnos.', [
                ('5.1 Fundamentos básicos del vídeo.', '', 0, ''),
                ('5.2 Todo listo para grabarnos.', '', 0, ''),
                ('5.3 Contenidos y organización.', '', 0, ''),
                ('5.4 ¿Cómo hablar nuestro guion?', '', 0, ''),
            ]),
            ('Edición en Capcut', [
                ('Edita con Capcut tus vídeos.', 'https://vimeo.com/925901001', 13, ''),
                ('Añadiendo subtítulos a tus vídeos con Capcut.', 'https://vimeo.com/925904432', 12, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections2, 1):
            sec = Section(course_id=fase2.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(
                    section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur,
                    description=l_desc, order=l_order,
                ))
        db.session.commit()
        print('[seed] FASE 2 course created with all sections and lessons.')

    # ── FASE 3 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 3. Atraer en redes sociales.').first():
        fase3 = Course(
            title='FASE 3. Atraer en redes sociales.',
            subtitle='YouTube, Instagram, TikTok y crecimiento exponencial',
            description='Empápate de como funciona Las redes sociales de principio a fin y crea una comunidad que genere tus primeros miles de suscriptores.',
            is_published=True, price=0.0,
        )
        db.session.add(fase3)
        db.session.flush()

        _sections3 = [
            ('1. YouTube', [
                ('1.0 Crear tu Canal de YouTube',                                  'https://vimeo.com/905919839',           16, ''),
                ('1.1 Como subir un vídeo a Youtube',                              'https://vimeo.com/924959549',            6, ''),
                ('1.2 Analíticas para despegar',                                   'https://vimeo.com/851008454/d29b98cc32', 47, ''),
                ('1.3 La mentalidad que necesitas para YT',                        'https://vimeo.com/855072488/4182033253', 60, ''),
                ('1.4 Títulos y Miniaturas',                                       'https://vimeo.com/857239956/678447f67a', 47, ''),
                ('1.4.1 Crear una miniatura con canva',                            'https://vimeo.com/948289425',            6, ''),
                ('1.4.2 Hacer miniaturas con Photoshop',                           'https://vimeo.com/956991589',            6, ''),
                ('1.5. Todo lo que debes saber sobre SEO',                         'https://vimeo.com/858909961/13bf9725bb', 41, ''),
                ('1.6. Copy y guiones para tus vídeos',                            'https://vimeo.com/863595428/b7de18d61b', 55, ''),
                ('1.6.1 Tres guiones para crear vídeo de Youtube',                 'https://vimeo.com/1164633315',           23, ''),
                ('1.7 Edita tus vídeos para retener la atención',                  'https://vimeo.com/891387027',            42, ''),
                ('1.8 Audio y música',                                             'https://vimeo.com/891394359',            40, ''),
                ('1.9 Trucos para YT',                                             'https://vimeo.com/891403759',            45, ''),
                ('1.10 Crossplatform',                                             'https://vimeo.com/891405274',            44, ''),
                ('1.11 CrossPlatform con ADS para conseguir trafico',              'https://vimeo.com/893240433',            34, ''),
                ('1.12 Todo sobre el copyright',                                   'https://vimeo.com/953984470',             9, ''),
                ('1.13 Configurar google adsense para monetizar',                  'https://vimeo.com/988825345',             7, ''),
                ('1.14 Configuración fiscal Google adsense',                       'https://youtu.be/wtX_YIN3KLU',           15, ''),
                ('1.15 Hacer crecer tu canal de YT con publicidad',                'https://vimeo.com/1054224518',           16, ''),
            ]),
            ('2. INSTAGRAM', [
                ('2.1 Primeros pases en la plataforma',                            'https://vimeo.com/901650539',            56, ''),
                ('2.2 Crear carruseles virales',                                   'https://vimeo.com/906272733',            66, ''),
                ('2.3 Creando comunidad en Historias de Instagram',                'https://vimeo.com/908386985',            61, ''),
                ('2.3.1 Historias destacas de Instagram',                          'https://vimeo.com/1013965873',            9, ''),
                ('2.4 Como crecer (rápido) Instagram (Fran Berges)',               'https://vimeo.com/911247204',           116, ''),
                ('2.5 Automatiza Instagram con Manychat',                          'https://vimeo.com/1135690799',           63, ''),
                ('2.6 Como y cuando hacer lives',                                  'https://vimeo.com/915327765',            58, ''),
                ('2.7 ¿Cómo hacer un reel viral?',                                'https://vimeo.com/933436126',             9, ''),
                ('2.8 Estructura vídeo viral',                                     'https://vimeo.com/933441911',             4, ''),
                ('2.9 Ganchos y copy writing para tu reel viral',                  'https://vimeo.com/933444451',            14, ''),
                ('2.10 Vuélvete viral con reels',                                  'https://vimeo.com/903836622',            75, ''),
                ('2.11 Ganchos visuales',                                          'https://vimeo.com/1125481695',           15, ''),
                ('2.12 Retención de la audiencia',                                 'https://vimeo.com/983628820',            23, ''),
            ]),
            ('3. TIKTOK', [
                ('3.0 Crea y configura tu cuenta de tiktok',                       'https://vimeo.com/957594124',             8, ''),
                ('3.1 Empezando en Tiktok',                                        'https://vimeo.com/889656484',            63, ''),
                ('3.2 Creando contenido para posicionarte en Tiktok',              'https://vimeo.com/892008535',            68, ''),
                ('3.3 Como vender en tiktok',                                      'https://vimeo.com/894269285',            62, ''),
            ]),
            ('4. CRECIMIENTO EXPONENCIAL EN RRSS', [
                ('4.1 Empezamos',                                                   'https://vimeo.com/1057991097',           18, ''),
                ('4.2 Avatar Especifico',                                           'https://vimeo.com/1059652394',           19, ''),
                ('4.3 Avatar 3.0',                                                  'https://vimeo.com/1060927420',           14, ''),
                ('4.4 Análisis de tu competencia',                                  'https://vimeo.com/1069644542',           14, ''),
                ('4.5 Validar un producto',                                         'https://vimeo.com/1142114582',           16, ''),
                ('4.6 Estrategia de venta En Redes Sociales',                       'https://vimeo.com/1139986255',           22, ''),
                ('4.7 Optimización de contenidos. Chat GPT',                        'https://vimeo.com/1013969721',           11, ''),
                ('Como crear comunidad y fidelidad',                                'https://vimeo.com/951552493',            15, ''),
            ]),
            ('5. PREGUNTAS Y DUDAS', [
                ('¿Cuál es la mejor hora para publicar vídeo? (YT)',               'https://vimeo.com/943679344',             2, ''),
                ('¿Es bueno hacer publicidad en Instagram pagada?',                'https://vimeo.com/951268479',             3, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections3, 1):
            sec = Section(course_id=fase3.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur, description=l_desc, order=l_order))
        db.session.commit()
        print('[seed] FASE 3 course created with all sections and lessons.')

    # ── FASE 4 course import ──────────────────────────────────────────────────
    if not Course.query.filter_by(title='FASE 4. Ventas de 10k/mes').first():
        fase4 = Course(
            title='FASE 4. Ventas de 10k/mes',
            subtitle='Producto, VSL, cierre de ventas, setters y copywriting',
            description='Todo lo que debes saber para vender por internet.',
            is_published=True, price=0.0,
        )
        db.session.add(fase4)
        db.session.flush()

        _sections4 = [
            ('1. Como crear tu producto o servicio.', [
                ('2.1 Utilizando Chat GPT para tu cliente ideal.',      'https://vimeo.com/1013973439',  13, ''),
                ('2.1 Definiendo tu producto para le venta masiva.',    'https://vimeo.com/1133845788',  30, ''),
                ('2.3 Precio y entrega del producto.',                  'https://vimeo.com/880312444',   86, ''),
                ('2.4 Recupera la inversión del Master de MP.',         'https://vimeo.com/882643397',   67, ''),
                ('2.5 Estrategia webinar.',                             'https://vimeo.com/885310951',   70, ''),
                ('2.6 Llamada a venta.',                                'https://vimeo.com/887431717',   63, ''),
            ]),
            ('2. Método VSL.', [
                ('1.1 Estrategia.',                                     'https://vimeo.com/920521443',   22, ''),
                ('1.2 Creando el VSL',                                  'https://vimeo.com/923461657',   23, ''),
                ('1.3 Estructura inicio VSL',                           'https://vimeo.com/1077030592',  25, ''),
                ('1.4 Parte media VSL',                                 'https://vimeo.com/1084013382',  17, ''),
                ('1.5 Parte final VSL',                                 'https://vimeo.com/1089509756',   8, ''),
                ('1.6 Como grabarse el VSL',                            'https://vimeo.com/1111930051',   7, ''),
                ('1.7 Optimización y ejemplos de VSL.',                 'https://vimeo.com/926614704',   17, ''),
                ('1.8 Como abrir y configurar Calendly',                'https://vimeo.com/1152825477',  13, ''),
            ]),
            ('3. Cierre de ventas.', [
                ('3.1 Creencias sobre la venta.',                       'https://vimeo.com/1133793652',  17, ''),
                ('3.2 Gana mucho dinero cerrando ventas.',              'https://vimeo.com/1111879667',  30, ''),
            ]),
            ('4. Escalar con setters', [
                ('3.1 ¿Qué es un setter?',                              'https://vimeo.com/1015648578',  16, ''),
                ('3.2 Funciones de un setter.',                         'https://vimeo.com/1019435606',  15, ''),
                ('3.3 Procedimiento para tus setters.',                 'https://vimeo.com/1020480794',  12, ''),
                ('3.4 Role Play conversaciones de setters.',            'https://vimeo.com/1025041421',  15, ''),
                ('3.5 ¿Qué requerimos de un setter?',                   'https://vimeo.com/1031864419',  12, ''),
            ]),
            ('5. Copywriter persuasivo', [
                ('4.0 Proposito de tu Marca.',                          'https://vimeo.com/1051602353',  15, ''),
                ('4.1 ¿Que és el copywriting?',                         'https://vimeo.com/1033089083',  17, ''),
                ('4.2 Como usar el copy en tu negocio.',                'https://vimeo.com/1036130237',  16, ''),
                ('4.3 Copy writing aplicado a la pagina web',           '',                               0, ''),
                ('4.4 Haciendo de tu web una maquina de ventas.',       'https://vimeo.com/1039186835',  15, ''),
                ('4.5 Copywriting para email marketing',                'https://vimeo.com/1042754681',  18, ''),
                ('4.6 Gestor de mailing',                               'https://vimeo.com/1042755748',   5, ''),
                ('4.7 Redactar con IA y automatizaciones de email.',    'https://vimeo.com/1056078465',  14, ''),
                ('4.8 Estrategia de Marca para comunicar.',             'https://vimeo.com/1046164226',  20, ''),
            ]),
            ('6. Facebook ADS.', [
                ('3.1 MasterClass conceptos Facebook e Instagram ADS',  'https://vimeo.com/962585274',   87, ''),
                ('3.2 Masterclass FACEBOOK ADS 2 28-ago-2024',          'https://vimeo.com/1005964444',  57, ''),
            ]),
            ('7. Afiliación.', [
                ('Amazon afiliados + audible',                          'https://vimeo.com/1028207666',   4, ''),
            ]),
            ('PREGUNTAS FRECUENTES', [
                ('¿En que plataforma subimos nuestros cursos?',         'https://vimeo.com/932500762',    2, ''),
                ('¿tengo 2 buyerpersona creo dos productos?',           'https://vimeo.com/951268043',    8, ''),
            ]),
        ]

        for s_order, (sec_title, lessons) in enumerate(_sections4, 1):
            sec = Section(course_id=fase4.id, title=sec_title, order=s_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url, l_dur, l_desc) in enumerate(lessons, 1):
                db.session.add(Lesson(section_id=sec.id, title=l_title,
                    video_url=l_url, duration_min=l_dur, description=l_desc, order=l_order))
        db.session.commit()
        print('[seed] FASE 4 course created with all sections and lessons.')

    # FASE 5 is handled exclusively by seed_fase5() — do NOT create it here


def fix_fase5_carpeta6():
    """Ensure '6 PROGRAMA TU MENTE PARA LA ABUNDANCIA' exists in FASE 5
    with all 7 lessons (3 originals + 4 finanzas). Creates the section if missing."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return

        all_lessons = [
            ('6.1 Atraer Abundancia y Dinero Cambiando tu Mente', 'https://youtu.be/l27PoZo_rpQ', 54),
            ('6.2 Tu vieja identidad sobre el dinero.',           'https://youtu.be/nG9F_gKpTTM', 31),
            ('6.3 El Dinero Está En La Relación Con Tu Padre',    'https://youtu.be/7samMzQPuzo', 18),
            ('¿Qué es el dinero?',                                'https://youtu.be/jBd3M20EQic', 11),
            ('¿Cómo ahorrar?',                                    'https://youtu.be/gN2Z6gVwsYA', 13),
            ('Gestiona tus finanzas personales.',                 'https://youtu.be/BbSj95aKAW4', 11),
            ('¿En que invertir?',                                 'https://youtu.be/L-yGqUTphN0', 16),
        ]

        sec = Section.query.filter_by(course_id=course.id,
                                      title='6 PROGRAMA TU MENTE PARA LA ABUNDANCIA').first()
        if not sec:
            # Section was deleted by an earlier seed — recreate it at a high order
            max_order = db.session.query(db.func.max(Section.order)).filter_by(
                course_id=course.id).scalar() or 0
            sec = Section(course_id=course.id,
                          title='6 PROGRAMA TU MENTE PARA LA ABUNDANCIA',
                          order=max_order + 1)
            db.session.add(sec)
            db.session.flush()
            print('[fix_fase5_carpeta6] Sección recreada.')

        existing_titles = {l.title for l in sec.lessons}
        max_l_order = max((l.order for l in sec.lessons), default=0)
        added = 0
        for title, url, dur in all_lessons:
            if title not in existing_titles:
                max_l_order += 1
                db.session.add(Lesson(section_id=sec.id, title=title,
                                      video_url=url, duration_min=dur, order=max_l_order))
                added += 1
        if added:
            db.session.commit()
            print(f'[fix_fase5_carpeta6] Añadidas {added} lecciones a carpeta 6.')
        else:
            print('[fix_fase5_carpeta6] Carpeta 6 ya estaba completa.')
    except Exception as e:
        print(f'[fix_fase5_carpeta6] ERROR: {e}')
        db.session.rollback()


def seed_descriptions():
    """Populate lesson descriptions using LESSON_DESCRIPTIONS dict via raw SQL."""
    updated = 0
    try:
        with db.engine.connect() as conn:
            for (course_title, lesson_title), html in LESSON_DESCRIPTIONS.items():
                row = conn.execute(text(
                    """SELECT l.id, l.description FROM lesson l
                       JOIN section s ON s.id = l.section_id
                       JOIN course c ON c.id = s.course_id
                       WHERE c.title = :ct AND l.title = :lt
                       LIMIT 1"""
                ), {'ct': course_title, 'lt': lesson_title}).fetchone()
                if row is None:
                    print(f'[seed_desc] WARNING - not found: {lesson_title!r}')
                    continue
                lesson_id, current_desc = row[0], row[1] or ''
                if len(current_desc) < 500:  # not yet rich
                    conn.execute(text(
                        'UPDATE lesson SET description = :html WHERE id = :lid'
                    ), {'html': html, 'lid': lesson_id})
                    updated += 1
                    print(f'[seed_desc] Updated id={lesson_id}: {lesson_title}')
            if updated:
                conn.commit()
                print(f'[seed_desc] Done — {updated} lesson(s) updated.')
            else:
                print('[seed_desc] All descriptions already rich.')
    except Exception as e:
        print(f'[seed_desc] ERROR: {e}')


# ── Forzar actualización de descripciones (solo admin) ───────────────────────

# Map of (course_title, lesson_title) → html description
LESSON_DESCRIPTIONS = {
    ('FASE 1 Crea tu Marca Personal', '1.1 Bienvenida.'): """<h2>¡Bienvenido!</h2>
<p>Estás en el lugar indicado para cambiar tu vida.</p>
<p>Lo más difícil ya lo has hecho, tener la humildad de aprender y formarte, así que mis más sincera enhorabuena.</p>
<p>Si tienes cualquier duda puedes anotarla en este formulario: <a href="https://forms.gle/FQ3L3W7E8Q8sNtaH8" target="_blank" rel="noopener noreferrer">https://forms.gle/FQ3L3W7E8Q8sNtaH8</a> — las dudas se resuelven los martes a las 20h (hora de España).</p>
<p>Tu camino empieza aquí y va a ser de dentro hacia afuera.</p>
<p><strong>VAMOS.</strong></p>""",

    ('FASE 1 Crea tu Marca Personal', '3.1 Conócete a ti mismo, define tu identidad.'): """<p>Los fundamentos para crear una Marca Personal se basan en:</p>
<ul>
  <li><strong>La identidad:</strong> Todo aquello que te define, desde tu manera de hablar, tu vestimenta, el color que utilizas para tus videos, tu peinado... También todo lo que está dentro de ti, como tu seguridad, la dureza del mensaje, la dulzura... Todo esto se puede entrenar y moldear para ir definiendo nuestra identidad.</li>
  <li><strong>Valor:</strong> El valor es lo que ayudas a los demás con tu mensaje, la identidad es lo que más le ayuda al otro y lo que más transmite, pero luego esta el mensaje. La información es la vía por la cual nosotros vamos a llegar al otro, un mensaje autentico, nuevo, fresco, creativo... va a atraer a nuestra audiencia.</li>
  <li><strong>Estrategia:</strong> La estrategia seria conocer el medio (las redes sociales), tener una fuente de ingresos, crear comunidad... Todo lo que tiene que ver con lo mecánico y los sistemas.</li>
</ul>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/5cd82c8ec20d4829a265e27212e9110e185a4ff7c916483398c51d6d679f9659-md.jpg" alt="Identidad, Valor, Estrategia" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/693fe4b714044043a07b4d3f11c3974a5d456466f2274b5797a886ff16ab5a9d-md.jpg" alt="" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/f21500dd3a41416a94d13f619749cae0e794403bde3b48b4bfbb5336b8520bed-md.jpg" alt="" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Singularidad y diferenciación</strong></p>
<p>Encuentra elementos que solo tú compartas y que formen parte de tu marca, podría ser un deporte, una actividad, una forma de vestir, una bebida... algo con lo que tu audiencia se identifique.</p>
<p>Tu historia es algo único, comparte tu evolución y tu historia de vida, de superación, te recomiendo que apliques el viaje del héroe a tu historia.</p>

<p><strong>Define qué es lo que haces</strong></p>
<p>Es importante definir que es lo que haces con una frase, para cuando alguien te pregunte o tengas que poner la descripción en tu Instagram o YouTube sepas directamente que poner. Ejemplo: <em>"Soy Samuel divulgador de la consciencia para generar un impacto en las personas y que estas puedan mejorar su vida y hacer de este mundo un lugar mejor."</em></p>
<p>Así mismo te recomiendo que apuntes en una <strong>lista los valores para tu marca.</strong> El valor, la integridad, la libertad, el amor... Para que sea lo que guíe tu camino y comuniques desde ahí.</p>

<p><strong>Haz una breve lista sobre qué problema resuelves</strong></p>
<p>Es fundamental determinar quién es la persona que vas a ayudar, aunque aún sea un poco pronto y lo iremos construyendo poco a poco a lo largo del master, coged ese arquetipo de persona que vais a ayudar con vuestro contenido y luego con vuestro proyecto.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/45ad3f1af90a468e8c6a2880c20e6bf16adc5e1452b640e295df14b583e8ef28-md.jpg" alt="Ejercicio autoestima" style="max-width:100%;border-radius:8px;margin:.75em 0"/>
<p>Esto es un poco avanzado para el punto en el que estás, pero son términos que está bien que te vayan sonando. No te preocupes si esto no te sale, es lo que más vamos a trabajar a lo largo del máster.</p>

<p><strong>Branding</strong></p>
<p>Aquí nos metemos de lleno en la imagen de marca. Es sencillo, fíjate en quién te fijas y ve implementándolo en ti con tu estilo natural. Si te ves a ti mismo en tu mejor versión, ¿qué peinado lleva? ¿Cómo viste? ¿Qué complementos se pone?</p>
<ul>
  <li><strong>Tipo de letra.</strong></li>
  <li><strong>Ropa.</strong></li>
  <li><strong>Decoración.</strong></li>
  <li><strong>Peinado.</strong></li>
  <li><strong>Colores.</strong></li>
  <li><strong>Estilo.</strong></li>
  <li><strong>Energía.</strong></li>
</ul>
<p>Todas estas cualidades y más que se te vayan ocurriendo las puedes ir definiendo y poniendo en un documento con imágenes, recortes, anotaciones...</p>

<p><strong>¿Qué te diferencia de los demás?</strong></p>
<p>Haz una lista de tus cualidades, de las cosas que crees que eres mejor que el resto. Haz lo mismo con lo que creas que te cuesta más. Tener la virtud de poner luz en nuestras sombras nos hace tener más información para tomar mejores decisiones en un futuro.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/d3ca0dbc6b804e0fa5c5e3cd8b1452c7612202d3bdd34eb38441697eb0d12457-md.jpg" alt="Ejercicio autoestima 2" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Autoconocimiento</strong></p>
<p>Te insto a que investigues sobre el eneagrama, los arquetipos de Carl Jung o cualquier herramienta de autoconocimiento, esto te dará una ventaja competitiva brutal.</p>

<p><strong>Potencia tu marca</strong></p>
<p>Mira en qué tribu social perteneces, quién es tu bando contrario, con quién te identificas. Esto puede definir mucho tu nicho y puedes hacer que tus seguidores te tengan como ídolo y referente en su causa.</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/0746927daba241e5a66447ca4d0ae9716f4cfe9901eb4e1ea1a02c847fac3ee3-md.jpg" alt="Tribu" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Haz de tu vida una película.</strong></p>
<p>Internet nos da una ventana al mundo, pero solo una ventana, cuida muy bien qué aparece, no porque tengas que impostar nada, ni ser una persona que no eres, sino que le pongas el alma a aquello que dejas ver por la ventana.</p>

<p><strong>Haz tu carta de diseño humano</strong></p>
<p><a href="https://freehumandesignchart.com/" target="_blank" rel="noopener noreferrer nofollow">https://freehumandesignchart.com/</a></p>
<p>Y lo comentamos en la llamada personal.</p>

<p><strong>Proyección</strong></p>
<p>¿Quién te inspira? ¿Cuál es la cualidad? (Para este ejercicio ver el vídeo)</p>
<img src="https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/99c0474c9e4f437e9f8f3931c8f1a66d856194f4da5147abb5972b2cbdc10b11-md.jpg" alt="Proyección" style="max-width:100%;border-radius:8px;margin:.75em 0"/>

<p><strong>Visualiza</strong></p>
<p>Este ejercicio es fundamental, visualiza dónde quieres estar, cuáles son tus objetivos, y siéntete como si ya los hubieras conseguido.</p>
<p>Puedes hacerte una visual board o visualizarte cuando no estés haciendo ninguna tarea intelectual. Da igual como lo hagas, pero define con todo lujo de detalles dónde quieres llegar y qué quieres hacer.</p>""",
}


def seed_fase5():
    """Create the FASE 5 MENTALIDAD course with all sections and lessons if it doesn't exist."""
    try:
        if Course.query.filter_by(title='FASE 5 MENTALIDAD').first():
            return

        course = Course(
            title='FASE 5 MENTALIDAD',
            subtitle='Todo el desarrollo personal que necesitas para ser autentico y volverte magnético y viral.',
            is_published=True,
            price=0.0,
            image='https://assets.skool.com/f/fbc26fa852864d56b36a10f8d8f3a4a1/c8e27db218c843f5af0e2b02f6daba519f0cdd8d0a1e4ceb990888649241cae6.jpg',
        )
        db.session.add(course)
        db.session.flush()

        _sections = [
            ('1 Hábitos para la paz mental', 0, [
                ('1.1 Introduccion',                        'https://vimeo.com/749878520'),
                ('1.2 Como realizar este curso',            'https://vimeo.com/749881629/e2cbd4caf7'),
                ('1.3 ¿Porque cuesta tanto cambiar?',      None),
                ('2.1 El presente',                        None),
                ('2.1.1 Profundizando en la meditacion',   None),
                ('2.2 Pensar menos, sentir mas',           None),
                ('2.3 Decido vivir este momento.',         None),
                ('2.3.1 Sanar el pasado',                  None),
                ('3.1 La Aceptacion',                      None),
                ('4.1 Como se forma el ego',               None),
                ('4.1.2 ¿Para que?',                       None),
                ('4.1.1 Creencias',                        None),
                ('4.2 Niño Interior',                      None),
                ('5.1 La ilusion de uno mismo',            None),
                ('5.2 Recogida de proyecciones',           None),
                ('5.1.1 Reprogramar la mente',             None),
                ('6.1 Habitos',                            None),
                ('7.1 Mindfull eating.',                   None),
                ('7.2.1 Alimentacion consciente',          None),
                ('7.2.2 Alimentacion consciente',          None),
                ('8.1 Iniciacion a la respiracion',        None),
                ('8.2 Respiracion consciente',             None),
                ('9.1 Energia sexual',                     None),
                ('9.2 Sexualidad consciente',              None),
                ('10. Super habitos',                      None),
                ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
            ]),
            ('2. Encuentra tu proposito', 1, [
                ('1. ¿A que me dedico?',                   'https://vimeo.com/733891828/9bc0bc2936'),
                ('2. Hoy vas a encontrar tu propósito.',   'https://vimeo.com/738144908/10eafd0ae1'),
                ('3. Tu don y tu talento.',                'https://vimeo.com/738152347/ea9a721a10'),
                ('4. El camino al propósito.',             'https://vimeo.com/733930732/8b5e4907c4'),
                ('5. El ego.',                             'https://vimeo.com/734454135/6eceed3077'),
                ('6. Monetiza tu pasión.',                 'https://vimeo.com/738158599/0f607a9a0d'),
            ]),
            ('5 REPROGRAMACIÓN MENTAL NIÑO INTERIOR', 2, [
                ('1. El Ambiente donde te programaste.',   'https://vimeo.com/1133998226'),
                ('2. La emoción que viviste de niño.',     'https://vimeo.com/1136253801'),
                ('3. Como se forja el personaje',          'https://vimeo.com/1138661240'),
                ('4. Desprogramando la mente',             'https://vimeo.com/1140914534'),
                ('5. Encuentro con el niño.',              'https://vimeo.com/1143207136'),
                ('6. Recogida de proyecciones.',           'https://vimeo.com/1145401240'),
                ('7. El personaje',                        'https://vimeo.com/1147459657'),
                ('8. El sistema del personaje.',           'https://vimeo.com/1152337303'),
                ('9. Final niño interior.',                'https://vimeo.com/1154444356'),
            ]),
            ('6 PROGRAMA TU MENTE PARA LA ABUNDANCIA', 3, [
                ('6.1 Atraer Abundancia y Dinero Cambiando tu Mente', 'https://youtu.be/l27PoZo_rpQ'),
                ('6.2 Tu vieja identidad sobre el dinero.',           'https://youtu.be/nG9F_gKpTTM'),
                ('6.3 El Dinero Está En La Relación Con Tu Padre',    'https://youtu.be/7samMzQPuzo'),
            ]),
        ]

        for sec_title, sec_order, lessons in _sections:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url or '',
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_fase5] FASE 5 MENTALIDAD course created with all sections and lessons.')
    except Exception as e:
        print(f'[seed_fase5] ERROR: {e}')
        db.session.rollback()


def seed_bono_habitos():
    """Ensure FASE 5 has a single '1. Habitos para la paz mental' section
    with all 26 lessons. Cleans up any old sub-section structure."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return

        # Guard: flat section already exists → nothing to do
        if Section.query.filter_by(course_id=course.id,
                                   title='1. Habitos para la paz mental').first():
            return

        # --- Cleanup: remove any sub-sections OR old flat section ---
        # Matches old names with or without accents / numbering variants
        old_names = [
            '1 Habitos para la paz mental',
            '1. Introduccion', '1. Introducción',
            '2. Aqui y ahora.', '2. Aquí y ahora.',
            '3. Aceptacion.', '3. Aceptación.',
            '4. La Mascara.',
            '5. La imagen de uno mismo.',
            '6. Habitos.', '6. Hábitos.',
            '7. Alimentacion.', '7. Alimentación.',
            '8. Respiracion.', '8. Respiración.',
            '9. Energia sexual.',
            '10. Super habitos y cierre.', '10. Super hábitos y cierre.',
        ]
        # Collect all sections to remove (by name or by order 0-9)
        secs_to_remove = []
        for name in old_names:
            sec = Section.query.filter_by(course_id=course.id, title=name).first()
            if sec and sec not in secs_to_remove:
                secs_to_remove.append(sec)
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 0, Section.order <= 9).all():
            if sec not in secs_to_remove:
                secs_to_remove.append(sec)

        # Delete LessonProgress first to avoid FK constraint errors
        for sec in secs_to_remove:
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
        db.session.flush()

        for sec in secs_to_remove:
            db.session.delete(sec)
        db.session.flush()

        # Reorder remaining sections compactly starting at 2
        remaining = Section.query.filter_by(course_id=course.id).order_by(Section.order).all()
        for i, sec in enumerate(remaining):
            sec.order = i + 2
        db.session.flush()

        # Create the single flat section at order 1
        new_sec = Section(course_id=course.id,
                          title='1. Habitos para la paz mental', order=1)
        db.session.add(new_sec)
        db.session.flush()

        _lessons = [
            ('1.1 Introduccion',                      'https://vimeo.com/749878520'),
            ('1.2 Como realizar este curso',           'https://vimeo.com/749881629/e2cbd4caf7'),
            ('1.3 Porque cuesta tanto cambiar',        'https://vimeo.com/749884233/1e320d927f'),
            ('2.1 El presente',                        'https://vimeo.com/749887187/ffba41cccb'),
            ('2.1.1 Profundizando en la meditacion',   'https://vimeo.com/749890461/a00d1504e0'),
            ('2.2 Pensar menos, sentir mas',           'https://vimeo.com/749888068/213b9224b8'),
            ('2.3 Decido vivir este momento',          'https://vimeo.com/749888144/f7e415bb2e'),
            ('2.3.1 Sanar el pasado',                  'https://vimeo.com/749892494/b00e80badc'),
            ('3.1 La Aceptacion',                      'https://vimeo.com/749893948/5b13abd2ba'),
            ('4.1 Como se forma el ego',               'https://vimeo.com/749894742/1fdf42c662'),
            ('4.1.2 Para que',                         'https://vimeo.com/749894828/5cdc074054'),
            ('4.1.1 Creencias',                        'https://vimeo.com/749894807/57e7fcf8e1'),
            ('4.2 Nino Interior',                      'https://vimeo.com/749897628/38e3e3a08d'),
            ('5.1 La ilusion de uno mismo',            'https://vimeo.com/749899407/9cef2eec80'),
            ('5.2 Recogida de proyecciones',           'https://vimeo.com/749901468/84733c5bfc'),
            ('5.1.1 Reprogramar la mente',             'https://vimeo.com/749899500/3357242a3d'),
            ('6.1 Reprogramar la mente',               'https://vimeo.com/749899500/3357242a3d'),
            ('7.1 Mindfull eating',                    'https://vimeo.com/749904175/162461a778'),
            ('7.2.1 Alimentacion consciente',          'https://vimeo.com/749906274/43a19e519b'),
            ('7.2.2 Alimentacion consciente 2',        'https://vimeo.com/749906363/e00d5f300d'),
            ('8.1 Iniciacion a la respiracion',        'https://vimeo.com/749908687/b0c7e3572b'),
            ('8.2 Respiracion consciente',             'https://vimeo.com/749909287/19c2af632c'),
            ('9.1 Energia sexual',                     'https://vimeo.com/749910594/f5716a6412'),
            ('9.2 Sexualidad consciente',              'https://vimeo.com/749910707/f8b9f064cf'),
            ('10. Super habitos',                      'https://vimeo.com/749912323/da572845b1'),
            ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_bono_habitos] Seccion plana "1. Habitos para la paz mental" creada con 26 lecciones.')
    except Exception as e:
        print(f'[seed_bono_habitos] ERROR: {e}')
        db.session.rollback()


def seed_bono_organizacion():
    """Insert '3. Organización para creadores' into FASE 5 at order 12,
    shifting any existing sections with order >= 12 up by 1."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return
        if Section.query.filter_by(course_id=course.id, title='3. Organización para creadores').first():
            return
        # Shift sections with order >= 12 up by 1 to make room
        for sec in Section.query.filter_by(course_id=course.id).filter(Section.order >= 12).all():
            sec.order += 1
        db.session.flush()

        sec = Section(course_id=course.id, title='3. Organización para creadores', order=12)
        db.session.add(sec)
        db.session.flush()

        _org_lessons = [
            ('1. Introduccion (Valores)',                    'https://vimeo.com/792949917/3501d6b099'),
            ('2. La importancia de la organizacion',         'https://vimeo.com/792950165/8bb066c84e'),
            ('3. La concentracion',                          'https://vimeo.com/792950514/2d6ae9c0ae'),
            ('4. Distracciones',                             'https://vimeo.com/792950884/59d066a593'),
            ('5. Ladrones de tiempo',                        'https://vimeo.com/792951168/1b496771ac'),
            ('6. Decir que no',                              'https://vimeo.com/792951669/aa2eeef11f'),
            ('7. Tu energia',                                'https://vimeo.com/792952021/c4c9d1a216'),
            ('8. Multitarea y mision de vida',               'https://vimeo.com/792952713/45ca8844e5'),
            ('9. Empezar a organizar nuestra vida',          'https://vimeo.com/792953317/c1f6008c6f'),
            ('10. Tus 4 Roles',                              'https://vimeo.com/792954676/9bec279e9f'),
            ('11. Objetivos',                                'https://vimeo.com/792955039/28608189dd'),
            ('12. Los 3 objetivos del dia',                  'https://vimeo.com/792955692/96cda7441c'),
            ('13. Cortafuegos',                              'https://vimeo.com/792956258/e7e477d773'),
            ('14. Capsulas',                                 'https://vimeo.com/792956721/8f32e161d6'),
            ('15. Comprometerse',                            'https://vimeo.com/792957399/ce80a7c8de'),
            ('16. Minimalismo',                              'https://vimeo.com/792957978/e9eabf25b1'),
            ('17. Delegar y optimizar',                      'https://vimeo.com/792959094/c5962fdb7e'),
            ('18. Automatizacion',                           'https://vimeo.com/792960902/00db3c90ee'),
            ('19. El correo electronico',                    'https://vimeo.com/792962419/7bc02d0c61'),
            ('20. Final + Preguntas y Respuestas',           'https://vimeo.com/792962621/f3aff50888'),
            ('21. Preguntas y Respuestas 1',                 'https://vimeo.com/792964436/ca0d78f106'),
            ('22. Preguntas y Respuestas 2',                 'https://vimeo.com/792949615/f89a85ec32'),
            ('23. BONO Como funciona Notion',                'https://youtu.be/_W_hyG5qNq0?si=bkSO6HcfUjGK-WM7'),
            ('24. Organizacion para creadores de contenido', 'https://vimeo.com/952664864'),
        ]
        for l_order, (l_title, l_url) in enumerate(_org_lessons):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_bono_organizacion] Seccion "3. Organizacion para creadores" anadida a FASE 5.')
    except Exception as e:
        print(f'[seed_bono_organizacion] ERROR: {e}')
        db.session.rollback()


def seed_liberacion_emocional():
    """Insert '4. Liberacion emocional' into FASE 5 with 18 lessons."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            return
        if Section.query.filter_by(course_id=course.id,
                                   title='4. Liberacion emocional').first():
            return
        # Shift sections with order >= 13 up by 1 to make room at order 13
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 13).all():
            sec.order += 1
        db.session.flush()

        sec = Section(course_id=course.id, title='4. Liberacion emocional', order=13)
        db.session.add(sec)
        db.session.flush()

        _lessons = [
            ('Bienvenidos',   'https://vimeo.com/719536396'),
            ('Capitulo 1',    'https://vimeo.com/719536479'),
            ('Capitulo 2',    'https://vimeo.com/719536500'),
            ('Capitulo 3',    'https://vimeo.com/719536514'),
            ('Capitulo 4',    'https://vimeo.com/719536558'),
            ('Capitulo 5',    'https://vimeo.com/721359695'),
            ('Capitulo 6',    'https://vimeo.com/719536688'),
            ('Capitulo 7',    'https://vimeo.com/719536704'),
            ('Capitulo 8',    'https://vimeo.com/719536728'),
            ('Capitulo 9',    'https://vimeo.com/720549604'),
            ('Capitulo 10',   'https://vimeo.com/720555536'),
            ('Capitulo 11',   'https://vimeo.com/720564299'),
            ('Capitulo 12',   'https://vimeo.com/720564393'),
            ('Capitulo 13',   'https://vimeo.com/720564485'),
            ('Capitulo 14',   'https://vimeo.com/721350229'),
            ('Capitulo 15',   'https://vimeo.com/721350331'),
            ('Capitulo 16',   'https://vimeo.com/721350382'),
            ('Capitulo 17',   'https://vimeo.com/803924439'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        print('[seed_liberacion_emocional] Seccion "4. Liberacion emocional" creada con 18 lecciones.')
    except Exception as e:
        print(f'[seed_liberacion_emocional] ERROR: {e}')
        db.session.rollback()


_PREMIERE_LESSONS = [
    ('Introduccion',  'https://vimeo.com/828873589/ae73df2542'),
    ('Capitulo 1',    'https://vimeo.com/828874651/6bd0a0d35d'),
    ('Capitulo 2',    'https://vimeo.com/828874928/5bdc98bce0'),
    ('Capitulo 3',    'https://vimeo.com/828875116/62f0232ea8'),
    ('Capitulo 4',    'https://vimeo.com/828876182/ab62fe56c0'),
    ('Capitulo 5',    'https://vimeo.com/828877925/aa07c9debb'),
    ('Capitulo 6',    'https://vimeo.com/828879749/b7372fbba9'),
    ('Capitulo 7',    'https://vimeo.com/828881488/3a76019d7e'),
    ('Capitulo 8',    'https://vimeo.com/828883812/0b8033b8e7'),
    ('Capitulo 9',    'https://vimeo.com/828885212/75625f9a8d'),
    ('Capitulo 10',   'https://vimeo.com/828886466/7a65b850c5'),
    ('Capitulo 11',   'https://vimeo.com/828887594/775adbf33d'),
    ('Capitulo 12',   'https://vimeo.com/828888224/ecb31a3af2'),
    ('Capitulo 13',   'https://vimeo.com/828889019/a66ded4671'),
    ('Capitulo 14',   'https://vimeo.com/828889334/c25b1ceb62'),
    ('Capitulo 15',   'https://vimeo.com/828890481/9e86341402'),
    ('Capitulo 16',   'https://vimeo.com/828891176/f30d0c698a'),
    ('Capitulo 17',   'https://vimeo.com/828892828/679f839587'),
    ('Capitulo 18',   'https://vimeo.com/828893963/c78e0e28c9'),
    ('Capitulo 19',   'https://vimeo.com/828894481/3665f76746'),
    ('Capitulo 20',   'https://vimeo.com/828895240/fddafac1e2'),
    ('Capitulo 21',   'https://vimeo.com/828895753/5d4c219ad6'),
    ('Capitulo 22',   'https://vimeo.com/828896136/5469431a93'),
    ('Capitulo 23',   'https://vimeo.com/828896587/6d2b184113'),
]

_CAPCUT_LESSONS = [
    ('1.1 Introduccion',                              'https://vimeo.com/1031721899', '1. Introduccion al curso y primeros pasos'),
    ('1.2 Como instalar Capcut para PC',              'https://vimeo.com/1031721943', '1. Introduccion al curso y primeros pasos'),
    ('1.3 Cambio de idioma',                          'https://vimeo.com/1031721869', '1. Introduccion al curso y primeros pasos'),
    ('2.1 Conociendo la interfaz de Capcut',          'https://vimeo.com/1031721976', '2. Conociendo la interfaz de Capcut'),
    ('3. Atajos y configuracion del teclado',         'https://vimeo.com/1031722032', '3. Atajos y configuracion del teclado'),
    ('4.1 Primeros pasos en la Creacion de un Proyecto', 'https://vimeo.com/1031722268', '4. Creacion de un Proyecto y Gestion de Archivos'),
    ('4.2 Ajuste del Formato y Dimensiones del Video','https://vimeo.com/1031722198', '4. Creacion de un Proyecto y Gestion de Archivos'),
    ('5.1 Como cortar videos',                        'https://vimeo.com/1031723250', '5. Recursos para crear videos virales'),
    ('5.2 Transiciones y efectos de sonido',          'https://vimeo.com/1031723322', '5. Recursos para crear videos virales'),
    ('5.3 Capas y Superposicion de Elementos',        'https://vimeo.com/1031723378', '5. Recursos para crear videos virales'),
    ('5.4 Textos y Subtitulos en Video',              'https://vimeo.com/1031723452', '5. Recursos para crear videos virales'),
    ('5.5 Audio y efectos de voz',                    'https://vimeo.com/1031723532', '5. Recursos para crear videos virales'),
    ('5.6 Efectos y animacion',                       'https://vimeo.com/1031723592', '5. Recursos para crear videos virales'),
    ('5.7 Musica',                                    'https://vimeo.com/1031723678', '5. Recursos para crear videos virales'),
    ('5.8 Elementos graficos para destacar puntos',   'https://vimeo.com/1031722936', '5. Recursos para crear videos virales'),
    ('5.9 Zoom y Keyframes',                          'https://vimeo.com/1031722983', '5. Recursos para crear videos virales'),
    ('5.10 Filtros',                                  'https://vimeo.com/1031723083', '5. Recursos para crear videos virales'),
    ('5.11 Exportacion del Video',                    'https://vimeo.com/1031723161', '5. Recursos para crear videos virales'),
    ('6.1 Conclusion',                                'https://vimeo.com/1031723091', '6. Conclusion'),
]

_WEB_LESSONS = [
    ('0. Bienvenidos',                              'https://vimeo.com/953217254'),
    ('1. Eligiendo y contratando nuestro hosting',  'https://vimeo.com/953194088'),
    ('2. Configuración e instalación de Wordpress', 'https://vimeo.com/953194175'),
    ('3. Panel de control de Wordpress',            'https://vimeo.com/953194195'),
    ('4. Iniciando sesión y editor nativo de Wordpress', 'https://vimeo.com/953194296'),
    ('5. Instalando elementor',                     'https://vimeo.com/953194327'),
    ('6. Jugando con Wordpress y Elementor',        'https://vimeo.com/953194382'),
    ('7. Editor de Elementor al completo',          'https://vimeo.com/953194406'),
    ('8. Vinculando nuestra cuenta de elementor',   'https://vimeo.com/953194499'),
    ('9. Descubriendo las plantillas de elementor', 'https://vimeo.com/953193184'),
    ('10. Mejores temas para Elementor',            'https://vimeo.com/953193668'),
    ('11. Instalando e importando nuestro primer tema', 'https://vimeo.com/953193585'),
    ('12. Opciones globales de configuración',      'https://vimeo.com/953193771'),
    ('13. Personalizando nuestro Header + LOGO',    'https://vimeo.com/953193299'),
    ('14. Elementos de nuestro sitio web: Títulos', 'https://vimeo.com/953193834'),
    ('15. Botones, enlaces y rutas',                'https://vimeo.com/953193412'),
    ('16. Columnas y secciones',                    'https://vimeo.com/953193456'),
    ('17. Sección de vídeo y enlaces',              'https://vimeo.com/953193909'),
    ('18. Carrousel de imágenes',                   'https://vimeo.com/953194006'),
    ('19. Cómo hacer y restaurar copias de seguridad', 'https://vimeo.com/953194053'),
    ('20. Plugins avanzados de seguridad',          'https://vimeo.com/953193531'),
]


def _build_programas_sections(course):
    """Helper: create/ensure '1. Capcut', '2. Premiere' and '3. Crea tu web' sections."""
    existing_titles = {s.title for s in course.sections}

    if '1. Capcut' not in existing_titles:
        sec = Section(course_id=course.id, title='1. Capcut', order=0)
        db.session.add(sec)
        db.session.flush()
        for l_order, (l_title, l_url, l_group) in enumerate(_CAPCUT_LESSONS):
            db.session.add(Lesson(section_id=sec.id, title=l_title,
                                  video_url=l_url, group_label=l_group, order=l_order))

    if '2. Premiere' not in existing_titles:
        sec2 = Section(course_id=course.id, title='2. Premiere', order=1)
        db.session.add(sec2)
        db.session.flush()
        for l_order, (l_title, l_url) in enumerate(_PREMIERE_LESSONS):
            db.session.add(Lesson(section_id=sec2.id, title=l_title,
                                  video_url=l_url, order=l_order))

    if '3. Crea tu web' not in existing_titles:
        sec3 = Section(course_id=course.id, title='3. Crea tu web', order=2)
        db.session.add(sec3)
        db.session.flush()
        for l_order, (l_title, l_url) in enumerate(_WEB_LESSONS):
            db.session.add(Lesson(section_id=sec3.id, title=l_title,
                                  video_url=l_url, order=l_order))


def seed_programas_marca():
    try:
        course = Course.query.filter_by(title='Programas para tu marca').first()

        if not course:
            course = Course(
                title='Programas para tu marca',
                subtitle='Herramientas y programas para potenciar tu marca personal',
                description='Formaciones sobre herramientas clave para crear y hacer crecer tu marca personal.',
                is_published=True,
                price=0.0,
            )
            db.session.add(course)
            db.session.flush()
            _build_programas_sections(course)
        else:
            sections = Section.query.filter_by(course_id=course.id).all()
            titles = {s.title for s in sections}

            # If old multi-section capcut structure exists, wipe and rebuild
            valid = (
                {'1. Capcut'},
                {'1. Capcut', '2. Premiere'},
                {'1. Capcut', '2. Premiere', '3. Crea tu web'},
            )
            if titles not in valid:
                for sec in sections:
                    for lesson in sec.lessons:
                        LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
                    db.session.delete(sec)
                db.session.flush()

            _build_programas_sections(course)

        db.session.commit()
        print('[seed_programas_marca] "Programas para tu marca" actualizado: Capcut + Premiere + Crea tu web.')
    except Exception as e:
        print(f'[seed_programas_marca] ERROR: {e}')
        db.session.rollback()


def seed_clases_2026():
    try:
        if Course.query.filter_by(title='Clases pasadas grabadas 2026').first():
            return
        course = Course(
            title='Clases pasadas grabadas 2026',
            subtitle='Todas las clases en directo de 2026',
            description='Accede a todas las grabaciones de las clases en directo realizadas durante 2026.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('Mayo 2026', 0, [
                ('6-5-2026 Constancia',  'https://vimeo.com/1189876173'),
                ('6-5-2026 Ventas',      'https://vimeo.com/1189510087'),
                ('3-5-2026',             'https://vimeo.com/1188857174'),
            ]),
            ('Abril 2026', 1, [
                ('29-4-2026 Creatividad',                          ''),
                ('28-4-2026 Mentalidad',                           'https://vimeo.com/1187597054'),
                ('26-4-2026 Retencion de Atencion por psicologia', 'https://vimeo.com/1186745469'),
                ('20-4-2026 Youtube y herramientas IA',            'https://vimeo.com/1184715176'),
                ('19-4-2026 - Juego de paja o mina de oro',        'https://vimeo.com/1184537495'),
                ('4-4-2026 - Meta Ads - Mentalidad - Coherencia',  'https://vimeo.com/1180151061'),
                ('2-4-2026 Dejar ir',                              'https://vimeo.com/1179488825'),
            ]),
            ('Marzo 2026', 2, [
                ('26-3-2026 Cuenta tu historia',          'https://vimeo.com/1177467500'),
                ('24-3-2026 Autoridad',                   'https://vimeo.com/1176131131'),
                ('18-3-2026',                             'https://vimeo.com/1174683508'),
                ('15-3-2026',                             'https://vimeo.com/1173810561'),
                ('14-03-2026 El poder de la comunicacion','https://vimeo.com/1173634843'),
                ('11-03-2026',                            'https://vimeo.com/1172320523'),
                ('4-3-2026 Mentalidad y habitos',         'https://vimeo.com/1170587953'),
                ('1-3-2026 Como atraer a tu publico objetivo', 'https://vimeo.com/1169364567'),
            ]),
            ('Febrero 2026', 3, [
                ('25-2-2026 Mentalidad y dinero',              'https://vimeo.com/1168247763'),
                ('25-2-2026 Analisis de Marcas Personales',    'https://vimeo.com/1168019884'),
                ('22-02-2026',                                 'https://vimeo.com/1167179538'),
                ('19-2-2026 Mentalidad',                       'https://vimeo.com/1166276374'),
                ('4-2-2026 Romper creencias Marca Personal',   'https://vimeo.com/1161575886'),
            ]),
            ('Enero 2026', 4, [
                ('28-1-2026', 'https://vimeo.com/1159154315'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_clases_2026] Curso "Clases pasadas grabadas 2026" creado con 5 secciones.')
    except Exception as e:
        print(f'[seed_clases_2026] ERROR: {e}')
        db.session.rollback()


def seed_ia():
    try:
        if Course.query.filter_by(title='IA').first():
            return
        course = Course(
            title='IA',
            subtitle='Crea contenido con Inteligencia Artificial y crece en redes sociales',
            description='Aprende a crear contenido faceless con IA: guiones, voz, edición y miniaturas para monetizar tu canal.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('FASE 1 CREAMOS TU CANAL', 0, [
                ('Bienvenida.',                                  'https://vimeo.com/905900462'),
                ('La mentalidad necesaria para este negocio.',   'https://vimeo.com/905900938'),
                ('¿Qué son los canales automatizados?',          'https://vimeo.com/905903531'),
                ('Como consumir este curso.',                     'https://vimeo.com/905905036'),
                ('Ayúdame.',                                     'https://vimeo.com/905906678'),
                ('2.1 Encuentra tu nicho.',                      'https://vimeo.com/905912313'),
                ('2.1.1 Escoge un nicho en tendencias.',         'https://vimeo.com/1022413674'),
                ('2.2 Abrir tu canal de YouTube.',               'https://vimeo.com/905919839'),
                ('2.3 Personalización del Canal + VidIQ.',       'https://vimeo.com/1022429684'),
                ('2.1.2 Algunos nichos interesantes.',           'https://vimeo.com/1025159053'),
                ('3.0 Analizando a tu audiencia.',               'https://vimeo.com/997656483'),
                ('3.1 Crear un guion con Chat GPT.',             'https://vimeo.com/913400871'),
                ('3.2 Ideas para crear tu guion.',               'https://vimeo.com/914177000'),
                ('GPTs Para crear tus guiones mas realistas.',   'https://vimeo.com/1011825608'),
                ('4.3 Pasar de texto a voz (Eleven Labs)',       'https://vimeo.com/918913820'),
                ('5.1 Edición con CapCut.',                      'https://vimeo.com/921756345'),
                ('5.2 Edición con CapCut Y exportado.',          'https://vimeo.com/925753040'),
                ('6.0 Entrar en Discord para acceder a Midjourney', 'https://vimeo.com/1000681997'),
                ('6.1 Creación de miniaturas con Midjourney.',   'https://vimeo.com/1024298757'),
                ('6.2 Crear miniatura con Canva',                'https://vimeo.com/948289425'),
                ('6.3 Crear miniatura con Leonardo AI',          'https://vimeo.com/1023726321'),
                ('6.4 Crear miniatura con Photoshop.',           'https://vimeo.com/956991589'),
                ('6.5 Mejorando miniaturas',                     'https://vimeo.com/967114138'),
                ('7.1 Resumen de todo lo que hemos visto.',      'https://vimeo.com/1018457003'),
                ('¿Cómo descargar videos de artgrid?',           'https://vimeo.com/948304344'),
            ]),
            ('FASE 2 PROGRAMAS PARA TU MARCA', 1, [
                ('1.1 Programa Animaciones dibujo Mano (VideoScribe)', 'https://vimeo.com/935925338'),
                ('1.2 Crear un avatar con IA (D-ID)',                  'https://vimeo.com/941271568'),
                ('1.3 Crear un avatar de ti (HeyGen)',                 'https://vimeo.com/1013616444'),
                ('2.1 Animación de fotos (Pikalabs)',                  'https://vimeo.com/937626127'),
                ('2.2 De texto a vídeo FLIKI',                         'https://vimeo.com/1022661291'),
                ('3.1 Leonardo AI (programa para hacer miniaturas)',    'https://vimeo.com/1023726321'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_ia] Curso "IA" creado con 2 fases y 31 lecciones.')
    except Exception as e:
        print(f'[seed_ia] ERROR: {e}')
        db.session.rollback()


def seed_coach_profesional():
    try:
        if Course.query.filter_by(title='Hazte Coach profesional').first():
            return
        course = Course(
            title='Hazte Coach profesional',
            subtitle='',
            description='',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.commit()
        print('[seed_coach_profesional] Curso "Hazte Coach profesional" creado.')
    except Exception as e:
        print(f'[seed_coach_profesional] ERROR: {e}')
        db.session.rollback()


def seed_clases_2025():
    try:
        if Course.query.filter_by(title='Clases 2025').first():
            return
        course = Course(
            title='Clases 2025',
            subtitle='Todas las clases en directo grabadas 2024-2025',
            description='Accede a todas las grabaciones de las clases en directo realizadas desde abril 2024 hasta enero 2026.',
            is_published=True,
            price=0.0,
        )
        db.session.add(course)
        db.session.flush()

        sections_data = [
            ('Enero 2026', 0, [
                ('15-1-2026 Como hacer ofertas',                      'https://vimeo.com/1154799599'),
                ('🔴 Final formación niño interior',                   'https://vimeo.com/1154444356'),
                ('11-1-2026 ¿Qué publicar en RRSS?',                  'https://vimeo.com/1153399221'),
                ('17-01-2026 - Como hacer sus primeros 1.000 mil EUR', 'https://vimeo.com/1155578698'),
                ('🔴 8-1-2025 El sistema del personaje.',              'https://vimeo.com/1152337303'),
                ('ENERO: METODO 30X FOCUS - BRYAN TRACY',              'https://vimeo.com/1151250794'),
                ('4-1-2026 Estrategia de contenido',                   'https://vimeo.com/1151511851'),
            ]),
            ('Diciembre 2025', 1, [
                ('28-12-2025 Final de año',                   'https://vimeo.com/1149927847'),
                ('🔴18-12-2025',                              'https://vimeo.com/1147459657'),
                ('16-12-2025',                                'https://vimeo.com/1147107355'),
                ('🔴10-12-2025 Recogida de proyecciones',     'https://vimeo.com/1145401240'),
                ('9-12-2025',                                 'https://vimeo.com/1145021487'),
                ('🔴 3-12-2025 Encuentro con el niño',        'https://vimeo.com/1143207136'),
            ]),
            ('Noviembre 2025', 2, [
                ('🔴 26-11-2025 Niño interior 4',                            'https://vimeo.com/1140914534'),
                ('29-11-2025 Espionaje de competencia + retención',          'https://vimeo.com/1141695080'),
                ('25-11-2015 Validar tu producto',                           'https://vimeo.com/1140554628'),
                ('🔴 19-11-2025 niño interior 3',                            'https://vimeo.com/1138661240'),
                ('19-11-2025 Estrategia general en redes para vender',       'https://vimeo.com/1138410011'),
                ('23-11-2025 Revisión de perfiles + ideas',                  'https://vimeo.com/1139893456'),
                ('16-11-2025 Estrategia entre redes',                        'https://vimeo.com/1137475177'),
                ('15-11-2025 Practica de rolplay + ventas',                  'https://vimeo.com/1137412964'),
                ('🔴 14-11-2025 Niño interior 2',                            'https://vimeo.com/1136253801'),
                ('11-11-2025 Retención en YouTube',                          'https://vimeo.com/1135873618'),
                ('9-11-2025 Historias de Instagram.',                        'https://vimeo.com/1135117297'),
                ('2-11-2025 Conversaciones en instagram',                    'https://vimeo.com/1132961723'),
                ('1-11-2025',                                                'https://vimeo.com/1132816708'),
            ]),
            ('Octubre 2025', 3, [
                ('21-10-2025',                                         'https://vimeo.com/1129305180'),
                ('5-10-2024 hook visuales.',                           'https://vimeo.com/1124648577'),
                ('29-10-2025 Oratoria consciente para redes sociales', 'https://vimeo.com/1131618392'),
            ]),
            ('Septiembre 2025', 4, [
                ('30-9-2025',                                      'https://vimeo.com/1123326774'),
                ('21-9-2025',                                      'https://vimeo.com/1120633252'),
                ('16-9-2025',                                      'https://vimeo.com/1119227745'),
                ('15-9-2025 Pasar de seguidores a clientes',       'https://vimeo.com/1118647697'),
                ('9-9-2025 Estrategia historias de Instagram.',    'https://vimeo.com/1117220874'),
                ('7-9-2025 Storytelling',                          'https://vimeo.com/1116592377'),
                ('2-9-2025 Trucos instagram',                      'https://vimeo.com/1115420774'),
            ]),
            ('Agosto 2025', 5, [
                ('26-8-2025 Análisis Marca Personales Alumnos', 'https://vimeo.com/1113470753'),
                ('24-8-2025',                                   'https://vimeo.com/1112720380'),
                ('17-8-2025',                                   'https://vimeo.com/1110751972'),
                ('5-8-2025',                                    'https://vimeo.com/1107522387'),
                ('3-8-2025 Generar comunidad en historias.',    'https://vimeo.com/1106916027'),
            ]),
            ('Julio 2025', 6, [
                ('30-7-2025',                                                   'https://vimeo.com/1105703917'),
                ('20-7-2025',                                                   'https://vimeo.com/1102956963'),
                ('8-7-2025 Estrategias contenido Youtube e instagram',          'https://vimeo.com/1099773083'),
                ('3-7-2025 Análisis de alumnos.',                               'https://vimeo.com/1098412325'),
                ('1-7-2025 Organización y calendarios de contenidos',           'https://vimeo.com/1097964796'),
            ]),
            ('Junio 2025', 7, [
                ('26-6-2025 Mentalidad',        'https://vimeo.com/1097344696'),
                ('24-6-2025',                   'https://vimeo.com/1096036077'),
                ('18-6-2025 Colaboraciones',    'https://vimeo.com/1094176751'),
                ('11-06-2025 Estrategia ganadora', 'https://vimeo.com/1092234172'),
                ('4-6-2025',                    'https://vimeo.com/1090614061'),
            ]),
            ('Mayo 2025', 8, [
                ('28-5-2025 Estrategia de contenido y producto', 'https://vimeo.com/1088540117'),
                ('20-5-2025 Análisis avatar con chat GPT',       'https://vimeo.com/1086185888'),
                ('18-5-2025 Trucos revisando contenido.',        'https://vimeo.com/1085594526'),
                ('14-5-2025 Trucos en el contenido',             'https://vimeo.com/1084883105'),
                ('13-5-2025 Trucos para instagram',              'https://vimeo.com/1084013765'),
                ('11-5-2025 Proposito en tu contenido.',         'https://vimeo.com/1083423939'),
                ('7-5-2025 Motivación Integrar la Sombra',       'https://vimeo.com/1082314951'),
                ('6-5-2025 Crear contenido viral',               'https://vimeo.com/1081955919'),
                ('4-5-2025 Elevar el nivel de consciencia',      'https://vimeo.com/1081313993'),
            ]),
            ('Abril 2025', 9, [
                ('30-4-2025 Contenido',                                    'https://vimeo.com/1080328343'),
                ('29-4-2025 Titulos',                                      'https://vimeo.com/1080099680'),
                ('23-4-2025 ¿Cómo hacer para que paren en el feed?',      'https://vimeo.com/1077729888'),
                ('16-4-2025 VSL en profundidad',                           'https://vimeo.com/1076164098'),
                ('15-4-2025 Motivación',                                   'https://vimeo.com/1075947951'),
                ('14-4-2025 VSL estrategia completa',                      'https://vimeo.com/1075124049'),
                ('9-4-2025 Historias de instagram',                        'https://vimeo.com/1074067588'),
                ('8-4-2025 Vender por Whastapp',                           'https://vimeo.com/1073686689'),
                ('6-4-2025',                                               'https://vimeo.com/1073010901'),
                ('1-4-2025 Automatización con IA TONET',                   'https://vimeo.com/1071552454'),
            ]),
            ('Marzo 2025', 10, [
                ('30-3-2025 IA como asistente',                         'https://vimeo.com/1070842309'),
                ('26-3-2025 Crecimiento Masivo en redes clase 5',       'https://vimeo.com/1069735468'),
                ('25-3-2025',                                           'https://vimeo.com/1069528783'),
                ('23-3-2025',                                           'https://vimeo.com/1068660985'),
                ('19-3-2025',                                           'https://vimeo.com/1067492688'),
                ('16-3-2025',                                           'https://vimeo.com/1066381229'),
                ('15-3-2025',                                           'https://vimeo.com/1066211842'),
                ('12-3-2025 Constancia',                                'https://vimeo.com/1065246322'),
                ('11-3-2025 Finanzas personales e inversión',           'https://vimeo.com/1064850100'),
                ('8-3-2025 Google ADS poner anuncio en google.',        'https://vimeo.com/1063919086'),
                ('5-3-2025 Encontrar a tu cliente',                     'https://vimeo.com/1062939381'),
                ('4-3-2025',                                            'https://vimeo.com/1062539139'),
            ]),
            ('Febrero 2025', 11, [
                ('26-2-2025 Avatar 3.0',                            'https://vimeo.com/1060624030'),
                ('25-2-2025',                                       'https://vimeo.com/1060245758'),
                ('19-2-2025 Buyer persona',                         'https://vimeo.com/1058485470'),
                ('17-2-2025 GPT\'s interesantes',                   'https://vimeo.com/1057629154'),
                ('15-2-2025 Edicion con capcut (Jenny)',             'https://vimeo.com/1057281769'),
                ('12-2-2025 Crecimiento masivo en RRSS 1',          'https://vimeo.com/1056140137'),
                ('11-2-2025 Eliminar resistencias',                  'https://vimeo.com/1055722980'),
                ('10-2-2025 Analisis estrategia Marca Personal',     'https://vimeo.com/1055024291'),
                ('09-02-2025 Preguntas y Respuestas',                'https://vimeo.com/1054860750'),
                ('5-2-2025 Copy 8',                                  'https://vimeo.com/1053911839'),
                ('3-2-2025 Estrategia en Youtube',                   'https://vimeo.com/1052928025'),
                ('02-02-2025 Crea tu Oceano Azul',                   'https://vimeo.com/1053342320'),
            ]),
            ('Enero 2025', 12, [
                ('29-1-2025 Copywriting 7',                         'https://vimeo.com/1051679678'),
                ('28-1-2025 Proposito para tu proyecto',            'https://vimeo.com/1051296007'),
                ('27-1-2025',                                       'https://vimeo.com/1050905159'),
                ('25-01-2025 Crecimiento Acelerado con Publicidad', 'https://vimeo.com/1050756441'),
                ('22-1-2025',                                       'https://vimeo.com/1049436953'),
                ('21-1-2025',                                       'https://vimeo.com/1049068089'),
                ('18-01-2025 Servicios y Productos',                'https://vimeo.com/1048587484'),
                ('15-1-2025 Avatar 3.0',                            'https://vimeo.com/1047490228'),
                ('14-1-2025 Avatar 2.0',                            'https://vimeo.com/1047037016'),
                ('12-1-2025 Avatar 0.1',                            'https://vimeo.com/1046217798'),
                ('9-1-2025 Copywriting 6',                          'https://vimeo.com/1045132445'),
                ('07-01-2025 Elevar el nivel de conciencia',        'https://vimeo.com/1044795402'),
            ]),
            ('Diciembre 2024', 13, [
                ('18-12-2024 Copy 5',                          'https://vimeo.com/1040530020'),
                ('15-12-2024 Cosas que te hacen viral',        'https://vimeo.com/1039460534'),
                ('08-12-2024 Crear GPTS',                      'https://vimeo.com/1037256479'),
                ('07-12-2024 Productividad + PyR',             'https://vimeo.com/1037200624'),
                ('03-12-2024 Preguntas y respuestas',          'https://vimeo.com/1035748510'),
                ('30-12-2024 Atraer a tu publico objetivo',    'https://vimeo.com/1043000993'),
            ]),
            ('Noviembre 2024', 14, [
                ('30-11-2024 Videos RolPlay',                   'https://vimeo.com/1034830397'),
                ('27-11-2024 Copywriter 2',                     'https://vimeo.com/1033995541'),
                ('26-11-2024',                                  'https://vimeo.com/1033608600'),
                ('20-11-2024 copy 1',                           'https://vimeo.com/1031685641'),
                ('17-11-2024 Ia de videos',                     'https://vimeo.com/1030542186'),
                ('16-11-2024 ChatGPT y PyR',                    'https://vimeo.com/1030377991'),
                ('15-11-2024 setter 4',                         'https://vimeo.com/1029400098'),
                ('12-11-2024 Análisis mercado para producto',   'https://vimeo.com/1028991502'),
                ('10-11-2024 Analizamos Canales de YT',         'https://vimeo.com/1028205920'),
                ('09-11-2024 Estrategia Mensajes y PyR',        'https://vimeo.com/1028041386'),
                ('07-11-2024 Análisis Marcas Personales',       'https://vimeo.com/1027438043'),
                ('06-11-2024 Monetización YouTube',             'https://vimeo.com/1027049700'),
                ('03-11-2024 Amazon afliliados',                'https://vimeo.com/1025931419'),
                ('02-11-2024 PyR',                              'https://vimeo.com/1028011101'),
            ]),
            ('Octubre 2024', 15, [
                ('30-10-2024 Setter 4',                                     'https://vimeo.com/1024893241'),
                ('29-10-2024 Recursos gratis',                              'https://vimeo.com/1024510579'),
                ('26-10-2024 Estudio de Mercado',                           'https://vimeo.com/1023599192'),
                ('21-10-2024 Analizando nichos para YT',                    'https://vimeo.com/1021497994'),
                ('19-10-2024 VSL y PyR',                                    'https://vimeo.com/1021338754'),
                ('16-10-2024 Setter 3 Análisis de canales y voz',           'https://vimeo.com/1020332240'),
                ('15-10-2024 Como crear una landing page',                  'https://vimeo.com/1019932102'),
                ('9-10-2024 Sistema setter || revision canal',              'https://vimeo.com/1018054139'),
                ('8-10-2024 Aumentar retención y canales de YT',            'https://vimeo.com/1017670354'),
                ('05-10-2024 Ofertas irresistibles y monetización',         'https://vimeo.com/1016561843'),
                ('02-10-2024 Setter figura.',                               'https://vimeo.com/1015401719'),
                ('01-10-2024 P&R',                                          'https://vimeo.com/1015141880'),
            ]),
            ('Septiembre 2024', 16, [
                ('29-9-2024 Inteligencia artificial para contenidos.', 'https://vimeo.com/1014101056'),
                ('28-09-2024 Base de Marketing pre-escalar',           'https://vimeo.com/1014115958'),
                ('25-9-2024 Historias destacadas instagram',           'https://vimeo.com/1012910840'),
                ('25-9-2024 Repasamos canales.',                       'https://vimeo.com/1012674859'),
                ('20-9-2024 Tendencias RRSS 2025',                    'https://vimeo.com/1011097003'),
                ('12-9-2024 Empezar a vender en RRSS',                'https://vimeo.com/1008565130'),
                ('11-9-2024 Reels virales con transición',             'https://vimeo.com/1008183717'),
                ('5-9-2024 Retener la atención.',                      'https://vimeo.com/1006352665'),
                ('3-9-2024 Tendencias en redes sociales.',             'https://vimeo.com/1005962046'),
            ]),
            ('Agosto 2024', 17, [
                ('21-8-2024 Entrenar el Carisma',                  'https://vimeo.com/1001342356'),
                ('20-8-2024 Análisis de Marca Personales.',         'https://vimeo.com/1000895736'),
                ('14-08-2024 Creando atmosfera para vender.',       'https://vimeo.com/998841537'),
                ('13-8-2024 Motivacion y estadisticas par aYT',    'https://vimeo.com/998372315'),
                ('7-8-2024 Edición en capcut',                     'https://vimeo.com/995939545'),
                ('6-8-2024 Revisión de canales',                   'https://vimeo.com/995524046'),
            ]),
            ('Julio 2024', 18, [
                ('30-7-2024 Crecimiento en YT + Ventas.',                    'https://vimeo.com/992315467'),
                ('24-7-2024 Vencer las excusas para crear contenido.',       'https://vimeo.com/989754430'),
                ('23-7-2024 Superar el miedo a crear contenido.',            'https://vimeo.com/989094804'),
                ('17-7-2024 Resolvemos dudas para e crecimiento RRSS',       'https://vimeo.com/986998405'),
                ('16-7-2024 MOTIVACION',                                     'https://vimeo.com/985582401'),
                ('10-7-2024 Retención de la audiencia.',                     'https://vimeo.com/982167628'),
                ('9-7-2024 P&R',                                             'https://vimeo.com/981553993'),
                ('3-7-2024 Posicionamiento SEO YT, Instagram, tiktok',      'https://vimeo.com/975647679'),
                ('2-7-2024 Cambio de estrategia P&R',                        'https://vimeo.com/974426591'),
            ]),
            ('Junio 2024', 19, [
                ('27-6-2024 Vender sin vender.',                            'https://vimeo.com/970090412'),
                ('25-6-2024 Preguntas y Respuestas.',                       'https://vimeo.com/968222164'),
                ('19-6-2024 Facebook ADS (Basico)',                         'https://vimeo.com/962585274'),
                ('18-6-2024 Crecer rápido en Instagram.',                   'https://vimeo.com/961542519'),
                ('12-6-2024 Estrategia de contenido.',                      'https://vimeo.com/970023795'),
                ('11-6-2024 Enlazar FB e Insta, como crear comunidad',      'https://vimeo.com/956706697'),
                ('05-06-2024 Estrategia contenidos + inversiones.',         'https://vimeo.com/958460844'),
                ('04-06-2024 Ads, motivación y ganar dinero.',              'https://vimeo.com/953680797'),
            ]),
            ('Abril 2024', 20, [
                ('29-05-2024 Perder el miedo a la cámara.',      'https://vimeo.com/951669832'),
                ('28-05-2024 Análisis canales de YouTube.',      'https://vimeo.com/951248358'),
                ('22-05-2024 Estrategia de contenidos.',         'https://vimeo.com/949292736'),
                ('21-05-2024 Preguntas y respuestas.',           'https://vimeo.com/948865310'),
                ('15-05-2024 Escribir un guion.',                'https://vimeo.com/948059231'),
                ('14-05-2024 Preguntas y respuestas',            'https://vimeo.com/946584164'),
            ]),
        ]

        for sec_title, sec_order, lessons in sections_data:
            sec = Section(course_id=course.id, title=sec_title, order=sec_order)
            db.session.add(sec)
            db.session.flush()
            for l_order, (l_title, l_url) in enumerate(lessons):
                db.session.add(Lesson(
                    section_id=sec.id,
                    title=l_title,
                    video_url=l_url,
                    order=l_order,
                ))

        db.session.commit()
        print('[seed_clases_2025] Curso "Clases 2025" creado con 21 secciones.')
    except Exception as e:
        print(f'[seed_clases_2025] ERROR: {e}')
        db.session.rollback()


@app.route('/admin/fix-programas-marca')
@login_required
@admin_required
def admin_fix_programas_marca():
    """Reorganiza Programas para tu marca: todas las lecciones en 1 carpeta Capcut con subcarpetas."""
    try:
        course = Course.query.filter_by(title='Programas para tu marca').first()
        if not course:
            flash('No se encontro el curso "Programas para tu marca".', 'error')
            return redirect(url_for('courses'))

        # Delete existing sections (and their lessons/progress)
        for sec in list(course.sections):
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
            db.session.delete(sec)
        db.session.flush()

        # Create single section with sub-folders via group_label
        sec = Section(course_id=course.id, title='1. Capcut', order=0)
        db.session.add(sec)
        db.session.flush()

        for l_order, (l_title, l_url, l_group) in enumerate(_CAPCUT_LESSONS):
            db.session.add(Lesson(
                section_id=sec.id,
                title=l_title,
                video_url=l_url,
                group_label=l_group,
                order=l_order,
            ))

        db.session.commit()
        flash('Programas para tu marca reorganizado: 1 carpeta Capcut con subcarpetas.', 'success')
        return redirect(url_for('admin_edit_course', course_id=course.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
        return redirect(url_for('courses'))


@app.route('/admin/fix-liberacion-emocional')
@login_required
@admin_required
def admin_fix_liberacion_emocional():
    """Force-insert '4. Liberacion emocional' into FASE 5, idempotent."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            flash('No se encontro el curso FASE 5 MENTALIDAD', 'error')
            return redirect(url_for('admin_dashboard'))

        # Remove any previous attempt
        existing = Section.query.filter_by(course_id=course.id,
                                           title='4. Liberacion emocional').first()
        if existing:
            for lesson in existing.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
            db.session.delete(existing)
            db.session.flush()

        # Shift sections with order >= 13 up by 1
        for sec in Section.query.filter_by(course_id=course.id).filter(
                Section.order >= 13).all():
            sec.order += 1
        db.session.flush()

        new_sec = Section(course_id=course.id,
                          title='4. Liberacion emocional', order=13)
        db.session.add(new_sec)
        db.session.flush()

        _lessons = [
            ('Bienvenidos',   'https://vimeo.com/719536396'),
            ('Capitulo 1',    'https://vimeo.com/719536479'),
            ('Capitulo 2',    'https://vimeo.com/719536500'),
            ('Capitulo 3',    'https://vimeo.com/719536514'),
            ('Capitulo 4',    'https://vimeo.com/719536558'),
            ('Capitulo 5',    'https://vimeo.com/721359695'),
            ('Capitulo 6',    'https://vimeo.com/719536688'),
            ('Capitulo 7',    'https://vimeo.com/719536704'),
            ('Capitulo 8',    'https://vimeo.com/719536728'),
            ('Capitulo 9',    'https://vimeo.com/720549604'),
            ('Capitulo 10',   'https://vimeo.com/720555536'),
            ('Capitulo 11',   'https://vimeo.com/720564299'),
            ('Capitulo 12',   'https://vimeo.com/720564393'),
            ('Capitulo 13',   'https://vimeo.com/720564485'),
            ('Capitulo 14',   'https://vimeo.com/721350229'),
            ('Capitulo 15',   'https://vimeo.com/721350331'),
            ('Capitulo 16',   'https://vimeo.com/721350382'),
            ('Capitulo 17',   'https://vimeo.com/803924439'),
        ]
        for l_order, (l_title, l_url) in enumerate(_lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        flash('Seccion "4. Liberacion emocional" creada con 18 lecciones.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/fix-fase5-habitos')
@login_required
@admin_required
def admin_fix_fase5_habitos():
    """One-shot route: collapse all bono sub-sections in FASE 5 into a
    single '1. Habitos para la paz mental' section with 26 lessons."""
    try:
        course = Course.query.filter_by(title='FASE 5 MENTALIDAD').first()
        if not course:
            flash('No se encontro el curso FASE 5 MENTALIDAD', 'error')
            return redirect(url_for('admin_dashboard'))

        # Identify sections to remove (orders 0-9 or any Habitos variant)
        secs_to_remove = [
            sec for sec in Section.query.filter_by(course_id=course.id).all()
            if sec.order <= 9 or 'Habitos' in sec.title or 'abitos' in sec.title
        ]

        # Delete LessonProgress for every lesson in those sections first
        for sec in secs_to_remove:
            for lesson in sec.lessons:
                LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
        db.session.flush()

        # Now safe to delete the sections (cascade removes lessons/files)
        for sec in secs_to_remove:
            db.session.delete(sec)
        db.session.flush()

        # Reorder remaining sections compactly starting at 2
        remaining = Section.query.filter_by(course_id=course.id).order_by(Section.order).all()
        for i, sec in enumerate(remaining):
            sec.order = i + 2
        db.session.flush()

        # Create the single flat section at order 1
        new_sec = Section(course_id=course.id, title='1. Habitos para la paz mental', order=1)
        db.session.add(new_sec)
        db.session.flush()

        lessons = [
            ('1.1 Introduccion',                      'https://vimeo.com/749878520'),
            ('1.2 Como realizar este curso',           'https://vimeo.com/749881629/e2cbd4caf7'),
            ('1.3 Porque cuesta tanto cambiar',        'https://vimeo.com/749884233/1e320d927f'),
            ('2.1 El presente',                        'https://vimeo.com/749887187/ffba41cccb'),
            ('2.1.1 Profundizando en la meditacion',   'https://vimeo.com/749890461/a00d1504e0'),
            ('2.2 Pensar menos, sentir mas',           'https://vimeo.com/749888068/213b9224b8'),
            ('2.3 Decido vivir este momento',          'https://vimeo.com/749888144/f7e415bb2e'),
            ('2.3.1 Sanar el pasado',                  'https://vimeo.com/749892494/b00e80badc'),
            ('3.1 La Aceptacion',                      'https://vimeo.com/749893948/5b13abd2ba'),
            ('4.1 Como se forma el ego',               'https://vimeo.com/749894742/1fdf42c662'),
            ('4.1.2 Para que',                         'https://vimeo.com/749894828/5cdc074054'),
            ('4.1.1 Creencias',                        'https://vimeo.com/749894807/57e7fcf8e1'),
            ('4.2 Nino Interior',                      'https://vimeo.com/749897628/38e3e3a08d'),
            ('5.1 La ilusion de uno mismo',            'https://vimeo.com/749899407/9cef2eec80'),
            ('5.2 Recogida de proyecciones',           'https://vimeo.com/749901468/84733c5bfc'),
            ('5.1.1 Reprogramar la mente',             'https://vimeo.com/749899500/3357242a3d'),
            ('6.1 Reprogramar la mente',               'https://vimeo.com/749899500/3357242a3d'),
            ('7.1 Mindfull eating',                    'https://vimeo.com/749904175/162461a778'),
            ('7.2.1 Alimentacion consciente',          'https://vimeo.com/749906274/43a19e519b'),
            ('7.2.2 Alimentacion consciente 2',        'https://vimeo.com/749906363/e00d5f300d'),
            ('8.1 Iniciacion a la respiracion',        'https://vimeo.com/749908687/b0c7e3572b'),
            ('8.2 Respiracion consciente',             'https://vimeo.com/749909287/19c2af632c'),
            ('9.1 Energia sexual',                     'https://vimeo.com/749910594/f5716a6412'),
            ('9.2 Sexualidad consciente',              'https://vimeo.com/749910707/f8b9f064cf'),
            ('10. Super habitos',                      'https://vimeo.com/749912323/da572845b1'),
            ('11. Cierre de curso + regalo',           'https://vimeo.com/749914145/8f0ad0592b'),
        ]
        for l_order, (l_title, l_url) in enumerate(lessons):
            db.session.add(Lesson(
                section_id=new_sec.id,
                title=l_title,
                video_url=l_url,
                order=l_order,
            ))

        db.session.commit()
        flash('FASE 5 corregida: 26 lecciones en una sola carpeta.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/update-descriptions', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_force_descriptions():
    """Force-update lesson descriptions via direct SQL. GET shows status, POST applies updates."""
    lines = []
    try:
        with db.engine.connect() as conn:
            for (course_title, lesson_title), html in LESSON_DESCRIPTIONS.items():
                # Find lesson id via raw SQL join
                row = conn.execute(text(
                    """SELECT l.id, l.description FROM lesson l
                       JOIN section s ON s.id = l.section_id
                       JOIN course c ON c.id = s.course_id
                       WHERE c.title = :ct AND l.title = :lt
                       LIMIT 1"""
                ), {'ct': course_title, 'lt': lesson_title}).fetchone()

                if row is None:
                    lines.append(f'❌ NO encontrada: "{lesson_title}" en "{course_title}"')
                    continue

                lesson_id = row[0]
                current_desc = row[1] or ''
                already_rich = len(current_desc) > 500
                lines.append(f'✅ id={lesson_id} — "{lesson_title[:50]}" — desc_len={len(current_desc)} — rica={already_rich}')

                if request.method == 'POST':
                    conn.execute(text(
                        'UPDATE lesson SET description = :html WHERE id = :lid'
                    ), {'html': html, 'lid': lesson_id})
                    lines.append(f'   → 💾 Descripción actualizada ({len(html)} chars)')

            if request.method == 'POST':
                conn.commit()
                lines.append('\n✔ COMMIT realizado correctamente.')
    except Exception as e:
        lines.append(f'\n💥 ERROR: {e}')

    output = '\n'.join(lines)
    action_btn = ''
    if request.method == 'GET':
        action_btn = f'<form method="POST"><button type="submit" style="margin-top:16px;padding:10px 20px;background:#7c3aed;color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px">🚀 Aplicar actualización ahora</button></form>'

    return f'''<!DOCTYPE html><html><body style="font-family:monospace;padding:30px;background:#0f0f0f;color:#d4d4d4">
<h2 style="color:#a78bfa">🔧 Admin — Actualizar descripciones</h2>
<pre style="background:#1a1a1a;padding:20px;border-radius:8px;white-space:pre-wrap">{output}</pre>
{action_btn}
<br><a href="{url_for('admin_dashboard')}" style="color:#7c3aed">← Volver al admin</a>
</body></html>'''


# ── Ruta diagnóstico de base de datos (solo admin) ────────────────────────────
@app.route('/admin/db-status')
@login_required
def admin_db_status():
    if current_user.role != 'admin':
        abort(403)
    db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'postgresql' in db_url or 'postgres' in db_url:
        db_type = '✅ PostgreSQL (datos permanentes)'
    else:
        db_type = '⚠️ SQLite (datos SE PIERDEN en cada despliegue)'
    try:
        user_count    = User.query.count()
        comment_count = Comment.query.count()
        course_count  = Course.query.count()
        users_with_avatar = User.query.filter(User.avatar_data != None).count()
    except Exception as e:
        return f'<pre>Error BD: {e}</pre>'
    return f'''<pre style="font-family:monospace;padding:20px">
Base de datos: {db_type}
URL tipo: {"postgresql" if "postgresql" in db_url else "sqlite"}

Usuarios:        {user_count}
Con foto perfil: {users_with_avatar}
Comentarios:     {comment_count}
Cursos:          {course_count}
</pre><a href="{url_for("admin_dashboard")}">← Volver al panel</a>'''

# Inicializar BD siempre (tanto con gunicorn como directo)
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f'[DB] ERROR en create_all: {e}')
    try:
        with db.engine.connect() as conn:
            # lesson_file binary migration
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS data BYTEA"))
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS mimetype VARCHAR(100) DEFAULT 'application/octet-stream'"))
            conn.execute(text("ALTER TABLE lesson_file ADD COLUMN IF NOT EXISTS size INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE lesson_file DROP COLUMN IF EXISTS url"))
            # user: last_seen + avatar
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS avatar_data BYTEA"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS avatar_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'"))
            # site_settings: binary banner
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_data BYTEA"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            # point_event table (created by db.create_all, but add index hint)
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_user ON point_event(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_date ON point_event(created_at)"))
            conn.execute(text("ALTER TABLE live_class ADD COLUMN IF NOT EXISTS recurrence VARCHAR(10) DEFAULT 'none'"))
            conn.execute(text("ALTER TABLE live_class ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES live_class(id)"))
            # course cover image (binary)
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS cover_data BYTEA"))
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS cover_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            conn.execute(text("ALTER TABLE course ADD COLUMN IF NOT EXISTS \"order\" INTEGER DEFAULT 0"))
            # lesson inline images for rich-text descriptions
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS lesson_image (
                    id SERIAL PRIMARY KEY,
                    lesson_id INTEGER NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
                    mimetype VARCHAR(100) DEFAULT 'image/jpeg',
                    data BYTEA NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS notification (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES \"user\"(id),
                    type VARCHAR(30),
                    message VARCHAR(300),
                    link VARCHAR(200) DEFAULT '',
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
    except Exception:
        pass
    # ── Diagnóstico de base de datos ──────────────────────────────────────────
    _db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'postgresql' in _db_uri:
        print('=' * 60)
        print('[DB] ✅ POSTGRESQL — datos PERSISTENTES')
        print('=' * 60)
    else:
        print('=' * 60)
        print('[DB] ⚠️  SQLITE — datos se pierden en cada despliegue.')
        print('[DB]    Asegúrate de tener DATABASE_URL en Railway → Variables.')
        print('=' * 60)

    try:
        seed_db()
    except Exception as e:
        print(f'[seed] ERROR en seed_db: {e}')
        db.session.rollback()

    try:
        seed_descriptions()
    except Exception as e:
        print(f'[seed_desc] ERROR en seed_descriptions: {e}')
        db.session.rollback()

    # DB column migration: add group_label to lesson if missing
    try:
        with db.engine.connect() as _conn:
            _conn.execute(text(
                "ALTER TABLE lesson ADD COLUMN IF NOT EXISTS group_label VARCHAR(200)"
            ))
            _conn.commit()
    except Exception as _e:
        print(f'[migration] group_label: {_e}')

    # DB migration: comment_likes table
    try:
        with db.engine.connect() as _conn:
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS comment_likes (
                    user_id    INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    comment_id INTEGER NOT NULL REFERENCES comment(id) ON DELETE CASCADE,
                    PRIMARY KEY (user_id, comment_id)
                )
            """))
            _conn.commit()
    except Exception as _e:
        print(f'[migration] comment_likes: {_e}')

    seed_fase5()
    fix_fase5_carpeta6()
    seed_bono_habitos()
    seed_bono_organizacion()
    seed_liberacion_emocional()
    seed_programas_marca()
    seed_clases_2026()
    seed_ia()
    seed_clases_2025()
    seed_coach_profesional()

    # Backfill points for existing lesson completions and comments
    try:
        for lp in LessonProgress.query.all():
            if not PointEvent.query.filter_by(user_id=lp.user_id, reason='lesson', ref_id=lp.lesson_id).first():
                db.session.add(PointEvent(user_id=lp.user_id, points=3, reason='lesson', ref_id=lp.lesson_id, created_at=lp.completed_at))
        for c in Comment.query.all():
            if not PointEvent.query.filter_by(user_id=c.user_id, reason='comment', ref_id=c.id).first():
                db.session.add(PointEvent(user_id=c.user_id, points=2, reason='comment', ref_id=c.id, created_at=c.created_at))
        for p in Post.query.all():
            if not PointEvent.query.filter_by(user_id=p.user_id, reason='post', ref_id=p.id).first():
                db.session.add(PointEvent(user_id=p.user_id, points=4, reason='post', ref_id=p.id, created_at=p.created_at))
        db.session.commit()
    except Exception as e:
        print(f'[seed] ERROR en backfill points: {e}')
        db.session.rollback()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)
