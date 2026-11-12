from pathlib import Path

from backend_core import ensure_samples_library
from library_paths import ALL_LIBRARY_FOLDERS


def test_ensure_samples_library_does_not_create_subfolders(tmp_path: Path):
    ensure_samples_library(tmp_path, quiet=True)
    assert tmp_path.is_dir()
    assert not any((tmp_path / name).exists() for name in ALL_LIBRARY_FOLDERS)
