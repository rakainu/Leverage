"""Pytest configuration and shared fixtures."""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: test that hits real external APIs; skip by default in CI",
    )
