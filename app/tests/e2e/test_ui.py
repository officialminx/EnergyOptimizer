import re


def test_dashboard_shows_optimizer_rows_without_signs(app):
    rows = app.locator("#opt-list .opt-row")
    assert rows.count() == 5
    assert rows.first.locator(".opt-w").inner_text().strip() != ""
    # Production and consumption are shown as plain values; the direction is in words.
    for sel in ("#sp", "#sc", "#sg"):
        assert not re.match(r"^[+\-−]", app.locator(sel).inner_text().strip())
    assert app.locator("#sg-dir").inner_text().strip() in ("Bezug", "Einspeisung", "ausgeglichen")


def test_row_opens_the_device(app):
    app.locator("#opt-list .opt-row").first.click()
    card = app.locator("#shc-0")
    assert "open" in card.get_attribute("class")
    assert app.locator("#page-devices").is_visible()


def test_page_change_starts_at_the_top(app):
    app.click(".btm-nav [data-page=chart]")
    app.evaluate("document.documentElement.style.scrollBehavior='auto';window.scrollTo(0,document.body.scrollHeight)")
    assert app.evaluate("window.scrollY") > 0
    app.click(".btm-nav [data-page=settings]")
    app.wait_for_timeout(400)
    assert app.evaluate("window.scrollY") == 0


def test_device_rows_expand_and_remember(app):
    app.click(".btm-nav [data-page=devices]")
    card = app.locator("#shc-1")
    assert "open" not in card.get_attribute("class")
    assert not card.locator(".sc-body").is_visible()
    card.locator(".sc-open").click()
    assert card.locator(".sc-body").is_visible()
    assert card.locator(".sc-open").get_attribute("aria-expanded") == "true"
    app.reload()
    app.wait_for_selector("#shc-1")
    assert "open" in app.locator("#shc-1").get_attribute("class")


def test_data_source_sections_follow_the_choice(app):
    app.click(".btm-nav [data-page=settings]")
    app.click(".set-row[data-set=src]")
    assert app.locator("#f-sl-ip").is_visible()
    app.select_option("#f-src", "modbus")
    assert not app.locator("#f-sl-ip").is_visible()
    assert app.locator("#f-src-host").is_visible()
    assert not app.locator("#f-mb-prod").is_visible()
    app.select_option("#f-mb-preset", "custom")
    assert app.locator("#f-mb-prod").is_visible()
    app.select_option("#f-mb-preset", "huawei")
    assert app.locator("#f-mb-unit").input_value() == "1"
    assert app.locator("#savebar").get_attribute("class").find("on") >= 0


def test_preset_sets_values(app):
    app.click(".btm-nav [data-page=settings]")
    app.click(".set-row[data-set=ctrl]")
    assert "active" in app.locator("#presets [data-p=bal]").get_attribute("class")
    app.click("#presets [data-p=agg]")
    assert app.locator("#f-onm").input_value() == "50"
    assert "active" in app.locator("#presets [data-p=agg]").get_attribute("class")
    app.fill("#f-onm", "77")
    app.dispatch_event("#f-onm", "input")
    assert app.locator("#preset-hint").inner_text().strip() == "Eigene Werte"


def test_simulation_and_day_navigation(app):
    app.click(".btm-nav [data-page=chart]")
    app.click("#card-sim button.bscan")
    app.wait_for_selector("#sim-res .sim-row")
    assert app.locator("#sim-res .sim-row").count() == 3
    assert app.locator("#h-kpis .kpi").count() >= 4
    app.click("#hday button[aria-label='Vorheriger Tag']")
    assert app.locator("#hday-today").is_visible()
    assert "am" in app.locator("#strip-title").text_content()


def test_every_button_has_an_accessible_name(app):
    missing = app.evaluate("""[...document.querySelectorAll('button')].filter(b => {
        const name = (b.getAttribute('aria-label') || b.textContent || b.title || '').trim();
        return !name;
    }).map(b => b.outerHTML.slice(0, 80))""")
    assert missing == []
