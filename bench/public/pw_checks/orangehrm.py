"""Plain-Playwright replays of the OrangeHRM open-source demo scenarios (read, search and filter only)."""

from __future__ import annotations

from typing import Callable

from playwright.sync_api import Locator, Page

from .flow import Flow

PASSWORD = "admin123"  # published on the OrangeHRM demo login page
PIM = "/web/index.php/pim/viewEmployeeList"
USERS = "/web/index.php/admin/viewSystemUsers"
SLOW_S = 30.0


def group(f: Flow, label: str) -> Locator:
    """The form group whose label is ``label`` (OrangeHRM labels are not tied to their inputs)."""
    return f.page.locator(".oxd-input-group").filter(has=f.page.locator("label", has_text=label))


def login(f: Flow, n: int, password: str = PASSWORD) -> int:
    page = f.page
    f.open(n, "/web/index.php/auth/login")
    f.fill(n + 1, page.get_by_placeholder("Username"), "Admin")
    f.fill(n + 2, page.get_by_placeholder("Password"), password)
    f.click(n + 3, page.get_by_role("button", name="Login"), "click Login")
    return n + 4


def logged_in(f: Flow, n: int) -> int:
    after = login(f, n)
    f.url_has(after - 1, "/dashboard/index", timeout_s=SLOW_S)
    return after


def choose(f: Flow, step: int, label: str, option: str) -> None:
    """Pick ``option`` in a custom OrangeHRM dropdown (div.oxd-select-text plus role=option items)."""

    def pick(page: Page) -> None:
        group(f, label).locator(".oxd-select-text").click()
        page.get_by_role("option", name=option, exact=True).click()

    f.do(step, f"choose {option!r} in {label!r}", pick)


def search(f: Flow, step: int) -> None:
    f.click(step, f.page.get_by_role("button", name="Search"), "click Search")


def column_values(f: Flow, index: int) -> list[str]:
    script = "(cards, i) => cards.map(c => ((c.querySelectorAll('.oxd-table-cell')[i] || {}).innerText || '').trim())"
    return f.page.locator(".oxd-table-card").evaluate_all(script, index)


def all_rows(f: Flow, step: int, index: int, value: str) -> None:
    def holds() -> bool:
        values = column_values(f, index)
        return bool(values) and all(v == value for v in values)

    f.wait_until(step, f"not every row has {value!r}", holds)


def oh01(f: Flow) -> None:
    login(f, 1)
    f.see(5, "Dashboard", timeout_s=SLOW_S)
    f.url_has(6, "/web/index.php/dashboard/index")


def oh02(f: Flow) -> None:
    login(f, 1, "not-the-password")
    f.see(5, "Invalid credentials", timeout_s=SLOW_S)
    f.url_has(6, "/auth/login")


def oh03(f: Flow) -> None:
    f.open(1, "/web/index.php/auth/login")
    f.click(2, f.page.get_by_role("button", name="Login"))
    f.see(3, "Required")
    f.url_has(4, "/auth/login")


def oh04(f: Flow) -> None:
    login(f, 1)
    for step, text in enumerate(["Time at Work", "My Actions", "Quick Launch", "Employees on Leave Today", "Employee Distribution by Sub Unit"], start=5):
        f.see(step, text, timeout_s=SLOW_S)


def oh05(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, PIM)
    f.fill(6, group(f, "Employee Name").locator("input"), "Russel")
    search(f, 7)
    f.see(8, "Hamilton", timeout_s=SLOW_S)
    f.see(9, "Record Found")


def oh06(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, PIM)
    f.fill(6, group(f, "Employee Name").locator("input"), "Zzqx Nobody")
    search(f, 7)
    f.see(8, "No Records Found", timeout_s=SLOW_S)


def oh07(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, PIM)
    choose(f, 6, "Employment Status", "Full-Time Permanent")
    search(f, 7)
    f.see(8, "Russel", timeout_s=SLOW_S)
    all_rows(f, 9, 5, "Full-Time Permanent")
    f.see(9, "Full-Time Permanent")


def oh08(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, PIM)
    choose(f, 6, "Job Title", "Software Engineer")
    search(f, 7)
    all_rows(f, 8, 4, "Software Engineer")
    f.see(8, "Russel", timeout_s=SLOW_S)
    f.see(9, "Hamilton")


def oh09(f: Flow) -> None:
    logged_in(f, 1)
    f.see(4, "Dashboard", timeout_s=SLOW_S)
    f.open(5, USERS)
    f.see(5, "System Users", timeout_s=SLOW_S)
    f.fill(6, group(f, "Username").locator("input"), "Admin")
    search(f, 7)
    f.see(7, "(1) Record Found", timeout_s=SLOW_S)
    f.see(8, "Enabled")


def oh10(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, USERS)
    choose(f, 6, "User Role", "ESS")
    search(f, 6)
    f.see(7, "Records Found", timeout_s=SLOW_S)
    all_rows(f, 7, 2, "ESS")
    f.see(8, "Records Found")


def oh11(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, "/web/index.php/leave/viewLeaveList")
    f.fill(6, group(f, "From Date").locator("input"), "2026-31-12")
    f.fill(7, group(f, "To Date").locator("input"), "2026-01-01")
    search(f, 8)
    f.see(9, "To date should be after from date")


def oh12(f: Flow) -> None:
    page = f.page
    logged_in(f, 1)
    f.open(5, "/web/index.php/directory/viewDirectory")
    f.do(6, "type 'Peter' in Employee Name", lambda p: group(f, "Employee Name").locator("input").press_sequentially("Peter", delay=80))
    f.click(7, page.get_by_role("option", name="Peter Mac Anderson"), "click the suggestion")
    search(f, 8)
    f.see(9, "(1) Record Found", timeout_s=SLOW_S)
    f.see(10, "Chief Financial Officer")


def oh13(f: Flow) -> None:
    logged_in(f, 1)
    f.click(5, f.page.get_by_role("link", name="My Info"))
    f.see(6, "Personal Details", timeout_s=SLOW_S)
    f.see(7, "Employee Full Name")
    f.url_has(8, "/pim/viewPersonalDetails")


def oh14(f: Flow) -> None:
    logged_in(f, 1)

    def log_out(p: Page) -> None:
        p.locator(".oxd-userdropdown-tab").click()
        p.get_by_role("menuitem", name="Logout").click()

    f.do(5, "user menu -> Logout", log_out)
    f.url_has(6, "/auth/login", timeout_s=SLOW_S)
    f.see(7, "Forgot your password?")


def oh15(f: Flow) -> None:
    page = f.page
    f.open(1, "/web/index.php/auth/login")
    f.click(2, page.get_by_text("Forgot your password?"))
    f.see(3, "Please enter your username to identify your account to reset your password", timeout_s=SLOW_S)
    f.url_has(4, "/auth/requestPasswordResetCode")
    f.click(5, page.get_by_role("button", name="Reset Password"))
    f.see(6, "Required")
    f.click(7, page.get_by_role("button", name="Cancel"))
    f.url_has(8, "/auth/login")


def oh16(f: Flow) -> None:
    logged_in(f, 1)
    f.click(5, f.page.get_by_role("button", name="Leave List"), "Quick Launch: Leave List")
    f.url_has(6, "/leave/viewLeaveList", timeout_s=SLOW_S)
    f.see(7, "Show Leave with Status", timeout_s=SLOW_S)


def ohn01(f: Flow) -> None:
    login(f, 1, "not-the-password")
    f.see(5, "Dashboard", timeout_s=10)


def ohn02(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, USERS)
    f.fill(6, group(f, "Username").locator("input"), "Admin")
    search(f, 7)
    f.see(8, "(2) Records Found", timeout_s=10)


def ohn03(f: Flow) -> None:
    logged_in(f, 1)
    f.open(5, PIM)

    def search_nobody(page: Page) -> None:
        group(f, "Employee Name").locator("input").fill("Zzqx Nobody")
        page.get_by_role("button", name="Search").click()

    f.do(6, "search for Zzqx Nobody", search_nobody)
    f.see(7, "(1) Record Found", timeout_s=10)


def ohn04(f: Flow) -> None:
    f.open(1, "/web/index.php/auth/login")
    f.click(2, f.page.get_by_text("Forgot your password?"))
    f.see(3, "Reset Password link sent successfully", timeout_s=8)


CHECKS: dict[str, Callable[[Flow], None]] = {
    "oh01_login_success": oh01,
    "oh02_invalid_credentials": oh02,
    "oh03_empty_login_required": oh03,
    "oh04_dashboard_widgets": oh04,
    "oh05_pim_search_by_name": oh05,
    "oh06_pim_search_no_match": oh06,
    "oh07_pim_filter_employment_status": oh07,
    "oh08_pim_filter_job_title": oh08,
    "oh09_admin_user_search_secret": oh09,
    "oh10_nl_admin_filter_role": oh10,
    "oh11_leave_list_date_order": oh11,
    "oh12_directory_search": oh12,
    "oh13_nl_my_info": oh13,
    "oh14_nl_logout": oh14,
    "oh15_forgot_password_page": oh15,
    "oh16_nl_quick_launch_leave_list": oh16,
    "ohn01_invalid_login_reaches_dashboard": ohn01,
    "ohn02_admin_search_two_records": ohn02,
    "ohn03_nl_unknown_employee_found": ohn03,
    "ohn04_forgot_password_link_sent": ohn04,
}
