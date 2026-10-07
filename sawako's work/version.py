"""Semantic versioning for Kaoruko."""

VERSION_INFO = {
    "major": 1,
    "minor": 0,
    "patch": 0,
    "pre_release": None,   # e.g. "alpha.1", "beta.2", "rc.1"
    "build": None,
}

__version__ = "{major}.{minor}.{patch}".format(**VERSION_INFO)

if VERSION_INFO["pre_release"]:
    __version__ += f"-{VERSION_INFO['pre_release']}"
