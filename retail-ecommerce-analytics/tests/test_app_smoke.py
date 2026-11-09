"""Streamlit smoke tests: the app starts and every page renders without an exception."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["Executive Trading Overview", "Product & Category", "Customer Intelligence", "Cohort & Retention",
         "Marketing Performance", "Returns & Operations", "Forecast & Planning", "Data Quality & Pipeline"]


@pytest.fixture(scope="module")
def app(built_db):
    mp = pytest.MonkeyPatch()
    mp.setenv("RETAIL_DB_PATH", str(built_db["db_path"]))
    at = AppTest.from_file(APP, default_timeout=180).run()
    yield at
    mp.undo()


def test_navigation_lists_all_pages(app):
    assert list(app.sidebar.radio[0].options) == PAGES
    assert not app.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app, page):
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert app.markdown, "page rendered no content"


def test_filters_to_empty_selection_show_empty_state(app):
    app.sidebar.radio[0].set_value("Executive Trading Overview").run()
    app.sidebar.date_input[0].set_value((app.sidebar.date_input[0].min, app.sidebar.date_input[0].min)).run()
    assert not app.exception


def test_deep_link_selects_page(built_db, monkeypatch):
    monkeypatch.setenv("RETAIL_DB_PATH", str(built_db["db_path"]))
    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["page"] = "marketing-performance"
    at.run()
    assert at.sidebar.radio[0].value == "Marketing Performance"
    assert not at.exception
