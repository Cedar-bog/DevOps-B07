"""Fixture copies shared by the offline repair tests."""

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir(tmp_path):
    source = next(
        parent / "fixtures" / "draft" for parent in Path(__file__).resolve().parents
        if (parent / "fixtures" / "draft").is_dir()
    )
    return Path(shutil.copytree(source, tmp_path / "fixture"))
