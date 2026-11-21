"""Plain-Playwright replays of the GlobalSQA "XYZ Bank" AngularJS demo (state lives in the browser session)."""

from __future__ import annotations

from typing import Callable

from playwright.sync_api import Page

from .flow import Flow

LOGIN = "/angularJs-protractor/BankingProject/#/login"


def customer(f: Flow, n: int, name: str = "Harry Potter") -> int:
    page = f.page
    f.open(n, LOGIN)
    f.click(n + 1, page.get_by_role("button", name="Customer Login"))
    f.select(n + 2, page.locator("#userSelect"), name)
    f.click(n + 3, page.get_by_role("button", name="Login", exact=True), "click Login")
    return n + 4


def _amount_form(f: Flow, step: int, tab: str, label: str, amount: int) -> None:
    page = f.page
    f.click(step, page.locator(f"button[ng-click='{tab}()']"), f"open the {tab} tab")
    f.see(step, label)
    f.fill(step + 1, page.locator("form input[ng-model='amount']"), str(amount))
    f.press(step + 2, "Enter")


def deposit(f: Flow, n: int, amount: int) -> int:
    _amount_form(f, n, "deposit", "Amount to be Deposited", amount)
    f.see(n + 2, "Deposit Successful")
    return n + 3


def withdraw(f: Flow, n: int, amount: int) -> int:
    _amount_form(f, n, "withdrawl", "Amount to be Withdrawn", amount)
    return n + 3


def manager(f: Flow, n: int) -> int:
    f.open(n, LOGIN)
    f.click(n + 1, f.page.get_by_role("button", name="Bank Manager Login"))
    return n + 2


def customers_search(f: Flow, n: int, query: str) -> None:
    f.click(n, f.page.locator("button[ng-click='showCust()']"), "open Customers")
    f.fill(n + 1, f.page.get_by_placeholder("Search Customer"), query)


def xb01(f: Flow) -> None:
    customer(f, 1)
    f.see(5, "Welcome Harry Potter !!")
    f.see(6, "Account Number : 1004")


def xb02(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 250)
    f.see(8, "Deposit Successful")
    f.see(9, "Balance : 250")


def xb03(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 300)
    withdraw(f, 8, 120)
    f.see(11, "Transaction successful")
    f.see(12, "Balance : 180")


def xb04(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 50)
    withdraw(f, 8, 100)
    f.see(11, "Transaction Failed. You can not withdraw amount more than the balance.")
    f.see(12, "Balance : 50")


def xb05(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 75)
    f.pause(8, 2)
    withdraw(f, 9, 25)
    f.see(11, "Transaction successful")
    f.pause(12, 2)
    f.click(13, f.page.locator("button[ng-click='transactions()']"), "open Transactions")
    f.see(14, "Credit")
    f.see(15, "Debit")
    f.see(16, "Transaction Type")
    f.wait_until(16, "the table does not list exactly the two transactions", lambda: f.page.locator("table tbody tr").count() == 2)


def xb06(f: Flow) -> None:
    customer(f, 1)
    f.select(5, f.page.locator("#accountSelect"), "1006")
    f.see(6, "Account Number : 1006")
    f.see(7, "Currency : Rupee")


def xb07(f: Flow) -> None:
    page = f.page
    manager(f, 1)
    f.click(3, page.locator("button[ng-click='addCust()']"), "open Add Customer")
    f.fill(4, page.get_by_placeholder("First Name"), "Luna")
    f.fill(5, page.get_by_placeholder("Last Name"), "Lovegood")
    f.fill(6, page.get_by_placeholder("Post Code"), "E1 7AA")
    f.press(7, "Enter")
    f.wait_until(7, "no confirmation alert", lambda: any("Customer added successfully" in m for m in f.dialogs))
    customers_search(f, 8, "Luna")
    f.see(10, "Lovegood")
    f.see(11, "E1 7AA")


def xb08(f: Flow) -> None:
    page = f.page
    manager(f, 1)
    f.click(3, page.locator("button[ng-click='openAccount()']"), "open Open Account")
    f.select(4, page.locator("#userSelect"), "Hermoine Granger")
    f.select(5, page.locator("#currency"), "Pound")
    f.click(6, page.get_by_role("button", name="Process"))
    f.wait_until(6, "no account-created alert", lambda: any("Account created successfully" in m for m in f.dialogs))
    customers_search(f, 7, "Granger")
    f.see(9, "1001 1002 1003 1016")


def xb09(f: Flow) -> None:
    manager(f, 1)
    customers_search(f, 3, "Longbottom")
    f.see(5, "Neville")
    f.not_see(6, "Hermoine")


def xb10(f: Flow) -> None:
    f.open(1, LOGIN)

    def log_in_as_ron(p: Page) -> None:
        p.get_by_role("button", name="Customer Login").click()
        p.locator("#userSelect").select_option(label="Ron Weasly")
        p.get_by_role("button", name="Login", exact=True).click()
        p.get_by_text("Welcome Ron Weasly").wait_for()

    def deposit_500(p: Page) -> None:
        p.locator("button[ng-click='deposit()']").click()
        p.locator("form input[ng-model='amount']").fill("500")
        p.locator("form").get_by_role("button", name="Deposit").click()

    f.do(2, "log in as Ron Weasly", log_in_as_ron)
    f.do(3, "deposit 500", deposit_500)
    f.see(4, "Balance : 500")
    f.see(5, "Deposit Successful")


def xb11(f: Flow) -> None:
    f.open(1, LOGIN)

    def lookup(p: Page) -> None:
        p.get_by_role("button", name="Bank Manager Login").click()
        p.locator("button[ng-click='showCust()']").click()
        p.get_by_placeholder("Search Customer").fill("E55656")

    f.do(2, "manager searches post code E55656", lookup)
    f.see(3, "Dumbledore")
    f.not_see(4, "Weasly")


def xbn01(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 50)
    withdraw(f, 8, 100)
    f.see(11, "Transaction successful", timeout_s=5)


def xbn02(f: Flow) -> None:
    customer(f, 1)
    deposit(f, 5, 300)
    withdraw(f, 8, 120)
    f.see(11, "Transaction successful")
    f.see(12, "Balance : 200", timeout_s=5)


def xbn03(f: Flow) -> None:
    manager(f, 1)
    customers_search(f, 3, "Longbottom")
    f.see(5, "Hermoine", timeout_s=5)


def xbn04(f: Flow) -> None:
    f.open(1, LOGIN)

    def log_in_as_albus(p: Page) -> None:
        p.get_by_role("button", name="Customer Login").click()
        p.locator("#userSelect").select_option(label="Albus Dumbledore")
        p.get_by_role("button", name="Login", exact=True).click()
        p.get_by_text("Welcome Albus Dumbledore").wait_for()

    f.do(2, "log in as Albus Dumbledore", log_in_as_albus)
    f.see(3, "Welcome Harry Potter", timeout_s=5)


CHECKS: dict[str, Callable[[Flow], None]] = {
    "xb01_customer_login": xb01,
    "xb02_deposit": xb02,
    "xb03_deposit_then_withdraw": xb03,
    "xb04_withdraw_over_balance": xb04,
    "xb05_transactions_list": xb05,
    "xb06_switch_account": xb06,
    "xb07_manager_add_customer": xb07,
    "xb08_manager_open_account": xb08,
    "xb09_customers_search": xb09,
    "xb10_nl_deposit": xb10,
    "xb11_nl_manager_lookup": xb11,
    "xbn01_withdraw_over_balance_success": xbn01,
    "xbn02_wrong_balance": xbn02,
    "xbn03_search_excludes_others": xbn03,
    "xbn04_nl_wrong_customer_greeting": xbn04,
}
