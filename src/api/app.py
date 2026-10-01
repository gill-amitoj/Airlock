"""
Flask application factory.

Creates and configures the Flask application with all necessary
extensions and error handlers.
"""

import hmac
import logging
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from src.config import get_config
from src.persistence import Database, get_database

logger = logging.getLogger(__name__)

# The dashboard (index.html, app.js, styles.css) is served from the API's own
# origin, so a deployed instance needs no separate static host.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Sent on every response. The CSP allows only same-origin scripts, so injected
# markup cannot run script even if an escaping bug slips into the dashboard.
# Inline styles stay allowed because the dashboard uses style attributes.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def create_app(config=None) -> Flask:
    """
    Application factory for creating Flask app.
    
    Args:
        config: Optional configuration object
        
    Returns:
        Configured Flask application
    """
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

    # Load configuration
    app_config = config or get_config()
    # Fail closed: a production deploy missing its key must not serve open writes.
    if app_config.FLASK_ENV == "production" and not app_config.API_KEY:
        raise RuntimeError("API_KEY must be set when FLASK_ENV is production")
    app.config["SECRET_KEY"] = app_config.SECRET_KEY
    app.config["DEBUG"] = app_config.FLASK_DEBUG
    app.config["MAX_CONTENT_LENGTH"] = app_config.MAX_REQUEST_BYTES

    # Behind a proxy, take the client IP from X-Forwarded-For (needed for
    # per-client rate limits) - but only from the configured number of hops,
    # so a client cannot spoof its address by sending the header itself.
    if app_config.TRUSTED_PROXY_HOPS:
        hops = app_config.TRUSTED_PROXY_HOPS
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    # CORS is only needed when the dashboard is opened from file:// locally.
    origins = [o.strip() for o in app_config.CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        CORS(app, origins=origins)

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[app_config.RATE_LIMIT_DEFAULT],
        storage_uri=app_config.RATE_LIMIT_STORAGE_URI,
        # A Redis outage should not take the API down with it.
        swallow_errors=True,
    )
    app.extensions["rate_limiter"] = limiter

    # Runs after the rate limiter's own check, so failed key guesses are throttled too.
    @app.before_request
    def require_api_key_for_writes():
        if not app_config.API_KEY or request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        supplied = request.headers.get("X-API-Key", "")
        if hmac.compare_digest(supplied.encode(), app_config.API_KEY.encode()):
            return None
        return jsonify({
            "error": {
                "code": 401,
                "name": "Unauthorized",
                "message": "A valid X-API-Key header is required to make changes",
            }
        }), 401

    @app.after_request
    def set_security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
    
    # Store config for access in routes
    app.config["APP_CONFIG"] = app_config
    
    # Initialize database
    db = get_database()
    app.config["DATABASE"] = db
    
    # Register error handlers
    register_error_handlers(app)
    
    # Register routes
    from .routes import register_routes
    register_routes(app, limiter, app_config.RATE_LIMIT_WRITES)
    
    @app.route("/")
    def dashboard():
        """Serve the dashboard."""
        return app.send_static_file("index.html")

    # Health check endpoint
    @app.route("/health")
    @limiter.exempt
    def health_check():
        """Health check endpoint."""
        db = app.config["DATABASE"]
        db_healthy = db.health_check()
        
        # Try Redis health check if queue is available
        redis_healthy = True
        try:
            from src.worker import TaskQueue
            queue = TaskQueue()
            redis_healthy = queue.health_check()
        except Exception:
            redis_healthy = False
        
        status = "healthy" if (db_healthy and redis_healthy) else "unhealthy"
        status_code = 200 if status == "healthy" else 503
        
        return jsonify({
            "status": status,
            "database": "healthy" if db_healthy else "unhealthy",
            "redis": "healthy" if redis_healthy else "unhealthy",
        }), status_code
    
    logger.info("Flask application created")
    return app


def register_error_handlers(app: Flask) -> None:
    """Register error handlers for the application."""
    
    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        """Handle HTTP exceptions."""
        response = {
            "error": {
                "code": e.code,
                "name": e.name,
                "message": e.description,
            }
        }
        return jsonify(response), e.code
    
    @app.errorhandler(ValueError)
    def handle_value_error(e: ValueError):
        """Handle validation errors."""
        return jsonify({
            "error": {
                "code": 400,
                "name": "Bad Request",
                "message": str(e),
            }
        }), 400
    
    @app.errorhandler(Exception)
    def handle_generic_exception(e: Exception):
        """Handle unexpected errors."""
        logger.exception(f"Unhandled exception: {e}")
        return jsonify({
            "error": {
                "code": 500,
                "name": "Internal Server Error",
                "message": "An unexpected error occurred",
            }
        }), 500
