"""Browser test of the sign-in setup: login required, accounts own their reports, moderators by role.
Needs the stub API started with SUPABASE_JWT_SECRET, the mock Supabase server, and a frontend built with
VITE_SUPABASE_URL pointing at it (see run_all.sh)."""
import json, os, re, sys, httpx
from playwright.sync_api import sync_playwright, expect

B = os.getenv("BASE", "http://localhost:4174"); API = os.getenv("API", "http://localhost:8001")
ADMIN = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "e2e-admin-key")}
SP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"); os.makedirs(SP, exist_ok=True)
CHROMIUM = os.getenv("CHROMIUM_PATH") or None
problems, steps = [], []
def ok(n): steps.append(n); print("PASS", n)

def sign_in(page, email, pw="correct-horse-1"):
    page.get_by_label("Email").fill(email); page.get_by_label("Password").fill(pw)
    page.get_by_role("button", name="Sign in", exact=True).click()

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    ctx.route(re.compile(r"https://(tile\.openstreetmap\.org|fonts\.(googleapis|gstatic)\.com)/.*"), lambda r: r.abort())
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

    # a. The server asks for sign-in, so reporting shows the form instead of the report form
    page.goto(B + "/explore?mode=report")
    expect(page.get_by_text("Sign in to report an issue.")).to_be_visible(timeout=10000)
    expect(page.get_by_role("link", name="Sign in")).to_be_visible()
    ok("login required: the report form asks to sign in first")
    # reading stays open
    page.get_by_role("button", name="Compare routes").first.click()
    page.get_by_label("From", exact=True).fill("Alpha"); page.get_by_label("To", exact=True).fill("Beta")
    page.get_by_role("button", name="Compare routes").last.click()
    expect(page.locator("button.card")).to_have_count(2, timeout=15000)
    ok("login required: comparing routes still needs no account")

    # b. A wrong password is refused with the provider's message; the right one signs in
    page.goto(B + "/account")
    sign_in(page, "user@example.com", "wrong-password")
    expect(page.get_by_role("alert")).to_contain_text("Invalid login credentials")
    sign_in(page, "user@example.com")
    expect(page.get_by_text("Signed in as")).to_be_visible(timeout=8000)
    ok("sign-in: wrong password refused, right one accepted")

    # c. A signed-in person reports; the report belongs to their account
    page.goto(B + "/explore?mode=report")
    expect(page.get_by_role("heading", name="Report an issue")).to_be_visible(timeout=10000)
    box = page.locator(".leaflet-container").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.5)
    page.get_by_label("Description").fill("Pavement tiles are broken and uneven here")
    page.get_by_label(re.compile("I have read the privacy notice")).check()
    page.get_by_role("button", name="Submit report").click()
    expect(page.get_by_role("status")).to_be_visible(timeout=8000)
    rows = httpx.get(API + "/api/admin/reports", headers=ADMIN, params={"status": "unverified"}).json()
    assert len(rows) == 1 and rows[0]["reporter_id"].startswith("user:"), rows
    rid = rows[0]["id"]
    ev = httpx.get(f"{API}/api/reports/{rid}/events", headers=ADMIN).json()
    assert ev[0]["actor"].startswith("user:") and "@" not in ev[0]["actor"], ev
    ok("report: saved under the account id, audit trail names a pseudonym, never an email")

    # d. A normal account is not a moderator
    page.goto(B + "/admin")
    expect(page.get_by_text("not accepted")).to_be_visible(timeout=10000)
    ok("admin: a normal account is refused by the server")

    # e. Sign out, sign in as a moderator, moderate without any shared key
    page.goto(B + "/account"); page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_label("Email")).to_be_visible(timeout=8000)
    sign_in(page, "mod@example.com")
    expect(page.get_by_text("Signed in as")).to_be_visible(timeout=8000)
    page.goto(B + "/admin")
    expect(page.get_by_text("Review queue")).to_be_visible(timeout=10000)
    expect(page.get_by_text("signed in as mod@example.com")).to_be_visible()
    item = page.get_by_test_id("admin-report").first
    item.get_by_role("button", name="Verify").click()
    expect(page.get_by_text("Nothing waiting for review")).to_be_visible(timeout=8000)
    ev = httpx.get(f"{API}/api/reports/{rid}/events", headers=ADMIN).json()
    assert ev[-1]["action"] == "status:verified" and ev[-1]["actor"].startswith("mod:"), ev
    ok("moderator account: verifies a report; the audit trail names mod:<pseudonym>")
    page.screenshot(path=os.path.join(SP, "admin_mod.png"))

    # f. The account can still not vote on its own report, and the server's reason is shown
    page.goto(B + "/account"); page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_label("Email")).to_be_visible(timeout=8000)
    sign_in(page, "user@example.com")
    expect(page.get_by_text("Signed in as")).to_be_visible(timeout=8000)
    page.goto(B + "/explore")
    expect(page.get_by_role("button", name="Compare routes").first).to_be_visible()
    page.wait_for_timeout(1500)
    page.evaluate("""() => { const d=[...document.querySelectorAll('path.leaflet-interactive')].filter(p => (p.getAttribute('fill')||'').toLowerCase()==='#1b2733'); d[0].dispatchEvent(new MouseEvent('click',{bubbles:true})); }""")
    expect(page.get_by_label("Selected report")).to_be_visible()
    page.get_by_role("button", name="Still there").click()
    expect(page.get_by_label("Selected report").get_by_role("alert")).to_be_visible(timeout=8000)
    print("   server said:", page.get_by_label("Selected report").get_by_role("alert").inner_text())
    ok("vote: the server's answer is shown to the person")
    browser.close()

print("\nSTEPS:", len(steps)); print("PROBLEMS:", json.dumps(problems, indent=1))
sys.exit(1 if problems else 0)
