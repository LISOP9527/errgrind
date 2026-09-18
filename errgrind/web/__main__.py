"""Start one local Waitress process with independent request threads."""
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description='ErrGrind 本机 WebUI（无账号系统）')
    parser.add_argument('--host', default='127.0.0.1', help='默认仅监听本机')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', help='使用独立 SQLite 路径，例如临时测试数据库')
    args = parser.parse_args(argv)
    try:
        from waitress import serve
        from .app import create_app
        app = create_app(db_path=args.db)
    except ImportError:
        parser.exit(1, "请安装 Web 依赖：python -m pip install -e '.[web]'\n")
    except Exception:
        parser.exit(1, '启动失败：请检查配置文件与数据库路径/权限。\n')
    if args.host not in {'127.0.0.1', 'localhost', '::1'}:
        print('注意：没有内建账号系统，请勿将此端口暴露公网。', flush=True)
    print(f'ErrGrind WebUI: http://{args.host}:{args.port}', flush=True)
    # Record accepts up to three separately validated 20 MB images in one
    # reviewed draft request, plus a small multipart envelope.
    serve(app, host=args.host, port=args.port, threads=8,
          max_request_body_size=61 * 1024 * 1024, expose_tracebacks=False)


if __name__ == '__main__':
    main()
