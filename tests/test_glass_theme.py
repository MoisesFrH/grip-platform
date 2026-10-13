"""
Tests for the shared liquid-glass stylesheet (app/static/glass.css) and its
wiring into the three internal pages.

Run with:
    .venv/bin/python -m pytest tests/test_glass_theme.py
"""

from pathlib import Path

from app import main

STATIC_DIR = Path(main.__file__).parent / "static"
PAGES = ("crm.html", "dashboard.html", "simulator.html")


def test_glass_css_route_serves_the_stylesheet():
    response = main.glass_css()
    assert Path(response.path).name == "glass.css"
    assert response.media_type == "text/css"
    assert Path(response.path).exists()


def test_glass_css_has_blur_and_accessibility_fallbacks():
    css = (STATIC_DIR / "glass.css").read_text(encoding="utf-8")
    assert "backdrop-filter" in css
    # Browsers without backdrop-filter and users who ask the OS for less
    # transparency must still get readable, near-solid surfaces.
    assert "@supports not" in css
    assert "prefers-reduced-transparency" in css


def test_every_internal_page_loads_glass_css_after_its_own_styles():
    # It has to come AFTER the page's inline <style> to win on equal specificity.
    for page in PAGES:
        html = (STATIC_DIR / page).read_text(encoding="utf-8")
        assert 'href="/assets/glass.css"' in html, page
        assert html.index("</style>") < html.index('href="/assets/glass.css"'), page