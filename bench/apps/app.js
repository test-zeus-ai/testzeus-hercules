// Demo Shop shared code: catalogue, localStorage-backed state, site header and toasts.
(function () {
  "use strict";

  const PRODUCTS = [
    { id: 1, name: "Classic Tee", category: "Apparel", price: 19.0, inStock: true, rank: 1, description: "A soft cotton t-shirt with a relaxed fit." },
    { id: 2, name: "Denim Jacket", category: "Apparel", price: 79.0, inStock: false, rank: 4, description: "A washed denim jacket with brass buttons." },
    { id: 3, name: "Running Shoes", category: "Footwear", price: 95.0, inStock: true, rank: 2, description: "Lightweight running shoes with a cushioned sole." },
    { id: 4, name: "Canvas Sneakers", category: "Footwear", price: 49.0, inStock: true, rank: 6, description: "Everyday canvas sneakers in off-white." },
    { id: 5, name: "Leather Wallet", category: "Accessories", price: 35.0, inStock: true, rank: 3, description: "A slim bifold wallet in full-grain leather." },
    { id: 6, name: "Wool Beanie", category: "Accessories", price: 15.0, inStock: false, rank: 7, description: "A ribbed merino wool beanie." },
    { id: 7, name: "Water Bottle", category: "Accessories", price: 12.5, inStock: true, rank: 5, description: "An insulated steel bottle that keeps drinks cold for 24 hours." },
    { id: 8, name: "Hiking Backpack", category: "Accessories", price: 120.0, inStock: true, rank: 8, description: "A 30 litre backpack with a rain cover." },
  ];

  const KEY = "demoShop.";
  const FREE_SHIPPING_FROM = 50;
  const STANDARD_SHIPPING = 5;
  const EXPRESS_SHIPPING = 15;
  const COUPONS = { SAVE10: 0.1 };

  function read(name, fallback) {
    try {
      const raw = localStorage.getItem(KEY + name);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  }

  function write(name, value) {
    localStorage.setItem(KEY + name, JSON.stringify(value));
  }

  function money(amount) {
    return "$" + amount.toFixed(2);
  }

  function product(id) {
    return PRODUCTS.find((item) => item.id === Number(id)) || null;
  }

  // ------------------------------------------------------------------ cart

  function cart() {
    return read("cart", []);
  }

  function saveCart(lines) {
    write("cart", lines.filter((line) => line.qty > 0));
    renderHeader();
  }

  function addToCart(id, qty) {
    const lines = cart();
    const existing = lines.find((line) => line.id === id);
    const updated = existing
      ? lines.map((line) => (line.id === id ? { id: line.id, qty: line.qty + qty } : line))
      : lines.concat([{ id: id, qty: qty }]);
    saveCart(updated);
  }

  function cartCount() {
    return cart().reduce((sum, line) => sum + line.qty, 0);
  }

  function totals(shippingMethod) {
    const lines = cart();
    const subtotal = lines.reduce((sum, line) => sum + product(line.id).price * line.qty, 0);
    const coupon = read("coupon", null);
    const rate = coupon && COUPONS[coupon] ? COUPONS[coupon] : 0;
    const discount = Math.round(subtotal * rate * 100) / 100;
    let shipping = subtotal - discount >= FREE_SHIPPING_FROM || subtotal === 0 ? 0 : STANDARD_SHIPPING;
    if (shippingMethod === "express") shipping = EXPRESS_SHIPPING;
    return { subtotal: subtotal, discount: discount, shipping: shipping, total: subtotal - discount + shipping, coupon: rate ? coupon : null };
  }

  // ------------------------------------------------------------------ header

  const CART_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.5 12h12l2-8H6.2"/></svg>';

  function renderHeader() {
    const host = document.getElementById("site-header");
    if (!host) return;
    const user = read("user", null);
    const count = cartCount();
    host.className = "site-header";
    host.innerHTML =
      '<a class="brand" href="index.html">Demo Shop</a>' +
      '<nav aria-label="Main">' +
      '<a href="products.html">Products</a>' +
      '<a href="orders.html">Orders</a>' +
      '<a href="spa.html">Store info</a>' +
      '<a href="faq.html">Help</a>' +
      '<a href="account.html">Account</a>' +
      (user ? '<a href="#" id="sign-out">Sign out</a>' : '<a href="login.html">Sign in</a>') +
      "</nav>" +
      (user ? '<span class="session">Signed in as ' + escapeHtml(user.email) + "</span>" : "") +
      '<span class="session" id="cart-count">Cart (' + count + ")</span>" +
      '<button type="button" class="icon-button" id="open-cart" aria-label="Open cart">' + CART_ICON +
      (count ? '<span class="badge" aria-hidden="true">' + count + "</span>" : "") +
      "</button>";
    host.querySelector("#open-cart").addEventListener("click", () => {
      location.href = "cart.html";
    });
    const signOut = host.querySelector("#sign-out");
    if (signOut) {
      signOut.addEventListener("click", (event) => {
        event.preventDefault();
        localStorage.removeItem(KEY + "user");
        location.href = "login.html";
      });
    }
  }

  // ------------------------------------------------------------------ toast

  let toastTimer = null;

  function toast(message) {
    let node = document.getElementById("toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "toast";
      node.className = "toast";
      node.setAttribute("role", "status");
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.hidden = true;
      node.textContent = "";
    }, 3000);
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  }

  function param(name) {
    return new URLSearchParams(location.search).get(name);
  }

  window.DemoShop = {
    PRODUCTS: PRODUCTS,
    COUPONS: COUPONS,
    read: read,
    write: write,
    money: money,
    product: product,
    cart: cart,
    saveCart: saveCart,
    addToCart: addToCart,
    totals: totals,
    toast: toast,
    escapeHtml: escapeHtml,
    param: param,
    renderHeader: renderHeader,
  };

  document.addEventListener("DOMContentLoaded", renderHeader);
})();
