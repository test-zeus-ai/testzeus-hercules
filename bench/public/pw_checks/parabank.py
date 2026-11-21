"""Plain-Playwright replays of the ParaBank demo scenarios (validation-only forms, ledger-neutral transfers)."""

from __future__ import annotations

from typing import Callable

from playwright.sync_api import Page

from .flow import Flow

PASSWORD = "demo"  # ParaBank's published demo customer john / demo


def login(f: Flow, n: int, password: str = PASSWORD) -> int:
    page = f.page
    f.open(n, "/parabank/index.htm")
    f.fill(n + 1, page.locator("input[name=username]"), "john")
    f.fill(n + 2, page.locator("input[name=password]"), password)
    f.click(n + 3, page.get_by_role("button", name="Log In"), "click Log In")
    return n + 4


def logged_in(f: Flow, n: int) -> int:
    after = login(f, n)
    f.see(after - 1, "Accounts Overview")
    return after


def link(f: Flow, step: int, name: str) -> None:
    """Follow a left-panel link and wait for window load: the forms bind their submit handlers on load."""

    def follow(page: Page) -> None:
        page.get_by_role("link", name=name, exact=True).click()
        page.wait_for_load_state("load")

    f.do(step, f"click {name}", follow)


def accounts_loaded(f: Flow, step: int, selector: str) -> None:
    f.wait_until(step, f"{selector} has no accounts", lambda: f.page.locator(f"{selector} option").count() > 0)


def find_by_amount(f: Flow, n: int, amount: str) -> None:
    page = f.page
    link(f, n, "Find Transactions")
    accounts_loaded(f, n, "#accountId")
    f.fill(n + 1, page.locator("#amount"), amount)
    f.click(n + 2, page.locator("#findByAmount"), "click Find Transactions (by amount)")


def pb01(f: Flow) -> None:
    login(f, 1)
    f.see(5, "Accounts Overview")
    f.see(6, "Balance includes deposits that may be subject to holds")
    f.url_has(7, "/parabank/overview.htm")


def pb02(f: Flow) -> None:
    login(f, 1, "not-the-password")
    f.see(5, "The username and password could not be verified.")


def pb03(f: Flow) -> None:
    f.open(1, "/parabank/index.htm")
    f.click(2, f.page.get_by_role("button", name="Log In"))
    f.see(3, "Please enter a username and password.")


def pb04(f: Flow) -> None:
    logged_in(f, 1)
    f.click(5, f.page.locator("#accountTable tbody a").first, "open the first account")

    def debits_only(page: Page) -> None:
        page.locator("#transactionType").select_option(label="Debit")
        page.get_by_role("button", name="Go").click()

    f.do(6, "show debit transactions", debits_only)
    f.see(7, "Account Details")
    f.not_see(8, "Funds Transfer Received")


def pb05(f: Flow) -> None:
    page = f.page
    logged_in(f, 1)
    link(f, 5, "Transfer Funds")
    f.see(5, "From account #")
    accounts_loaded(f, 5, "#fromAccountId")
    f.fill(6, page.locator("#amount"), "10")
    f.click(7, page.get_by_role("button", name="Transfer"))
    f.see(7, "Transfer Complete!")
    f.see(8, "$10.00 has been transferred")


def pb06(f: Flow) -> None:
    logged_in(f, 1)

    def transfer(page: Page) -> None:
        page.get_by_role("link", name="Transfer Funds", exact=True).click()
        page.wait_for_load_state("load")
        page.locator("#toAccountId option").nth(1).wait_for(state="attached")
        page.locator("#amount").fill("5")
        page.locator("#fromAccountId").select_option(index=0)
        page.locator("#toAccountId").select_option(index=1)
        page.get_by_role("button", name="Transfer").click()

    f.do(5, "transfer $5 from the first to the second account", transfer)
    f.see(6, "Transfer Complete!")
    f.see(7, "$5.00 has been transferred")


def pb07(f: Flow) -> None:
    logged_in(f, 1)
    link(f, 5, "Bill Pay")
    accounts_loaded(f, 5, "#rightPanel select")
    f.click(6, f.page.get_by_role("button", name="Send Payment"))
    for step, text in enumerate(["Payee name is required.", "Zip Code is required.", "The amount cannot be empty."], start=7):
        f.see(step, text)


def pb08(f: Flow) -> None:
    page = f.page
    logged_in(f, 1)
    link(f, 5, "Bill Pay")
    accounts_loaded(f, 5, "#rightPanel select")
    f.fill(6, page.locator("input[name='payee.accountNumber']"), "12345")
    f.fill(7, page.locator("input[name='verifyAccount']"), "54321")
    f.fill(8, page.locator("input[name='amount']"), "abc")
    f.click(9, page.get_by_role("button", name="Send Payment"))
    f.see(10, "The account numbers do not match.")
    f.see(11, "Please enter a valid amount.")
    f.not_see(11, "Bill Payment Complete", settle_s=0)


def pb09(f: Flow) -> None:
    logged_in(f, 1)
    find_by_amount(f, 5, "100")
    f.see(8, "Transaction Results")


def pb10(f: Flow) -> None:
    logged_in(f, 1)
    find_by_amount(f, 5, "abc")
    f.see(8, "Invalid amount")


def pb11(f: Flow) -> None:
    logged_in(f, 1)
    link(f, 5, "Request Loan")
    accounts_loaded(f, 5, "#fromAccountId")
    f.click(6, f.page.get_by_role("button", name="Apply Now"))
    f.see(7, "An internal error has occurred and has been logged.")


def pb12(f: Flow) -> None:
    page = f.page
    logged_in(f, 1)
    link(f, 5, "Update Contact Info")
    f.see(6, "Update Profile")
    first_name = page.locator("[id='customer.firstName']")
    f.wait_until(6, "profile did not load", lambda: first_name.input_value() != "")
    f.fill(7, first_name, "")
    f.click(8, page.get_by_role("button", name="Update Profile"))
    f.see(9, "First name is required.")
    f.not_see(9, "Profile Updated", settle_s=0)


def pb13(f: Flow) -> None:
    logged_in(f, 1)
    link(f, 5, "Log Out")
    f.see(6, "Customer Login")
    f.not_see(7, "Accounts Overview")


def pbn01(f: Flow) -> None:
    login(f, 1, "not-the-password")
    f.see(5, "Accounts Overview", timeout_s=6)


def pbn02(f: Flow) -> None:
    logged_in(f, 1)
    link(f, 5, "Bill Pay")
    accounts_loaded(f, 5, "#rightPanel select")
    f.click(6, f.page.get_by_role("button", name="Send Payment"))
    f.see(7, "Bill Payment Complete", timeout_s=6)


def pbn03(f: Flow) -> None:
    logged_in(f, 1)

    def submit_empty(page: Page) -> None:
        page.get_by_role("link", name="Request Loan", exact=True).click()
        page.wait_for_load_state("load")
        page.locator("#fromAccountId option").first.wait_for(state="attached")
        page.get_by_role("button", name="Apply Now").click()

    f.do(5, "submit the empty loan form", submit_empty)
    f.see(6, "Loan amount is required", timeout_s=6)


def pbn04(f: Flow) -> None:
    page = f.page
    logged_in(f, 1)
    link(f, 5, "Transfer Funds")
    accounts_loaded(f, 5, "#fromAccountId")
    f.fill(6, page.locator("#amount"), "10")
    f.click(7, page.get_by_role("button", name="Transfer"))
    f.see(8, "Transfer Complete!")
    f.see(9, "$100.00 has been transferred", timeout_s=6)


CHECKS: dict[str, Callable[[Flow], None]] = {
    "pb01_login_overview": pb01,
    "pb02_invalid_login": pb02,
    "pb03_empty_login": pb03,
    "pb04_nl_activity_debits_only": pb04,
    "pb05_transfer_funds_secret": pb05,
    "pb06_nl_transfer_between_accounts": pb06,
    "pb07_billpay_empty_validation": pb07,
    "pb08_billpay_account_mismatch": pb08,
    "pb09_find_by_amount": pb09,
    "pb10_find_invalid_amount": pb10,
    "pb11_request_loan_empty": pb11,
    "pb12_update_contact_validation": pb12,
    "pb13_nl_logout": pb13,
    "pbn01_invalid_login_overview": pbn01,
    "pbn02_billpay_empty_complete": pbn02,
    "pbn03_nl_loan_friendly_validation": pbn03,
    "pbn04_transfer_wrong_amount": pbn04,
}
