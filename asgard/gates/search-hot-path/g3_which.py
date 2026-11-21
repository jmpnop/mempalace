"""pytest plugin for G3: record which mempalace package the run imported."""

import os


def pytest_sessionstart(session):
    import mempalace

    with open(os.environ["G3_WHICH_OUT"], "w") as f:
        f.write(os.path.realpath(mempalace.__file__))
