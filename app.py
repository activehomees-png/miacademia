import os
import io
from functools import wraps
from datetime import datetime, timedelta

from flask import (Flask, render_template, redirect, url_for,
                   request, flash, jsonify, abort, send_file)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from sqlalchemy import text

from models import (db, User, Category, Post, Comment,
                    Course, Section, Lesson, LessonFile, Enrollment, LessonProgress, LiveClass,
                    SiteSettings, PointEvent)

app = Flask(__name__)
app.config.from_pyfile('config.py')

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Inicia sesión para continuar.'

# ── Jinja helpers ─────────────────────────────────────────────────────────────

def youtube_embed(url: str) -> str:
    if not url:
        return ''
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

@app.before_request
def update_last_seen():
    if current_user.is_authenticated:
        now = datetime.utcnow()
        if not current_user.last_seen or (now - current_user.last_seen).total_seconds() > 60:
            current_user.last_seen = now
            db.session.commit()

def award_points(user_id, reason, ref_id, pts):
    if not PointEvent.query.filter_by(user_id=user_id, reason=reason, ref_id=ref_id).first():
        db.session.add(PointEvent(user_id=user_id, points=pts, reason=reason, ref_id=ref_id))
        db.session.commit()

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
        if len(pw) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Ese email ya está registrado.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Ese nombre de usuario ya existe.', 'error')
        else:
            user = User(username=username, email=email)
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash('¡Bienvenido a la academia!', 'success')
            return redirect(url_for('community'))
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

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
    return render_template('community/feed.html',
                           posts=posts, categories=categories, active_cat=cat_id,
                           member_count=member_count, admin_count=admin_count,
                           admins=admins, online_users=online_users, top_month=top_month)

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
    return jsonify({'ok': True, 'username': current_user.username,
                    'initials': current_user.initials, 'content': content,
                    'timeago': 'ahora mismo'})

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

# ── COURSES ───────────────────────────────────────────────────────────────────

@app.route('/cursos')
@login_required
def courses():
    all_courses  = Course.query.filter_by(is_published=True).all()
    enrolled_ids = {e.course_id for e in current_user.enrollments}
    return render_template('courses/catalog.html',
                           courses=all_courses, enrolled_ids=enrolled_ids)

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
    return render_template('leaderboard.html', ranking=ranking, period=period, my_pts=my_pts)

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
            'title': c.title,
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

@app.route('/admin/cursos/<int:course_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    db.session.delete(course)
    db.session.commit()
    flash('Curso eliminado.', 'success')
    return redirect(url_for('admin_courses'))

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
        date_str = request.form.get('scheduled_at', '')
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
        )
        db.session.add(lc)
        db.session.commit()
        flash('Clase programada.', 'success')
        return redirect(url_for('admin_live_classes'))
    return render_template('admin/new_live_class.html')

@app.route('/admin/clases/<int:class_id>/borrar', methods=['POST'])
@login_required
@admin_required
def admin_delete_live_class(class_id):
    lc = LiveClass.query.get_or_404(class_id)
    db.session.delete(lc)
    db.session.commit()
    flash('Clase eliminada.', 'success')
    return redirect(url_for('admin_live_classes'))

@app.route('/admin/usuarios')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/usuarios/<int:user_id>/rol', methods=['POST'])
@login_required
@admin_required
def admin_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id != current_user.id:
        user.role = 'admin' if user.role == 'student' else 'student'
        db.session.commit()
    return redirect(url_for('admin_users'))

# ── ERROR PAGES ───────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

# ── INIT ──────────────────────────────────────────────────────────────────────

def seed_db():
    if not SiteSettings.query.first():
        db.session.add(SiteSettings())
    if not User.query.filter_by(role='admin').first():
        admin = User(username='admin', email='admin@academia.com', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
    if not Category.query.first():
        for name, color, emoji in [
            ('General',   '#6366f1', '💬'),
            ('Anuncios',  '#f59e0b', '📢'),
            ('Preguntas', '#10b981', '❓'),
            ('Recursos',  '#3b82f6', '📚'),
        ]:
            db.session.add(Category(name=name, color=color, emoji=emoji))
    db.session.commit()

# Inicializar BD siempre (tanto con gunicorn como directo)
with app.app_context():
    db.create_all()
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
            # site_settings: binary banner
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_data BYTEA"))
            conn.execute(text("ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS community_image_mime VARCHAR(50) DEFAULT 'image/jpeg'"))
            # point_event table (created by db.create_all, but add index hint)
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_user ON point_event(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_point_event_date ON point_event(created_at)"))
            conn.commit()
    except Exception:
        pass
    seed_db()
    # Backfill points for existing lesson completions and comments
    for lp in LessonProgress.query.all():
        if not PointEvent.query.filter_by(user_id=lp.user_id, reason='lesson', ref_id=lp.lesson_id).first():
            db.session.add(PointEvent(user_id=lp.user_id, points=3, reason='lesson', ref_id=lp.lesson_id, created_at=lp.completed_at))
    for c in Comment.query.all():
        if not PointEvent.query.filter_by(user_id=c.user_id, reason='comment', ref_id=c.id).first():
            db.session.add(PointEvent(user_id=c.user_id, points=2, reason='comment', ref_id=c.id, created_at=c.created_at))
    db.session.commit()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)
