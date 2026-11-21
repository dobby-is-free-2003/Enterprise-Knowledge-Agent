import pytest

from eka.config import Settings
from eka.datagen import generate_all
from eka.deps import build_deps

DOMAIN = "northwind.example"


@pytest.fixture()
def deps(tmp_path):
    generate_all(tmp_path)
    return build_deps(Settings(data_dir=tmp_path))


@pytest.fixture()
def U(deps):
    def get(name: str):
        u = deps.users.get(f"{name}@{DOMAIN}")
        assert u is not None, name
        return u

    return get
