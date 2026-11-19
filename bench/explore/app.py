"""Acme Pantry: a small shop with seeded bugs, for measuring exploration (``hercules explore``).

Each seeded bug (see ``bugs.yaml``) is switched on by name. With none on, the app is the "clean build": a session
there should report no findings. Standard library only; state is in memory, per signed-in session.

Usage::

    python bench/explore/app.py --bugs all          # every seeded bug, a free port printed on start
    python bench/explore/app.py --bugs none         # the clean build
    python bench/explore/app.py --bugs B3,B5 --port 8100

Sign in at ``/login`` with any name: the session cookie is the starting state.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import email.parser
import email.policy
import html
import http.server
import io
import itertools
import json
import secrets
import socket
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HOST = "127.0.0.1"
MANIFEST = Path(__file__).resolve().parent / "bugs.yaml"
PRODUCTS = {"tea": ("Green tea", 4), "rice": ("Basmati rice", 6), "oil": ("Olive oil", 9)}
_ids = itertools.count(1)


def all_bugs() -> tuple[str, ...]:
    return tuple(item["id"] for item in yaml.safe_load(MANIFEST.read_text())["bugs"])


@dataclass
class Account:
    name: str
    email: str = ""
    cart: dict[str, int] = field(default_factory=dict)
    badge: int = 0  # what the header shows; equals the cart's item count unless B4 is on
    orders: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    returns: list[dict[str, object]] = field(default_factory=list)
    token: str = ""  # the order form's one-time token (double submit protection)


class App:
    def __init__(self, bugs: tuple[str, ...]) -> None:
        self.bugs = frozenset(bugs)
        self.sessions: dict[str, Account] = {}
        self.lock = threading.Lock()

    def on(self, bug: str) -> bool:
        return bug in self.bugs


def page(title: str, body: str, account: Account | None = None, *, script: str = "") -> str:
    nav = ""
    if account is not None:
        nav = (
            '<nav><a href="/home">Home</a> · <a href="/shop">Shop</a> · '
            f'<a href="/cart" id="cart-link">Cart (<span id="badge">{account.badge}</span>)</a> · '
            '<a href="/orders">Orders</a> · <a href="/returns">Returns</a> · <a href="/notes">Notes</a> · <a href="/profile">Profile</a> · '
            '<form method="get" action="/search" style="display:inline"><label>Search <input name="q"></label> '
            "<button>Go</button></form></nav>"
        )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)} · Acme Pantry</title></head>"
        f"<body><header><strong>Acme Pantry</strong> {nav}</header><main><h1>{html.escape(title)}</h1>{body}</main>"
        f"{f'<script>{script}</script>' if script else ''}</body></html>"
    )


class Handler(http.server.BaseHTTPRequestHandler):
    app: App
    verbose = False

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if self.verbose:
            super().log_message(format, *args)

    # ------------------------------------------------------------------ plumbing

    def _account(self) -> Account | None:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "pantry_session":
                return self.app.sessions.get(value)
        return None

    def _send(self, status: int, text: str, *, content_type: str = "text/html; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self) -> dict[str, str]:
        """The posted form. A multipart form's files land in ``self.files`` as {field: (file name, size in bytes)}."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.files: dict[str, tuple[str, int]] = {}
        content_type = self.headers.get("Content-Type") or ""
        if content_type.startswith("multipart/form-data"):
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw)
            form: dict[str, str] = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                data = part.get_payload(decode=True) or b""
                if part.get_filename() is not None:
                    if part.get_filename():
                        self.files[name] = (Path(part.get_filename()).name, len(data))
                else:
                    form[name] = data.decode("utf-8", "replace")
            return form
        text = raw.decode("utf-8")
        return {key: values[-1] for key, values in urllib.parse.parse_qs(text, keep_blank_values=True).items()}

    def _error(self, account: Account | None) -> None:
        self._send(500, page("Something went wrong", "<p>Internal server error.</p>", account))

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        account = self._account()
        if url.path in ("/", "/login"):
            self._send(200, page("Sign in", '<form method="post" action="/login"><label>Name <input name="name"></label> <button>Sign in</button></form>'))
            return
        if account is None:
            self._redirect("/login")
            return
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(url.query, keep_blank_values=True).items()}
        with self.app.lock:
            route = getattr(self, "get_" + url.path.strip("/").replace("-", "_").replace(".", "_"), None)
            if route is None:
                self._send(404, page("Not found", "<p>No such page.</p>", account))
                return
            route(account, query)

    def do_POST(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        form = self._form()
        if url.path == "/login":
            name = form.get("name", "").strip() or "guest"
            session = secrets.token_hex(8)
            self.app.sessions[session] = Account(name=name)
            self._redirect("/home", {"Set-Cookie": f"pantry_session={session}; Path=/; HttpOnly; SameSite=Lax"})
            return
        account = self._account()
        if account is None:
            self._redirect("/login")
            return
        with self.app.lock:
            route = getattr(self, "post_" + url.path.strip("/").replace("-", "_").replace("/", "_"), None)
            if route is None:
                self._send(404, page("Not found", "<p>No such action.</p>", account))
                return
            route(account, form)

    # home, search
    def get_home(self, account: Account, query: dict[str, str]) -> None:
        self._send(200, page(f"Welcome, {account.name}", "<p>Fresh pantry staples, delivered.</p>", account))

    def get_search(self, account: Account, query: dict[str, str]) -> None:
        term = query.get("q", "")
        if not term.strip():
            if self.app.on("B7"):
                self._error(account)  # B7: an empty search is a server error
                return
            self._send(200, page("Search", "<p>Type something to search for.</p>", account))
            return
        hits = [name for name, _ in PRODUCTS.values() if term.lower() in name.lower()]
        rows = "".join(f"<li>{html.escape(hit)}</li>" for hit in hits) or "<li>No products found.</li>"
        self._send(200, page("Search", f"<p>Results for “{html.escape(term)}”</p><ul>{rows}</ul>", account))

    # profile
    def get_profile(self, account: Account, query: dict[str, str]) -> None:
        saved = '<p role="status">Profile saved.</p>' if query.get("saved") else ""
        error = f'<p role="alert">{html.escape(query["error"])}</p>' if query.get("error") else ""
        script = ""
        if self.app.on("B2"):  # B2: a name with an emoji throws in the page's greeting code
            script = "const n = document.getElementById('shown-name').textContent; if (/\\p{Extended_Pictographic}/u.test(n)) { null.greeting(); }"
        body = (
            f"{saved}{error}<p>Signed in as <span id='shown-name'>{html.escape(account.name)}</span></p>"
            '<form method="post" action="/profile">'
            f'<label>Display name <input name="name" value="{html.escape(account.name)}"></label> '
            f'<label>Email <input name="email" value="{html.escape(account.email)}"></label> '
            "<button>Save profile</button></form>"
        )
        self._send(200, page("Profile", body, account, script=script))

    def post_profile(self, account: Account, form: dict[str, str]) -> None:
        name, email = form.get("name", "").strip(), form.get("email", "").strip()
        if len(name) > 40:
            if self.app.on("B1"):
                self._error(account)  # B1: a long name is a server error instead of a message
                return
            self._redirect("/profile?error=" + urllib.parse.quote("Display name must be at most 40 characters."))
            return
        if not name:
            self._redirect("/profile?error=" + urllib.parse.quote("Display name is required."))
            return
        if email and "@" not in email:
            self._redirect("/profile?error=" + urllib.parse.quote("Enter a valid email address."))
            return
        account.name, account.email = name, email
        self._redirect("/profile?saved=1")

    # shop and cart
    def get_shop(self, account: Account, query: dict[str, str]) -> None:
        error = f'<p role="alert">{html.escape(query["error"])}</p>' if query.get("error") else ""
        rows = "".join(
            f'<li><form method="post" action="/cart/add"><input type="hidden" name="item" value="{key}">'
            f'{html.escape(name)} (${price}) <label>Quantity for {html.escape(name)} <input name="qty" value="1"></label> '
            f"<button>Add {html.escape(name)} to cart</button></form></li>"
            for key, (name, price) in PRODUCTS.items()
        )
        self._send(200, page("Shop", f"{error}<ul>{rows}</ul>", account))

    def post_cart_add(self, account: Account, form: dict[str, str]) -> None:
        item = form.get("item", "")
        try:
            qty = int(form.get("qty", "1"))
        except ValueError:
            self._redirect("/shop?error=" + urllib.parse.quote("Quantity must be a whole number."))
            return
        if item not in PRODUCTS:
            self._redirect("/shop?error=" + urllib.parse.quote("Unknown product."))
            return
        if qty < 1 and not self.app.on("B3"):  # B3: zero or negative quantities are accepted
            self._redirect("/shop?error=" + urllib.parse.quote("Quantity must be at least 1."))
            return
        account.cart[item] = account.cart.get(item, 0) + qty
        account.badge = sum(account.cart.values())
        self._redirect("/cart")

    def get_cart(self, account: Account, query: dict[str, str]) -> None:
        notice = '<p role="status">Coupon applied.</p>' if query.get("coupon") == "ok" else ""
        if query.get("coupon") == "bad":
            notice = '<p role="alert">That coupon is not valid.</p>'
        rows = "".join(
            f"<tr><td>{html.escape(PRODUCTS[item][0])}</td><td>{qty}</td><td>${PRODUCTS[item][1] * qty}</td>"
            f'<td><form method="post" action="/cart/remove"><input type="hidden" name="item" value="{item}">'
            f"<button>Remove {html.escape(PRODUCTS[item][0])}</button></form></td></tr>"
            for item, qty in account.cart.items()
        )
        total = sum(PRODUCTS[item][1] * qty for item, qty in account.cart.items())
        account.token = secrets.token_hex(6)
        coupon_action = "" if self.app.on("B8") else ' action="/cart/coupon" method="post"'
        coupon_button = '<button type="button">Apply coupon</button>' if self.app.on("B8") else "<button>Apply coupon</button>"  # B8: a dead button
        body = (
            f"{notice}<table><thead><tr><th>Item</th><th>Quantity</th><th>Price</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
            f"<p>Items in cart: <span id='count'>{sum(account.cart.values())}</span></p>"
            f"<p>Total: <strong id='total'>${total}</strong></p>"
            f'<form{coupon_action}><label>Coupon <input name="code"></label> {coupon_button}</form>'
            f'<form method="post" action="/order"><input type="hidden" name="token" value="{account.token}"><button>Place order</button></form>'
        )
        self._send(200, page("Cart", body, account))

    def post_cart_remove(self, account: Account, form: dict[str, str]) -> None:
        account.cart.pop(form.get("item", ""), None)
        if not self.app.on("B4"):  # B4: the header's cart badge is not updated after a removal
            account.badge = sum(account.cart.values())
        self._redirect("/cart")

    def post_cart_coupon(self, account: Account, form: dict[str, str]) -> None:
        self._redirect("/cart?coupon=" + ("ok" if form.get("code", "").strip().upper() == "PANTRY10" else "bad"))

    def post_order(self, account: Account, form: dict[str, str]) -> None:
        if not account.cart:
            self._redirect("/cart")
            return
        if form.get("token") != account.token and not self.app.on("B5"):  # B5: a second submit places a second order
            self._redirect("/orders")
            return
        account.token = ""
        total = sum(PRODUCTS[item][1] * qty for item, qty in account.cart.items())
        account.orders.append({"id": next(_ids), "items": dict(account.cart), "total": total})
        if not self.app.on("B5"):
            account.cart.clear()
            account.badge = 0
        self._redirect("/orders")

    def get_orders(self, account: Account, query: dict[str, str]) -> None:
        rows = "".join(f"<li>Order #{order['id']}: ${order['total']}</li>" for order in account.orders) or "<li>No orders yet.</li>"
        download = '<p><a href="/receipts.csv">Download receipts (CSV)</a></p>'
        self._send(200, page("Orders", f"<p>Orders placed: <span id='orders'>{len(account.orders)}</span></p><ul>{rows}</ul>{download}", account))

    def get_receipts_csv(self, account: Account, query: dict[str, str]) -> None:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["Order ID", "Items", "Total", "Status"])
        for order in account.orders:
            items = "; ".join(f"{PRODUCTS[item][0]} x{qty}" for item, qty in order["items"].items())  # type: ignore[union-attr]
            writer.writerow([order["id"], items, f"${order['total']}", "Paid"])
        self._send(200, out.getvalue(), content_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="receipts.csv"'})

    # returns: a form with a photo upload
    def get_returns(self, account: Account, query: dict[str, str]) -> None:
        notice = f'<p role="alert">{html.escape(query["error"])}</p>' if query.get("error") else ""
        if query.get("sent"):
            notice = '<p role="status">Return requested.</p>'
        rows = (
            "".join(f"<li>Return #{ret['id']} for order #{html.escape(str(ret['order']))}: {html.escape(str(ret['photo']))} ({ret['size']} bytes)</li>" for ret in account.returns)
            or "<li>No returns yet.</li>"
        )
        body = (
            f"{notice}"
            '<form method="post" action="/returns" enctype="multipart/form-data">'
            '<p><label>Order number <input name="order"></label></p>'
            '<p><label>Photo of the item <input type="file" name="photo" accept="image/*"></label></p>'
            "<button>Request return</button></form>"
            f'<ul id="return-list">{rows}</ul>'
        )
        self._send(200, page("Returns", body, account))

    def post_returns(self, account: Account, form: dict[str, str]) -> None:
        order = form.get("order", "").strip().lstrip("#")
        photo = self.files.get("photo")
        if not order.isdigit():
            self._redirect("/returns?error=" + urllib.parse.quote("Enter the order number."))
            return
        if photo is None:
            self._redirect("/returns?error=" + urllib.parse.quote("Add a photo of the item."))
            return
        account.returns.append({"id": len(account.returns) + 1, "order": int(order), "photo": photo[0], "size": photo[1]})
        self._redirect("/returns?sent=1")

    # notes
    def get_notes(self, account: Account, query: dict[str, str]) -> None:
        rows = "".join(f"<li>{html.escape(note)}</li>" for note in account.notes) or "<li>No notes yet.</li>"
        script = ""
        if self.app.on("B6"):  # B6: a note is only added to the page; it is lost on reload
            script = (
                "document.getElementById('note-form').addEventListener('submit', (event) => { event.preventDefault();"
                " const text = document.getElementById('note-text').value; if (!text.trim()) return;"
                " const list = document.getElementById('note-list'); if (list.textContent.includes('No notes yet.')) list.innerHTML = '';"
                " const item = document.createElement('li'); item.textContent = text; list.appendChild(item);"
                " document.getElementById('note-status').textContent = 'Note saved.'; });"
            )
        body = (
            '<p role="status" id="note-status"></p>'
            '<form id="note-form" method="post" action="/notes"><label>New note <textarea id="note-text" name="text"></textarea></label> '
            f'<button>Save note</button></form><ul id="note-list">{rows}</ul>'
        )
        self._send(200, page("Notes", body, account, script=script))

    def post_notes(self, account: Account, form: dict[str, str]) -> None:
        text = form.get("text", "").strip()
        if text:
            account.notes.append(text[:500])
        self._redirect("/notes")

    # the benchmark's own endpoint: which bugs are on (never linked from the app)
    def get___bench(self, account: Account, query: dict[str, str]) -> None:
        self._send(200, json.dumps({"bugs": sorted(self.app.bugs)}), content_type="application/json")


def free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def parse_bugs(value: str) -> tuple[str, ...]:
    if value in ("", "none"):
        return ()
    if value == "all":
        return all_bugs()
    known = set(all_bugs())
    chosen = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    unknown = [item for item in chosen if item not in known]
    if unknown:
        raise ValueError(f"unknown bug(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    return chosen


@contextlib.contextmanager
def serve(bugs: tuple[str, ...], port: int = 0) -> Iterator[str]:
    """Serve the app in a background thread; yields its base URL."""
    handler = type("PantryHandler", (Handler,), {"app": App(bugs)})
    server = http.server.ThreadingHTTPServer((HOST, port or free_port()), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acme Pantry, a shop with seeded bugs for hercules explore")
    parser.add_argument("--bugs", default="all", help="all, none, or a comma-separated list such as B3,B5")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        bugs = parse_bugs(args.bugs)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    Handler.verbose = args.verbose
    with serve(bugs, args.port) as url:
        print(f"Acme Pantry on {url} (bugs: {', '.join(bugs) or 'none'})", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
