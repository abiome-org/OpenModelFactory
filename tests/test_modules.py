import io
import tarfile

import pytest
from omf.errors import ValidationError
from omf.modules import extract_module_package, package_module


def test_reproducible_package_and_links_rejected(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "x").write_text("x")
    a, b = tmp_path / "a.tar", tmp_path / "b.tar"
    assert package_module(root, a) == package_module(root, b)
    (root / "link").symlink_to("x")
    with pytest.raises(ValidationError, match="unsupported"):
        package_module(root, tmp_path / "c.tar")


def test_safe_module_extraction_and_traversal_rejection(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "entry.py").write_text("print('ok')")
    package = tmp_path / "module.tar"
    package_module(root, package)
    destination = extract_module_package(package, tmp_path / "extracted")
    assert (destination / "entry.py").read_text() == "print('ok')"
    with pytest.raises(ValidationError, match="already exists"):
        extract_module_package(package, destination)

    unsafe = tmp_path / "unsafe.tar"
    with tarfile.open(unsafe, "w") as archive:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValidationError, match="unsafe"):
        extract_module_package(unsafe, tmp_path / "unsafe-out")
    assert not (tmp_path / "escape").exists()
