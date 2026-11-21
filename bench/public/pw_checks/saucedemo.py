"""Plain-Playwright replays of the Swag Labs (saucedemo.com) scenarios."""

from __future__ import annotations

from typing import Callable

from playwright.sync_api import Page

from .flow import Flow, StepFailed

PASSWORD = "secret_sauce"  # published on the Swag Labs login page
PRODUCTS = {0: "Sauce Labs Bike Light", 1: "Sauce Labs Bolt T-Shirt", 2: "Sauce Labs Onesie", 4: "Sauce Labs Backpack", 5: "Sauce Labs Fleece Jacket"}


def login(f: Flow, n: int, user: str, password: str = PASSWORD) -> int:
    page = f.page
    f.open(n, "/")
    f.fill(n + 1, page.get_by_placeholder("Username"), user)
    f.fill(n + 2, page.get_by_placeholder("Password"), password)
    f.click(n + 3, page.get_by_role("button", name="Login", exact=True), "click Login")
    return n + 4


def add(f: Flow, n: int, item_id: int, *, check_name: bool = True) -> int:
    page = f.page
    f.open(n, f"/inventory-item.html?id={item_id}")
    if check_name:
        f.see(n, PRODUCTS[item_id])
    f.click(n + 1, page.get_by_role("button", name="Add to cart"), "click Add to cart")
    return n + 2


def checkout_info(f: Flow, n: int, first: str = "Ada", last: str = "Lovelace", zip_code: str = "10001") -> int:
    """Open cart, Checkout, fill the three fields, Continue: steps n..n+5."""
    page = f.page
    f.open(n, "/cart.html")
    f.click(n + 1, page.get_by_role("button", name="Checkout"), "click Checkout")
    f.fill(n + 2, page.get_by_placeholder("First Name"), first)
    f.fill(n + 3, page.get_by_placeholder("Last Name"), last)
    f.fill(n + 4, page.get_by_placeholder("Zip/Postal Code"), zip_code)
    f.click(n + 5, page.get_by_role("button", name="Continue"), "click Continue")
    return n + 6


def first_product_is(f: Flow, step: int, name: str) -> None:
    f.wait_until(step, f"first product is not {name!r}", lambda: f.page.locator(".inventory_item_name").first.inner_text().strip() == name)


def cart_button(f: Flow, step: int, label: str) -> None:
    f.visible(step, f.page.get_by_role("button", name=label, exact=True), f"{label!r} cart button", timeout_s=5)


def sd01(f: Flow) -> None:
    login(f, 1, "standard_user")
    f.see(5, "Products")
    f.url_has(6, "/inventory.html")


def sd02(f: Flow) -> None:
    login(f, 1, "locked_out_user")
    f.see(5, "Epic sadface: Sorry, this user has been locked out.")
    f.not_see(6, "Add to cart")


def sd03(f: Flow) -> None:
    login(f, 1, "standard_user", "not_the_password")
    f.see(5, "Epic sadface: Username and password do not match any user in this service")


def sd04(f: Flow) -> None:
    page = f.page
    f.open(1, "/")
    f.click(2, page.get_by_role("button", name="Login", exact=True))
    f.see(3, "Epic sadface: Username is required")
    f.fill(4, page.get_by_placeholder("Username"), "standard_user")
    f.click(5, page.get_by_role("button", name="Login", exact=True))
    f.see(6, "Epic sadface: Password is required")


def _sort(f: Flow, step: int, label: str) -> None:
    f.select(step, f.page.get_by_role("combobox", name="Sort products"), label)


def sd05(f: Flow) -> None:
    login(f, 1, "standard_user")
    _sort(f, 5, "Name (Z to A)")
    f.see(6, "Price (high to low) Test.allTheThings() T-Shirt (Red)")
    first_product_is(f, 6, "Test.allTheThings() T-Shirt (Red)")


def sd06(f: Flow) -> None:
    login(f, 1, "standard_user")
    _sort(f, 5, "Price (low to high)")
    f.see(6, "Price (high to low) Sauce Labs Onesie")
    first_product_is(f, 6, "Sauce Labs Onesie")
    f.see(7, "$7.99")


def sd07(f: Flow) -> None:
    login(f, 1, "standard_user")
    _sort(f, 5, "Price (high to low)")
    first_product_is(f, 6, "Sauce Labs Fleece Jacket")
    f.wait_until(6, "first price is not $49.99", lambda: f.page.locator(".inventory_item_price").first.inner_text() == "$49.99")


def sd08(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 4)
    add(f, 7, 0)
    cart_button(f, 9, "Cart, 2 items")


def sd09(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 4)
    add(f, 7, 0)

    def remove_backpack(p: Page) -> None:
        p.goto(f"{f.base_url}/cart.html")
        p.locator(".cart_item", has_text="Sauce Labs Backpack").get_by_role("button", name="Remove").click()

    f.do(9, "open the cart and remove the Backpack", remove_backpack)
    f.see(10, "Sauce Labs Bike Light")
    f.not_see(11, "Sauce Labs Backpack")
    cart_button(f, 11, "Cart, 1 items")


def sd10(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 1)
    add(f, 7, 2)
    f.open(9, "/cart.html")
    for step, text in enumerate(["Your Cart", "Sauce Labs Bolt T-Shirt", "$15.99", "Sauce Labs Onesie", "$7.99"], start=10):
        f.see(step, text)


def sd11(f: Flow) -> None:
    page = f.page
    login(f, 1, "standard_user")
    add(f, 5, 4)
    f.open(7, "/cart.html")
    f.click(8, page.get_by_role("button", name="Checkout"))
    f.click(9, page.get_by_role("button", name="Continue"))
    f.see(10, "Error: First Name is required")


def sd12(f: Flow) -> None:
    page = f.page
    login(f, 1, "standard_user")
    add(f, 5, 4)
    f.open(7, "/cart.html")
    f.click(8, page.get_by_role("button", name="Checkout"))
    f.fill(9, page.get_by_placeholder("First Name"), "Ada")
    f.click(10, page.get_by_role("button", name="Continue"))
    f.see(11, "Error: Last Name is required")
    f.fill(12, page.get_by_placeholder("Last Name"), "Lovelace")
    f.click(13, page.get_by_role("button", name="Continue"))
    f.see(14, "Error: Postal Code is required")


def sd13(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 4)
    add(f, 7, 1)
    checkout_info(f, 9)
    for step, text in enumerate(["Checkout: Overview", "Item total: $45.98", "Tax: $3.68", "Total: $49.66"], start=15):
        f.see(step, text)


def sd14(f: Flow) -> None:
    page = f.page
    login(f, 1, "standard_user")
    f.see(4, "Products")
    add(f, 5, 4)
    cart_button(f, 7, "Cart, 1 items")
    f.open(8, "/cart.html")
    f.see(8, "Sauce Labs Backpack")
    f.click(9, page.get_by_role("button", name="Checkout"))
    f.see(9, "Checkout: Your Information")
    f.fill(10, page.get_by_placeholder("First Name"), "Ada")
    f.fill(11, page.get_by_placeholder("Last Name"), "Lovelace")
    f.fill(12, page.get_by_placeholder("Zip/Postal Code"), "10001")
    f.click(13, page.get_by_role("button", name="Continue"))
    f.see(13, "Total: $32.39")
    f.click(14, page.get_by_role("button", name="Finish"))
    f.see(14, "Thank you for your order!")
    f.click(15, page.get_by_role("button", name="Back Home"))
    f.url_has(15, "/inventory.html")


def sd15(f: Flow) -> None:
    page = f.page
    login(f, 1, "standard_user")
    add(f, 5, 4)
    f.click(7, page.get_by_role("button", name="Open Menu"))
    f.click(8, page.get_by_role("button", name="Reset App State"))
    cart_button(f, 9, "Cart, empty")
    f.click(10, page.get_by_role("button", name="Logout"))
    f.url_is(11, "https://www.saucedemo.com/")
    f.see(12, "Accepted usernames are:")


def sd16(f: Flow) -> None:
    login(f, 1, "standard_user")
    f.open(5, "/cart.html")

    def all_items(p: Page) -> None:
        p.get_by_role("button", name="Open Menu").click()
        p.get_by_role("button", name="All Items").click()

    f.do(6, "menu -> All Items", all_items)
    f.url_has(7, "/inventory.html")
    f.see(8, "Products")


def sd17(f: Flow) -> None:
    page = f.page
    login(f, 1, "standard_user")
    f.click(5, page.get_by_role("button", name="View details for Sauce Labs Fleece Jacket").filter(has_text="Sauce Labs Fleece Jacket"))
    f.url_has(6, "/inventory-item.html?id=5")
    f.see(7, "$49.99")
    f.click(8, page.get_by_role("button", name="Back to products"))
    f.url_has(9, "/inventory.html")


def sd18(f: Flow) -> None:
    f.open(1, "/")

    def sign_in(p: Page) -> None:
        p.get_by_placeholder("Username").fill("performance_glitch_user")
        p.get_by_placeholder("Password").fill(PASSWORD)
        p.get_by_role("button", name="Login", exact=True).click()

    f.do(2, "sign in as performance_glitch_user", sign_in)
    f.see(3, "Products", timeout_s=30)
    f.url_has(4, "/inventory.html")


def sd19(f: Flow) -> None:
    f.open(1, "/")

    def sign_in(p: Page) -> None:
        p.get_by_placeholder("Username").fill("standard_user")
        p.get_by_placeholder("Password").fill(PASSWORD)
        p.get_by_role("button", name="Login", exact=True).click()
        p.wait_for_url("**/inventory.html")

    def add_and_checkout(p: Page) -> None:
        p.locator(".inventory_item", has_text="Sauce Labs Backpack").get_by_role("button", name="Add to cart").click()
        p.locator(".shopping_cart_link").click()
        p.get_by_role("button", name="Checkout").click()

    def info(p: Page) -> None:
        p.get_by_placeholder("First Name").fill("Ada")
        p.get_by_placeholder("Last Name").fill("Lovelace")
        p.get_by_placeholder("Zip/Postal Code").fill("10001")
        p.get_by_role("button", name="Continue").click()
        p.wait_for_url("**/checkout-step-two.html")

    f.do(2, "sign in", sign_in)
    f.do(3, "add the Backpack and go to checkout", add_and_checkout)
    f.do(4, "enter details and continue", info)
    f.click(5, f.page.get_by_role("button", name="Finish"))
    f.see(6, "Thank you for your order!")


def sdn01(f: Flow) -> None:
    login(f, 1, "locked_out_user")
    f.see(5, "Products", timeout_s=5)


def sdn02(f: Flow) -> None:
    login(f, 1, "problem_user")
    _sort(f, 5, "Price (low to high)")
    f.see(6, "Price (high to low) Sauce Labs Onesie", timeout_s=5)


def sdn03(f: Flow) -> None:
    login(f, 1, "problem_user")
    sources = f.page.locator("img.inventory_item_img").evaluate_all("els => els.map(e => e.getAttribute('src'))")
    f.wait_until(5, f"product photos are placeholders: {sorted(set(sources))}", lambda: len(set(sources)) == len(sources) and not any("404" in s for s in sources), timeout_s=1)


def sdn04(f: Flow) -> None:
    login(f, 1, "problem_user")
    add(f, 5, 4, check_name=False)
    checkout_info(f, 7)
    f.see(13, "Checkout: Overview", timeout_s=5)


def sdn05(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 4)
    add(f, 7, 0)
    checkout_info(f, 9)
    f.see(15, "Item total: $49.98", timeout_s=5)


def sdn06(f: Flow) -> None:
    login(f, 1, "error_user")
    add(f, 5, 4)
    checkout_info(f, 7)
    f.click(13, f.page.get_by_role("button", name="Finish"))
    f.see(14, "Thank you for your order!", timeout_s=5)


def sdn07(f: Flow) -> None:
    login(f, 1, "standard_user")
    add(f, 5, 4)
    cart_button(f, 7, "Cart, 2 items")


CHECKS: dict[str, Callable[[Flow], None]] = {
    "sd01_login_success": sd01,
    "sd02_locked_out_message": sd02,
    "sd03_wrong_password_message": sd03,
    "sd04_empty_login_required": sd04,
    "sd05_sort_name_z_to_a": sd05,
    "sd06_sort_price_low_to_high": sd06,
    "sd07_nl_sort_price_high_to_low": sd07,
    "sd08_cart_badge_two_items": sd08,
    "sd09_nl_remove_from_cart": sd09,
    "sd10_cart_contents": sd10,
    "sd11_checkout_first_name_required": sd11,
    "sd12_checkout_last_and_zip_required": sd12,
    "sd13_checkout_overview_totals": sd13,
    "sd14_full_purchase_secret": sd14,
    "sd15_menu_reset_and_logout": sd15,
    "sd16_nl_menu_all_items": sd16,
    "sd17_product_detail_and_back": sd17,
    "sd18_nl_performance_glitch_login": sd18,
    "sd19_nl_full_purchase": sd19,
    "sdn01_locked_out_reaches_inventory": sdn01,
    "sdn02_problem_user_sort": sdn02,
    "sdn03_nl_problem_user_images": sdn03,
    "sdn04_problem_user_last_name": sdn04,
    "sdn05_wrong_item_total": sdn05,
    "sdn06_error_user_cannot_finish": sdn06,
    "sdn07_cart_badge_wrong_count": sdn07,
}

__all__ = ["CHECKS", "StepFailed"]
