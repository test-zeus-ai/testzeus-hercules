// <coupon-box>: a custom element whose input, button and message live in an open shadow root.
(function () {
  "use strict";

  class CouponBox extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML =
        "<style>" +
        ":host{display:block;margin:16px 0;padding:12px;border:1px dashed #9aa5b1;border-radius:6px;background:#fff}" +
        "label{font-weight:600;display:block;margin-bottom:4px}" +
        "input{font:inherit;padding:6px 8px;border:1px solid #d9dee4;border-radius:6px}" +
        "button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid #1f5fbf;background:#1f5fbf;color:#fff;cursor:pointer}" +
        ".msg{margin:6px 0 0;font-size:14px}.bad{color:#b3261e}.good{color:#1b7a3a}" +
        "</style>" +
        '<label for="code">Coupon code</label>' +
        '<input id="code" type="text" placeholder="Enter coupon code" autocomplete="off"> ' +
        '<button type="button" id="apply">Apply</button>' +
        '<p class="msg" id="msg" role="status"></p>';
      const active = window.DemoShop.read("coupon", null);
      if (active) this.message("Coupon applied: 10% off", "good");
      root.getElementById("apply").addEventListener("click", () => this.apply());
      root.getElementById("code").addEventListener("keydown", (event) => {
        if (event.key === "Enter") this.apply();
      });
    }

    message(text, kind) {
      const node = this.shadowRoot.getElementById("msg");
      node.textContent = text;
      node.className = "msg " + kind;
    }

    apply() {
      const code = this.shadowRoot.getElementById("code").value.trim().toUpperCase();
      if (!code) {
        this.message("Enter a coupon code", "bad");
        return;
      }
      if (!window.DemoShop.COUPONS[code]) {
        this.message("Invalid coupon code", "bad");
        return;
      }
      window.DemoShop.write("coupon", code);
      this.message("Coupon applied: " + Math.round(window.DemoShop.COUPONS[code] * 100) + "% off", "good");
      document.dispatchEvent(new CustomEvent("coupon-changed"));
    }
  }

  customElements.define("coupon-box", CouponBox);
})();
