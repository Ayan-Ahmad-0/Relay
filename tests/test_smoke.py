"""Smoke test verifying project setup and imports."""

import relay


def test_package_version():
    assert relay.__version__ == "0.1.0"
