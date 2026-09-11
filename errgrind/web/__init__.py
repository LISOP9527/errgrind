"""Optional local Web adapter; importing core ErrGrind needs no web dependency."""


def create_app(**kwargs):
    from .app import create_app as factory
    return factory(**kwargs)


__all__ = ['create_app']
