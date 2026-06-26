import numpy as np
# 兼容 numpy 2.x 与 chromadb 0.4.x
if not hasattr(np, "float_"):
    np.float_ = np.float64
if not hasattr(np, "int_"):
    np.int_ = np.int_
if not hasattr(np, "bool_"):
    np.bool_ = np.bool_

from pathlib import Path

from flask import Flask, Blueprint
from flask_restx import Api

from .core.logger_setup import setup_logging
from .config import Config
from .api import register_namespaces
from flask_cors import CORS

from .core.extensions import db
from .core.response import success


def create_app(test_config=None):
    """创建并初始化 Flask 应用、数据库、跨域与全部 API 路由。"""

    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    Path(app.config["STORAGE_DIR"]).mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=app.config.get("LOG_DIR"))

    CORS(
        app,
        supports_credentials=False,
    )
    db.init_app(app)

    api_blueprint = Blueprint("api", __name__, url_prefix="/api")
    api = Api(
        api_blueprint,
        title=app.config["APP_NAME"],
        version=app.config["APP_VERSION"],
        description="ERP 标书后端基础接口",
        doc="/docs",
    )
    register_namespaces(api)
    app.register_blueprint(api_blueprint)

    @api.errorhandler(ValueError)
    def handle_value_error(error):
        """将业务参数错误统一转换为 400 响应。"""

        return success(message=str(error)), 400

    @api.errorhandler(LookupError)
    def handle_lookup_error(error):
        """将资源不存在错误统一转换为 404 响应。"""

        return success(message=str(error)), 404

    @api.errorhandler(RuntimeError)
    def handle_runtime_error(error):
        """将运行期业务错误统一转换为 500 响应。"""

        return success(message=str(error)), 500

    with app.app_context():
        from .domain import models  # noqa: F401
        from .service_modules import BiddingTaskService

        db.create_all()
        BiddingTaskService.recover_background_tasks()

    return app
