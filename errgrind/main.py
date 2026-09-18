import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["web"]:
        from .web.__main__ import main as web_main

        return web_main(argv[1:])

    from .cli.app import run_session

    run_session()


if __name__ == "__main__":
    main()
