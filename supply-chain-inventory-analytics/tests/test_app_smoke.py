"""Streamlit smoke tests: every page renders without exceptions against the test database."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import config.settings as settings

PAGES = ["Executive Overview", "Inventory Health", "Demand & Forecast", "Supplier Performance",
         "Procurement & PO Tracking", "Warehouse & Network", "Replenishment Workbench", "Data Quality & Operations"]


@pytest.fixture(scope="module")
def app(built_db):
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "DB_PATH", built_db[0])
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=120).run()
    yield at
    mp.undo()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app, page):
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error, [e.value for e in app.error]


def test_filters_empty_state(app):
    app.sidebar.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any("Select at least one location" in m.value for m in app.markdown)
    app.sidebar.multiselect[0].set_value(app.sidebar.multiselect[0].options).run()
    assert not app.exception


def test_workbench_what_if(app):
    app.sidebar.radio[0].set_value("Replenishment Workbench").run()
    app.slider[0].set_value(30).run()      # +30% demand
    app.slider[1].set_value(10).run()      # 10-day supplier delay
    assert not app.exception, [e.value for e in app.exception]
