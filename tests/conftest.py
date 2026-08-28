"""全局测试配置：每个测试获得一个独立的临时数据库。"""
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"
