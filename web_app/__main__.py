"""通过 ``python -m web_app`` 启动本地Web控制台。"""

import os

from .app import create_app


def main():
    app = create_app()
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "5000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
