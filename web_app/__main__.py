"""通过 ``python -m web_app`` 启动本地Web控制台。"""

import os

from infrastructure.logging import configure_application_logging

from .app import create_app


def main():
    configure_application_logging()
    app = create_app()
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "5000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
