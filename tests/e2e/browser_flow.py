"""Browser test of the open-sign-in setup: compare, report with consent, vote, moderate, export.
Needs the stub API on API and the built frontend on BASE (see run_all.sh)."""
import json, os, re, sys, httpx
from playwright.sync_api import sync_playwright, expect

B = os.getenv("BASE", "http://localhost:4173"); API = os.getenv("API", "http://localhost:8000")
ADMIN = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "e2e-admin-key")}
SP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"); os.makedirs(SP, exist_ok=True)
CHROMIUM = os.getenv("CHROMIUM_PATH") or None
problems, steps = [], []
EXPECTED = ("tile.openstreetmap", "fonts.g", "ERR_FAILED", "ERR_TUNNEL", "ERR_NAME_NOT_RESOLVED", "status of 422", "status of 401")

def ok(name): steps.append(name); print("PASS", name)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    ctx.route(re.compile(r"https://(tile\.openstreetmap\.org|fonts\.(googleapis|gstatic)\.com)/.*"), lambda r: r.abort())
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" and not any(x in m.text for x in EXPECTED) else None)

    # 1. Home and the server notice path
    page.goto(B + "/"); expect(page.get_by_role("heading", name=re.compile("Compare walking routes"))).to_be_visible(); ok("home renders")

    # 2. Compare with nothing known
    page.goto(B + "/explore")
    page.get_by_label("From", exact=True).fill("Alpha"); page.get_by_label("To", exact=True).fill("Beta")
    page.get_by_role("button", name="Compare routes", exact=False).last.click()
    expect(page.locator("button.card")).to_have_count(2, timeout=15000)
    expect(page.locator("button.card").first).to_contain_text("Not enough data")
    expect(page.get_by_text("Where nothing is known")).to_be_visible()
    expect(page.get_by_text("does not predict crime")).to_be_visible()
    ok("compare: two routes, 'Not enough data', gaps listed, disclaimer shown")
    page.screenshot(path=os.path.join(SP, "explore_empty.png"))

    # 3. Field-audit evidence along route A through the real API, then re-compare
    for i, lng in enumerate([-0.0990, -0.0960, -0.0930, -0.0900, -0.0870]):
        r = httpx.post(API + "/api/reports/field-audit", headers=ADMIN, json={"category": ["streetlight", "footpath"][i % 2], "lat": 51.5, "lng": lng, "description": "Walked and checked"})
        assert r.status_code == 201, r.text
    for i, lng in enumerate([-0.0975, -0.0945, -0.0915, -0.0885]):
        r = httpx.post(API + "/api/reports/field-audit", headers=ADMIN, json={"category": "poor_lighting", "lat": 51.5020, "lng": lng, "description": "Dark stretch"})
        assert r.status_code == 201, r.text
    page.get_by_role("button", name="Compare routes", exact=False).last.click()
    expect(page.locator("button.card").first).not_to_contain_text("Not enough data", timeout=15000)
    expect(page.locator("button.card").first).to_contain_text(re.compile(r"\d+ / 100"))
    expect(page.get_by_text("Ranking:")).to_be_visible()
    expect(page.get_by_text("What the score is made of")).to_be_visible()
    expect(page.get_by_text("Context from OpenStreetMap (not scored)")).to_be_visible()
    ok("compare with evidence: score, range/stability, breakdown, OSM context")
    page.screenshot(path=os.path.join(SP, "explore_scored.png"))

    # 4. Changing the persona re-compares by itself
    calls = []
    page.on("request", lambda r: calls.append(r.url) if r.url.endswith("/api/routes/compare") else None)
    page.get_by_label("Who is walking").select_option("night_walker")
    page.wait_for_timeout(1200)
    assert len(calls) == 1, calls
    expect(page.get_by_test_id("daylight")).not_to_be_empty()
    ok("persona change re-runs the comparison once")
    page.get_by_label("When (optional, defaults to now)").fill("2026-12-15T23:00")
    page.wait_for_timeout(1200)
    assert len(calls) == 2, calls
    expect(page.get_by_test_id("daylight")).to_contain_text("After dark")
    ok("time of day change re-runs and says lighting counts in full after dark")

    # 5. Report flow with consent and the classifier
    page.get_by_role("button", name="Report an issue").click()
    expect(page.get_by_role("heading", name="Report an issue")).to_be_visible()
    box = page.locator(".leaflet-container").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    expect(page.get_by_text("Location set:")).to_be_visible()
    page.get_by_label("Description").fill("The street light is broken near the crossing")
    import io
    from PIL import Image
    jpg = io.BytesIO(); Image.new("RGB", (1200, 900), (90, 120, 150)).save(jpg, "JPEG")
    page.set_input_files("input[type=file]", {"name": "pole.jpg", "mimeType": "image/jpeg", "buffer": jpg.getvalue()})
    expect(page.get_by_test_id("suggestion")).to_contain_text("Broken streetlight", timeout=8000)
    page.get_by_role("button", name="Submit report").click()
    expect(page.get_by_role("alert")).to_contain_text("privacy notice")
    ok("report: the classifier suggests a category; submitting without consent is refused")
    page.get_by_label(re.compile("I have read the privacy notice")).check()
    page.get_by_role("button", name="Submit report").click()
    expect(page.get_by_role("status")).to_contain_text(re.compile("unverified|Saved|moderator", re.I), timeout=8000)
    ok("report: saved after consent")
    page.screenshot(path=os.path.join(SP, "report_saved.png"))
    rid = httpx.get(API + "/api/admin/reports", headers=ADMIN, params={"status": "unverified"}).json()[0]["id"]

    # 6. Confirm / dispute from the map
    page.get_by_role("button", name="Compare routes").first.click()
    with page.expect_response(re.compile(r".*/api/routes/compare")):
        page.get_by_role("button", name="Compare routes", exact=False).last.click()
    expect(page.locator("button.card")).to_have_count(2, timeout=15000)
    page.wait_for_timeout(300)
    dots = "() => [...document.querySelectorAll('path.leaflet-interactive')].filter(p => (p.getAttribute('fill')||'').toLowerCase() === '#1b2733')"
    n = page.evaluate(dots + ".length")
    assert n > 0, "no report markers drawn"
    page.evaluate("(" + dots + ")()[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    expect(page.get_by_label("Selected report")).to_be_visible()
    page.get_by_role("button", name="Still there").click()
    expect(page.get_by_label("Selected report").get_by_role("status")).to_contain_text("Thanks")
    ok("map marker opens the report panel; 'Still there' is accepted")
    page.get_by_role("button", name="This is wrong").click()
    page.get_by_label("What is wrong with it?").fill("ask me on 98765 43210")
    page.get_by_role("button", name="Send to moderators").click()
    expect(page.get_by_label("Selected report").get_by_role("alert")).to_contain_text("Remove phone numbers")
    ok("dispute with a phone number is refused with the server's message")

    # 7. Moderation
    page.goto(B + "/admin")
    expect(page.get_by_text("Only moderators can review reports")).to_be_visible()
    page.get_by_label("Admin key").fill("wrong-key"); page.get_by_role("button", name="Use admin key").click()
    expect(page.get_by_text("not accepted")).to_be_visible(timeout=8000)
    page.get_by_label("Admin key").fill("e2e-admin-key"); page.get_by_role("button", name="Use admin key").click()
    expect(page.get_by_text("Review queue")).to_be_visible(timeout=8000)
    expect(page.get_by_test_id("admin-report").first).to_be_visible()
    ok("admin: wrong key refused by the server, right key opens the queue")
    page.screenshot(path=os.path.join(SP, "admin_queue.png"))
    item = page.get_by_test_id("admin-report").first
    expect(page.get_by_text("with a photo")).to_be_visible()
    item.get_by_role("button", name="View photo").click()
    expect(item.get_by_alt_text("Photo submitted with this report")).to_be_visible(timeout=8000)
    assert page.evaluate("document.querySelector('img.thumb').naturalWidth") > 0
    ok("admin: the photo uploaded from the browser is shown to the moderator")
    item.get_by_role("button", name="Remove photo").click()
    item.get_by_label(re.compile("Why is the photo removed")).fill("shows a face")
    item.get_by_role("button", name="Remove photo, keep report").click()
    expect(item.get_by_role("button", name="View photo")).to_have_count(0, timeout=8000)
    ok("admin: the photo can be removed while the report stays")
    item.get_by_role("button", name="Reject", exact=True).click()
    expect(item.get_by_role("button", name="Reject report")).to_be_disabled()
    item.get_by_label(re.compile("Why is it rejected")).fill("Duplicate of another report")
    item.get_by_role("button", name="Reject report").click()
    expect(page.get_by_text("Nothing waiting for review")).to_be_visible(timeout=8000)
    ok("admin: a rejection needs a reason and then leaves the queue")
    page.get_by_role("button", name=re.compile("Rejected")).click()
    row = page.get_by_test_id("admin-report").first
    expect(row).to_be_visible(timeout=8000)
    row.get_by_role("button", name="History").click()
    expect(row.locator(".events")).to_contain_text("status:rejected")
    expect(row.locator(".events")).to_contain_text("admin")
    expect(row.locator(".events")).to_contain_text("Duplicate of another report")
    ok("admin: the audit trail shows who, what and why")
    row.get_by_role("button", name="Reopen").click()
    expect(page.get_by_text("No rejected reports")).to_be_visible(timeout=8000)
    ok("admin: reopen")

    # 8. Export and retention
    page.get_by_text("Export issues (open data)").click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download").click()
    d = dl.value; path = os.path.join(SP, d.suggested_filename); d.save_as(path)
    csv = open(path).read()
    assert d.suggested_filename == "pathlens-issues-all.csv" and csv.startswith("id,category"), csv[:80]
    assert "Walked and checked" in csv
    ok("export: CSV downloaded with the verified field-audit reports")
    page.get_by_text("Data retention").click()
    page.get_by_role("button", name="Run retention now").click()
    expect(page.get_by_text(re.compile("Deleted 0 rejected"))).to_be_visible(timeout=8000)
    ok("retention can be run from the page")

    # 9. Mobile width
    page.set_viewport_size({"width": 390, "height": 800}); page.goto(B + "/explore"); page.wait_for_timeout(500)
    sw = page.evaluate("document.documentElement.scrollWidth"); assert sw <= 392, sw
    ok("mobile width: no horizontal scroll")
    browser.close()

print("\nSTEPS:", len(steps)); print("PROBLEMS:", json.dumps(problems, indent=1))
sys.exit(1 if problems else 0)
