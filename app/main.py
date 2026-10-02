"""本地启动入口：python3 -m app.main [port]"""
import sys

from .server import make_server

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    httpd = make_server("127.0.0.1", port)
    print(f"楼道巡查处置系统已启动：http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
