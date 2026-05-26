import os

workers    = 3
timeout    = 120
bind       = '0.0.0.0:' + os.environ.get('PORT', '5000')
preload_app = True          # importa app.py UNA sola vez, luego hace fork de workers
worker_class = 'sync'

def post_fork(server, worker):
    """Reinicia el pool de conexiones BD en cada worker después del fork."""
    try:
        from app import db
        db.engine.dispose()
    except Exception:
        pass
