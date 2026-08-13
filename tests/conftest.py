from pathlib import Path

import mrcfile
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_MATERIALS = REPO_ROOT / "test_materials"
REAL_MRC = TEST_MATERIALS / "map15_ts_028_10.00Apx.mrc"

# test_materials/ is real research data, kept local-only and excluded from
# git (see .gitignore). Tests that depend on it skip cleanly on a fresh
# clone instead of failing.
requires_test_materials = pytest.mark.skipif(
    not REAL_MRC.exists(), reason="test_materials/ (real fixture) not present locally"
)


@pytest.fixture
def make_mrc(tmp_path):
    def _make(name: str, shape=(5, 64, 64), dtype=np.float32, seed=0):
        path = tmp_path / name
        rng = np.random.default_rng(seed)
        data = rng.normal(loc=100, scale=20, size=shape).astype(dtype)
        with mrcfile.new(str(path), overwrite=True) as mrc:
            mrc.set_data(data)
        return path

    return _make
