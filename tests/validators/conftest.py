from __future__ import annotations

import pytest
from sparkplan.catalog import load_catalog
from tests.buildspecs import codes, errors, make_spec

__all__ = ["catalog", "codes", "errors", "make_spec"]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()
