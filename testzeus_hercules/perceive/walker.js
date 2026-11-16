// Set-of-Mark element walker. Evaluated by Playwright in one frame at a time; it never writes to the page.
//
// Two entry points share this source:
//   frame.evaluate(src, { start, frames })  -> walk(): marks elements in this frame
//   locator.evaluate(src, { describe: 1 })  -> describe(el): role, name and path of one element
//
// Pages may patch globals and prototypes (sandboxes that wrap the DOM, polyfills, frameworks), and custom elements may
// have getters that throw. The walker therefore uses plain loops instead of Array methods (Array.from, map, filter,
// forEach, some, includes, join, push), keeps lookup tables in prototype-less objects, and guards every element and
// every shadow root, slot and frame: a node that cannot be read is skipped (and counted in `errors`), never the page.
(first, second) => {
  const MAX_NAME = 80;
  const MAX_VALUE = 80;
  const MAX_ATTR = 60;
  const MAX_ATTRS = 10;
  const MAX_SECTION = 60;
  const MAX_MESSAGE = 160;
  const MAX_MESSAGES = 12;
  const MAX_MARKS = 3000;
  const TEXT_LIMIT = 240;
  const MAX_ERRORS = 20;
  const MAX_ERROR_TEXT = 200;

  // Captured once, before any page object is touched (a getter the walk triggers cannot swap them mid-walk).
  const NativeMap = Map;
  const NativeSet = Set;
  const NativeURL = typeof URL === "function" ? URL : null;
  const cssEscape = typeof CSS !== "undefined" && CSS && typeof CSS.escape === "function" ? CSS.escape : (s) => String(s).replace(/[^A-Za-z0-9_-]/g, (c) => "\\" + c);
  const computed = typeof getComputedStyle === "function" ? getComputedStyle : null;

  const table = (names) => {
    const out = { __proto__: null };
    for (let i = 0; i < names.length; i += 1) out[names[i]] = true;
    return out;
  };
  const add = (list, item) => {
    list[list.length] = item;
  };
  const listOf = (collection) => {
    const out = [];
    const n = collection ? collection.length || 0 : 0;
    for (let i = 0; i < n; i += 1) out[out.length] = collection[i];
    return out;
  };
  const joined = (list, sep) => {
    let out = "";
    for (let i = 0; i < list.length; i += 1) out += (i ? sep : "") + list[i];
    return out;
  };

  const FIELD_ROLES = table(["textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton", "listbox"]);
  const CONTROL_ROLES = table(["button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab"]);
  const MINOR_ROLES = table(["option", "treeitem", "gridcell", "cell", "rowheader", "columnheader", "label"]);
  const NAME_FROM_CONTENT = table([
    "button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "treeitem", "gridcell",
    "cell", "rowheader", "columnheader", "heading", "label", "generic", "checkbox", "radio", "switch", "tooltip", "img",
  ]);
  const NO_DESCEND = table(["script", "style", "noscript", "template", "head", "svg", "select", "datalist", "iframe", "frame", "object", "embed", "canvas", "video", "audio"]);
  const NO_TEXT = table(["script", "style", "noscript", "template", "select", "textarea", "datalist", "option", "input"]);
  const INLINE = table(["a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "em", "font", "i", "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var"]);
  const LANDMARKS = table(["section", "nav", "aside", "form", "main", "header", "footer", "article"]);
  const ID_REF_ARIA = table(["aria-label", "aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-activedescendant", "aria-details", "aria-flowto", "aria-errormessage"]);
  const BUTTON_INPUTS = table(["button", "submit", "reset", "image", "file", "color"]);
  const FORM_CONTROLS = table(["input", "select", "textarea", "meter", "progress", "output"]);
  const CAPTION_STOPS = table(["body", "form", "html", "fieldset", "table"]);
  const NOT_SELECT_LIKE = table(["a", "button", "input", "select", "textarea", "label"]);
  const FIELD_TAGS = table(["input", "textarea", "select"]);
  const CHECKED_ROLES = table(["checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"]);
  const STOP_TAGS = table(["body", "html"]);
  // Elements that scope a heading: what follows a heading inside one of these belongs to it (ARIA landmarks and sections).
  const CONTAINER_TAGS = table(["section", "nav", "aside", "form", "main", "header", "footer", "article", "fieldset", "dialog"]);
  const CONTAINER_ROLES = table(["dialog", "alertdialog", "region", "navigation", "main", "form", "complementary", "banner", "contentinfo", "article", "tabpanel", "group", "search"]);
  const DIALOG_ROLES = table(["dialog", "alertdialog"]);
  const SCROLLING = table(["auto", "scroll", "overlay"]);
  const DEFAULT_STYLE = { display: "", visibility: "visible", opacity: "1", cursor: "auto", paddingLeft: "0", paddingTop: "0", paddingRight: "0", paddingBottom: "0" };

  // ---------- errors: one bounded record per (stage, tag, message), with a count ----------
  const errors = [];
  const errorIndex = new NativeMap();
  let skipped = 0;
  const textOfError = (e) => {
    try {
      const name = e && typeof e.name === "string" ? e.name : "Error";
      const message = e && typeof e.message === "string" ? e.message : String(e);
      return (name + ": " + message).slice(0, MAX_ERROR_TEXT);
    } catch (inner) {
      return "unreadable error";
    }
  };
  const tagOf = (node) => {
    try {
      return node && typeof node.localName === "string" ? node.localName : node && node.nodeType === 11 ? "#shadow-root" : "";
    } catch (e) {
      return "";
    }
  };
  const note = (stage, node, e) => {
    const message = textOfError(e);
    const tag = tagOf(node);
    const key = stage + "\u0000" + tag + "\u0000" + message;
    const known = errorIndex.get(key);
    if (known !== undefined) {
      known.count += 1;
      return;
    }
    if (errors.length >= MAX_ERRORS) {
      errors[errors.length - 1].count += 1;
      return;
    }
    const record = { stage, tag, message, count: 1 };
    errorIndex.set(key, record);
    add(errors, record);
  };
  // Run `read`; on an error note it and return `fallback` (the walk goes on).
  const guard = (stage, node, read, fallback) => {
    try {
      return read();
    } catch (e) {
      note(stage, node, e);
      return fallback;
    }
  };

  const clean = (s) => String(s === null || s === undefined ? "" : s).replace(/\s+/g, " ").trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const attr = (el, name) => guard("attribute", el, () => el.getAttribute(name), null);
  const hasAttr = (el, name) => guard("attribute", el, () => el.hasAttribute(name), false);
  const styleOf = (el) => (computed ? guard("style", el, () => computed(el) || DEFAULT_STYLE, DEFAULT_STYLE) : DEFAULT_STYLE);
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { left: +r.left || 0, top: +r.top || 0, right: +r.right || 0, bottom: +r.bottom || 0, width: +r.width || 0, height: +r.height || 0 };
  };
  const safeRect = (el) => guard("rect", el, () => rectOf(el), null);

  // ---------- composed tree helpers ----------
  const composedParent = (n) => {
    if (n.assignedSlot) return n.assignedSlot;
    const p = n.parentNode;
    return p && p.nodeType === 11 && p.host ? p.host : p;
  };
  const isInside = (node, ancestor) => {
    for (let n = node; n; n = composedParent(n)) if (n === ancestor) return true;
    return false;
  };
  const shadowOf = (el) => guard("shadow-root", el, () => el.shadowRoot || null, null);
  // The element's children one sibling at a time: for an element whose `children` cannot be read.
  const siblingsFrom = (el) => {
    const out = [];
    for (let child = el.firstElementChild; child; child = child.nextElementSibling) add(out, child);
    return out;
  };
  const childElements = (el) =>
    guard(
      "children",
      el,
      () => {
        const shadow = shadowOf(el);
        if (shadow) return listOf(shadow.children);
        if (el.localName === "slot") {
          const assigned = listOf(el.assignedElements({ flatten: true }));
          return assigned.length ? assigned : listOf(el.children);
        }
        try {
          return listOf(el.children);
        } catch (e) {
          note("children", el, e);
          return siblingsFrom(el);
        }
      },
      [],
    );
  const childNodes = (node) =>
    guard(
      "children",
      node,
      () => {
        const shadow = node.nodeType === 1 ? shadowOf(node) : null;
        if (shadow) return listOf(shadow.childNodes);
        if (node.localName === "slot") {
          const assigned = listOf(node.assignedNodes({ flatten: true }));
          return assigned.length ? assigned : listOf(node.childNodes);
        }
        return listOf(node.childNodes);
      },
      [],
    );
  const deepHit = (x, y) => {
    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  };

  // ---------- text and names ----------
  const textOf = (node, isRoot) => {
    if (node.nodeType === 3) return node.data;
    if (node.nodeType !== 1 && node.nodeType !== 11) return "";
    if (node.nodeType === 1) {
      const tag = node.localName;
      if (NO_TEXT[tag]) {
        if (tag === "input" && BUTTON_INPUTS[(node.type || "").toLowerCase()]) return " " + (node.value || node.alt || "") + " ";
        return "";
      }
      if (attr(node, "aria-hidden") === "true" || node.hidden) return "";
      if (tag === "img") return " " + (attr(node, "alt") || "") + " ";
      if (!isRoot && hasAttr(node, "aria-label")) return " " + attr(node, "aria-label") + " ";
      if (tag === "svg") {
        const title = node.querySelector("title");
        return title ? " " + title.textContent + " " : "";
      }
    }
    let out = "";
    const children = childNodes(node);
    for (let i = 0; i < children.length; i += 1) {
      const child = children[i];
      const part = guard("text", child, () => textOf(child, false), "");
      const sep = child.nodeType === 1 && !INLINE[child.localName] ? " " : "";
      out += sep + part + sep;
      if (out.length > TEXT_LIMIT) break;
    }
    return out;
  };
  // The element with this id in the same tree. Some pages forbid getElementById on shadow roots; an attribute
  // selector finds the same element there.
  const refById = (root, id) => {
    try {
      if (root.getElementById) return root.getElementById(id);
    } catch (e) {
      // fall through to the selector
    }
    try {
      return root.querySelector(`[id="${String(id).replace(/["\\]/g, "\\$&")}"]`);
    } catch (e) {
      return null;
    }
  };
  const byIds = (el, name) => {
    const ids = clean(attr(el, name)).split(" ");
    const root = el.getRootNode();
    const out = [];
    for (let i = 0; i < ids.length; i += 1) {
      if (!ids[i]) continue;
      const ref = refById(root, ids[i]);
      if (ref) add(out, ref);
    }
    return out;
  };
  const isFormControl = (el) => !!FORM_CONTROLS[el.localName];
  const labelsOf = (el) => (isFormControl(el) ? guard("labels", el, () => listOf(el.labels), []) : []);

  // ---------- captions of fields without a tied label ----------
  // A field whose label is not associated with it (no for=, aria-label or aria-labelledby) usually has its
  // caption just before it: in a sibling, or in a sibling of one of its near ancestors (a table cell, a
  // label wrapper). The nearest such text, stopping at the previous field, is its caption.
  const MAX_CAPTION = 40;
  const CAPTION_DEPTH = 5;
  const CAPTION_SIBLINGS = 3;
  // A previous sibling holding (or being) one of these is another control, never a caption: the search stops there.
  const CONTROL_SELECTOR = "input:not([type=hidden]), select, textarea, button, a[href], [role=button], [role=link], [role=combobox], [role=textbox], [role=listbox]";
  const captionCache = new NativeMap();
  const hasTiedLabel = (el) => !!(clean(attr(el, "aria-label")) || byIds(el, "aria-labelledby").length || labelsOf(el).length);
  const captionText = (node) => {
    if (node.nodeType === 3) return clean(node.data);
    if (node.nodeType !== 1) return "";
    if (NO_TEXT[node.localName] || node.matches(CONTROL_SELECTOR) || node.querySelector(CONTROL_SELECTOR)) return null;
    return clean(textOf(node, true));
  };
  const captionOf = (el) => {
    if (captionCache.has(el)) return captionCache.get(el);
    let found = "";
    let node = el;
    for (let depth = 0; depth < CAPTION_DEPTH && node && !found; depth += 1) {
      const parent = node.parentNode;
      if (!parent || parent.nodeType !== 1) break;
      let seen = 0;
      let blocked = false;
      for (let sib = node.previousSibling; sib && seen < CAPTION_SIBLINGS; sib = sib.previousSibling) {
        if (sib.nodeType === 1 && styleOf(sib).display === "none") continue;
        const text = captionText(sib);
        if (text === null) {
          blocked = true;  // the previous field: anything before it belongs to that field
          break;
        }
        if (!text) continue;
        seen += 1;
        if (text.length <= MAX_CAPTION) found = text;
        break;
      }
      if (blocked || CAPTION_STOPS[parent.localName]) break;
      node = parent;
    }
    found = clean(found.replace(/[\s:*]+$/, ""));
    captionCache.set(el, found);
    return found;
  };
  const captionFor = (el, role) => (FIELD_ROLES[role] && !hasTiedLabel(el) ? captionOf(el) : "");

  // ---------- drop-downs built from plain elements ----------
  const SELECT_LIKE_CLASS = /(^|[\s_-])(select|dropdown|combobox|picker)([\s_-]|$)/i;
  const selectLikeCache = new NativeMap();
  const isSelectLike = (el) => {
    if (!selectLikeCache.has(el)) selectLikeCache.set(el, selectLike(el));
    return selectLikeCache.get(el);
  };
  const selectLike = (el) => {
    if (!(el instanceof HTMLElement) || NOT_SELECT_LIKE[el.localName]) return false;
    const popup = (attr(el, "aria-haspopup") || "").toLowerCase();
    const focusable = hasAttr(el, "tabindex") && el.tabIndex >= 0;
    if (popup === "listbox" && focusable) return true;
    if (!SELECT_LIKE_CLASS.test(el.className && typeof el.className === "string" ? el.className : "")) return false;
    const parentCursor = el.parentElement ? styleOf(el.parentElement).cursor : "auto";
    if (!focusable && (styleOf(el).cursor !== "pointer" || parentCursor === "pointer")) return false;
    // The parts inside a drop-down (its text, its arrow) are not further drop-downs: only the outermost is the control.
    for (let up = el.parentElement, depth = 0; up && depth < 3; up = up.parentElement, depth += 1) {
      if (isSelectLike(up)) return false;
    }
    return !el.querySelector("a[href], button, input, select, textarea, [role=option]");
  };

  const nameOf = (el, role) => {
    const tag = el.localName;
    const labelledBy = byIds(el, "aria-labelledby");
    if (labelledBy.length) {
      const parts = [];
      for (let i = 0; i < labelledBy.length; i += 1) add(parts, attr(labelledBy[i], "aria-label") || textOf(labelledBy[i], true));
      const text = clean(joined(parts, " "));
      if (text) return text;
    }
    const aria = clean(attr(el, "aria-label"));
    if (aria) return aria;
    const labels = labelsOf(el);
    if (labels.length) {
      const parts = [];
      for (let i = 0; i < labels.length; i += 1) add(parts, textOf(labels[i], true));
      const text = clean(joined(parts, " "));
      if (text) return text;
    }
    if (tag === "input") {
      const type = (el.type || "").toLowerCase();
      if (type === "submit" || type === "reset" || type === "button") {
        const v = clean(el.value) || (type === "submit" ? "Submit" : type === "reset" ? "Reset" : "");
        if (v) return v;
      }
      if (type === "image" && el.alt) return clean(el.alt);
    }
    if (NAME_FROM_CONTENT[role] && !isFormControl(el)) {
      const text = clean(textOf(el, true));
      if (text) return text;
    }
    const fallbacks = ["title", "placeholder", "aria-placeholder", "alt"];
    for (let i = 0; i < fallbacks.length; i += 1) {
      const v = clean(attr(el, fallbacks[i]));
      if (v) return v;
    }
    return captionFor(el, role);
  };

  // ---------- roles and tiers ----------
  const editableRoot = (el) => !!el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable);
  const roleOf = (el) => {
    const explicit = clean(attr(el, "role")).split(" ")[0];
    if (explicit && explicit !== "presentation" && explicit !== "none") return explicit;
    switch (el.localName) {
      case "a":
      case "area":
        return hasAttr(el, "href") ? "link" : "generic";
      case "button":
      case "summary":
        return "button";
      case "select":
        return el.multiple || el.size > 1 ? "listbox" : "combobox";
      case "textarea":
        return "textbox";
      case "label":
        return "label";
      case "option":
        return "option";
      case "img":
        return "img";
      case "h1": case "h2": case "h3": case "h4": case "h5": case "h6":
        return "heading";
      case "input": {
        const type = (el.type || "text").toLowerCase();
        if (type === "checkbox" || type === "radio") return type;
        if (type === "range") return "slider";
        if (type === "number") return "spinbutton";
        if (type === "search") return "searchbox";
        if (BUTTON_INPUTS[type]) return "button";
        return "textbox";
      }
      default:
        if (editableRoot(el)) return "textbox";
        return isSelectLike(el) ? "combobox" : "generic";
    }
  };
  const rendered = (el) => {
    if (!el.isConnected) return false;
    const r = safeRect(el);
    if (!r || r.width * r.height < 4) return false;
    const st = styleOf(el);
    return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
  };
  const tierOf = (el, role, style, parentCursor, insideMarked) => {
    const tag = el.localName;
    if (tag === "html" || tag === "body") return -1;
    if (tag === "input" && (el.type || "").toLowerCase() === "hidden") return -1;
    if (FIELD_ROLES[role]) return 0;
    if (CONTROL_ROLES[role]) return 1;
    if (tag === "label") return el.control && !rendered(el.control) ? 2 : -1;
    if (MINOR_ROLES[role]) return 2;
    if (hasAttr(el, "tabindex") && el.tabIndex >= 0) return 3;
    if (!insideMarked && style.cursor === "pointer" && parentCursor !== "pointer") return 3;
    return -1;
  };

  // ---------- state ----------
  const valueOf = (el, role) => {
    const tag = el.localName;
    if (tag === "input") {
      const type = (el.type || "text").toLowerCase();
      if (type === "checkbox" || type === "radio" || BUTTON_INPUTS[type]) {
        if (type !== "file") return null;
        const files = listOf(el.files);
        const names = [];
        for (let i = 0; i < files.length; i += 1) add(names, files[i].name);
        return clean(joined(names, ", "));
      }
      if (type === "password") return el.value ? "***" : "";
      return el.value;
    }
    if (tag === "textarea") return el.value;
    if (tag === "select") {
      const chosen = listOf(el.selectedOptions);
      const labels = [];
      for (let i = 0; i < chosen.length; i += 1) add(labels, chosen[i].label || chosen[i].text);
      return clean(joined(labels, ", "));
    }
    if (editableRoot(el)) return clean(textOf(el, true));
    if (role === "combobox" && !hasAttr(el, "role") && !isFormControl(el)) return clean(textOf(el, true));
    if (FIELD_ROLES[role] && role !== "checkbox" && role !== "radio" && role !== "switch") {
      const v = attr(el, "aria-valuetext") || attr(el, "aria-valuenow");
      return v === null ? null : v;
    }
    return null;
  };
  const checkedOf = (el, role) => {
    if (el.localName === "input" && (el.type === "checkbox" || el.type === "radio")) return !!el.checked;
    if (CHECKED_ROLES[role]) {
      const v = attr(el, "aria-checked");
      return v === "true" ? true : v === "false" ? false : null;
    }
    return null;
  };
  const formFieldNames = (el) => {
    const fields = listOf(el.form.elements);
    const names = [];
    for (let i = 0; i < fields.length && names.length < 3; i += 1) {
      const f = fields[i];
      const type = (f.type || "").toLowerCase();
      if (f === el || !FIELD_TAGS[f.localName] || BUTTON_INPUTS[type] || type === "hidden") continue;
      const name = guard("form", f, () => {
        const fieldRole = roleOf(f);
        return clean(captionFor(f, fieldRole) || nameOf(f, fieldRole) || attr(f, "placeholder") || attr(f, "aria-label") || attr(f, "name") || "");
      }, "");
      if (name) add(names, name);
    }
    return names;
  };
  // A form field's own facts, for deciders and step continuations: whether it is marked invalid (aria-invalid, or the
  // browser's check once a user changed it, unless its form opts out with novalidate), the error text tied to it
  // (aria-errormessage; aria-describedby when invalid; else, when invalid, the text of its own container: the highest
  // ancestor holding no other field), its help text (aria-describedby) and its format attributes.
  const FACT_ATTRS = ["pattern", "title", "maxlength", "minlength", "min", "max", "inputmode"];
  const FIELD_QUERY = "input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image]),select,textarea,[role=textbox],[role=combobox],[role=searchbox],[role=spinbutton],[role=listbox],[role=slider]";
  const MAX_FACT = 120;
  const refsText = (el, name) => {
    const refs = byIds(el, name);
    let out = "";
    for (let i = 0; i < refs.length; i += 1) {
      const r = safeRect(refs[i]);
      if (!r || r.width * r.height <= 0) continue;
      out += " " + textOf(refs[i], true);
    }
    return clip(clean(out), MAX_FACT);
  };
  const othersIn = (node, el) => {
    const found = listOf(node.querySelectorAll(FIELD_QUERY));
    for (let i = 0; i < found.length; i += 1) if (found[i] !== el && !isInside(el, found[i])) return true;
    const shadow = shadowOf(node);
    if (!shadow) return false;
    const inner = listOf(shadow.querySelectorAll(FIELD_QUERY));
    for (let i = 0; i < inner.length; i += 1) if (inner[i] !== el && !isInside(el, inner[i])) return true;
    return false;
  };
  const containerText = (el, name, caption) => {
    let container = null;
    for (let n = composedParent(el), depth = 0; n && n.nodeType === 1 && depth < 6; n = composedParent(n), depth += 1) {
      if (STOP_TAGS[n.localName] || n.localName === "form" || n.localName === "fieldset" || othersIn(n, el)) break;
      container = n;
    }
    if (!container) return "";
    let text = clean(textOf(container, true));
    const known = [name, caption];
    for (let i = 0; i < known.length; i += 1) if (known[i]) text = clean(joined(text.split(known[i]), " "));
    return (text.match(/\p{L}/gu) || []).length >= 3 ? clip(text, MAX_FACT) : "";
  };
  const fieldFacts = (el, role, name, caption) => {
    const facts = {};
    const form = el.form || null;
    const native = !!(el.validity && el.willValidate && !(form && form.noValidate));
    const userInvalid = native && guard("validity", el, () => el.matches(":user-invalid"), false);
    const invalid = attr(el, "aria-invalid") === "true" || userInvalid;
    if (invalid) facts.invalid = "true";
    const errormessage = refsText(el, "aria-errormessage");
    const describedby = refsText(el, "aria-describedby");
    let error = errormessage || (invalid ? describedby : "");
    if (!error && invalid) error = guard("field-error", el, () => containerText(el, name, caption), "");
    if (!error && userInvalid) error = clip(clean(el.validationMessage), MAX_FACT);
    if (error) facts.error = error;
    if (describedby && describedby !== error) facts.hint = describedby;
    for (let i = 0; i < FACT_ATTRS.length; i += 1) {
      const v = clean(attr(el, FACT_ATTRS[i]));
      if (v && !(FACT_ATTRS[i] === "title" && v === name)) facts[FACT_ATTRS[i]] = clip(v, MAX_FACT);
    }
    return facts;
  };

  const attrsOf = (el, role, name) => {
    const out = {};
    let count = 0;
    const put = (k, v) => {
      if (count >= MAX_ATTRS || v === null || v === undefined || v === "") return;
      out[k] = clip(clean(String(v)), MAX_ATTR);
      count += 1;
    };
    if (el.localName === "input") put("type", (el.type || "text").toLowerCase());
    else if (el.localName === "button" && el.form && (el.type || "").toLowerCase() === "submit") put("type", "submit");
    // A button inside a form names the form's first fields, so it can be told apart from a same-named tab or link outside it.
    if (role === "button" && el.form) {
      const names = guard("form", el, () => formFieldNames(el), []);
      if (names.length) put("form", joined(names, ", "));
    }
    const caption = captionFor(el, role);
    if (caption && caption !== name) put("label", caption);
    put("placeholder", attr(el, "placeholder"));
    if (hasAttr(el, "href") && el.href && NativeURL) {
      try {
        const u = new NativeURL(el.href, document.baseURI);
        if (u.protocol !== "javascript:") put("href", u.pathname);
      } catch (e) {
        /* unparsable href: leave it out */
      }
    }
    put("name", attr(el, "name"));
    put("id", el.id);
    if (el.required) put("aria-required", "true");
    const attributes = listOf(el.attributes);
    for (let i = 0; i < attributes.length; i += 1) {
      const a = attributes[i];
      if (a.name.slice(0, 5) === "aria-" && !ID_REF_ARIA[a.name] && !(a.name in out)) put(a.name, a.value);
    }
    if (FIELD_ROLES[role] || el.isContentEditable) {
      // Field facts have their own budget: a field's state matters more than its other attributes.
      const facts = guard("field", el, () => fieldFacts(el, role, name, caption), {});
      for (const key in facts) if (!(key in out)) out[key] = facts[key];
    }
    return out;
  };

  // ---------- resolution paths ----------
  const uniqueIdIn = (el, root) => {
    if (!el.id) return false;
    try {
      return root.querySelectorAll("#" + cssEscape(el.id)).length === 1;
    } catch (e) {
      return false;
    }
  };
  const step = (el) => {
    const tag = cssEscape(el.localName);
    let index = 1;
    for (let s = el.previousElementSibling; s; s = s.previousElementSibling) if (s.localName === el.localName) index += 1;
    let alone = index === 1;
    for (let s = el.nextElementSibling; alone && s; s = s.nextElementSibling) if (s.localName === el.localName) alone = false;
    return alone ? tag : `${tag}:nth-of-type(${index})`;
  };
  const segmentOf = (el) => {
    const root = el.getRootNode();
    const steps = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
      if (uniqueIdIn(n, root)) {
        steps.unshift("#" + cssEscape(n.id));
        return joined(steps, " > ");
      }
      steps.unshift(step(n));
    }
    const path = joined(steps, " > ");
    return root.nodeType === 11 ? ":scope > " + path : path;
  };
  const pathOf = (el) => {
    const segments = [];
    for (let n = el; n; ) {
      segments.unshift(segmentOf(n));
      const root = n.getRootNode();
      n = root.nodeType === 11 && root.host ? root.host : null;
    }
    return segments;
  };

  // ---------- geometry ----------
  const boxOf = (r) => [r.left, r.top, r.width, r.height];
  const visibleBox = (el, r, vw, vh) => {
    const x0 = Math.max(r.left, 0);
    const y0 = Math.max(r.top, 0);
    const x1 = Math.min(r.right, vw);
    const y1 = Math.min(r.bottom, vh);
    if (x1 - x0 < 1 || y1 - y0 < 1) return null;
    const cw = (x1 - x0) / 3;
    const ch = (y1 - y0) / 3;
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    try {
      for (let i = 0; i < 3; i += 1) {
        for (let j = 0; j < 3; j += 1) {
          const hit = deepHit(x0 + cw * (i + 0.5), y0 + ch * (j + 0.5));
          if (hit && (hit === el || isInside(hit, el))) {
            bx0 = Math.min(bx0, x0 + cw * i);
            by0 = Math.min(by0, y0 + ch * j);
            bx1 = Math.max(bx1, x0 + cw * (i + 1));
            by1 = Math.max(by1, y0 + ch * (j + 1));
          }
        }
      }
    } catch (e) {
      note("hit-test", el, e);
      return [x0, y0, x1 - x0, y1 - y0];  // cannot tell what covers it: take the part inside the viewport
    }
    return bx1 > bx0 ? [bx0, by0, bx1 - bx0, by1 - by0] : null;
  };

  const MAX_NEAR = 60;
  const NEAR_DEPTH = 6;
  const ownName = (name) => new RegExp("(^|\\s)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?=\\s|$)");
  const surroundingText = (el, name) => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < NEAR_DEPTH; depth += 1, node = node.parentElement) {
      if (STOP_TAGS[node.localName]) break;
      const text = clean(textOf(node, true).replace(ownName(name), " "));
      if ((text.match(/\p{L}/gu) || []).length >= 3) return clip(text, MAX_NEAR);  // a price or a count alone does not tell rows apart
    }
    return "";
  };

  const describe = (el) => {
    const role = guard("role", el, () => roleOf(el), "generic");
    const name = guard("name", el, () => clip(clean(nameOf(el, role)), MAX_NAME), "");
    return { role, name, path: pathOf(el) };
  };

  // ---------- dialogs, sections and scroll areas (the element's own ancestry, never its position on screen) ----------
  const isModalDialog = (el, role) => {
    if (el.localName === "dialog") {
      if (guard("modal", el, () => el.matches(":modal"), false)) return true;
    }
    return !!DIALOG_ROLES[role] && attr(el, "aria-modal") === "true";
  };
  const isDialog = (el, role) => !!DIALOG_ROLES[role] || (el.localName === "dialog" && hasAttr(el, "open"));
  const isContainer = (el, role) => !!CONTAINER_TAGS[el.localName] || !!CONTAINER_ROLES[role];
  const isScroller = (el, style) => {
    if (STOP_TAGS[el.localName]) return false;
    if (!SCROLLING[style.overflowY]) return false;
    return el.scrollHeight > el.clientHeight + 1;
  };
  // The nearest heading before the element inside its own section, landmark or dialog (a dialog is never left).
  const sectionOf = (container) => {
    for (let c = container; c; c = c.parent) {
      if (c.heading) return c.heading;
      if (c.boundary) return "";
    }
    return "";
  };
  // Where an element lies in its scroll area: "above" or "below" the part shown, or "" when (partly) shown.
  const scrolledOut = (r, scroller) => {
    if (!scroller || !scroller.rect) return "";
    const s = scroller.rect;
    if (r.bottom <= s.top + 1) return "above";
    if (r.top >= s.bottom - 1) return "below";
    return "";
  };
  // The z-index that decides which of two overlapping layers is on top (the highest among positioned ancestors).
  const layerOf = (el) => {
    let z = 0;
    for (let n = el; n && n.nodeType === 1; n = composedParent(n)) {
      const st = styleOf(n);
      const value = parseInt(st.zIndex, 10);
      if (st.position && st.position !== "static" && value === value && value > z) z = value;
    }
    return z;
  };
  // A node with an inert or aria-hidden="true" element on its way to the document: not offered to a user or a test.
  const hiddenAncestor = (node) => {
    for (let n = node; n; n = composedParent(n)) {
      if (n.nodeType !== 1) continue;
      if (hasAttr(n, "inert")) return "inert";
      if (attr(n, "aria-hidden") === "true") return "aria-hidden";
    }
    return "";
  };

  const walk = (params) => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let md = params.start || 1;
    const elements = [];
    const nodes = [];
    const sections = [];
    const messages = [];
    const seen = new NativeSet();
    // What the page announces: alerts and live regions, and the error text an invalid field points to (ARIA only).
    const noteMessage = (text) => {
      const t = clip(clean(text), MAX_MESSAGE);
      if (!t || messages.length >= MAX_MESSAGES) return;
      for (let i = 0; i < messages.length; i += 1) if (messages[i] === t) return;
      add(messages, t);
    };
    const noteMessages = (el, role) => {
      const live = attr(el, "aria-live");
      if (role === "alert" || role === "status" || role === "alertdialog" || live === "assertive" || live === "polite") {
        const r = safeRect(el);
        if (r && r.width > 0 && r.height > 0) noteMessage(textOf(el, true));
      }
      if (attr(el, "aria-invalid") === "true") {
        const ids = clean(`${attr(el, "aria-errormessage") || ""} ${attr(el, "aria-describedby") || ""}`).split(" ");
        const root = el.getRootNode();
        for (let i = 0; i < ids.length; i += 1) {
          if (!ids[i]) continue;
          const ref = refById(root, ids[i]) || refById(document, ids[i]);
          if (ref) noteMessage(textOf(ref, true));
        }
      }
    };

    // Dialogs met on the walk (modal or not), and the context of each marked element (same index as `elements`).
    const dialogs = [];
    const contexts = [];
    const contextOf = (r, ctx) => ({
      dialog: ctx.dialog,
      section: clip(sectionOf(ctx.container), MAX_SECTION),
      scroll: scrolledOut(r, ctx.scroller),
      scrollInDialog: !!(ctx.scroller && ctx.scroller.inDialog),
      hidden: ctx.hidden,
    });

    // Mark one element when it is a control; returns whether it was marked. Throws when the element cannot be read.
    const mark = (el, tag, role, style, parentCursor, insideMarked, ctx) => {
      const tier = elements.length < MAX_MARKS ? tierOf(el, role, style, parentCursor, insideMarked) : -1;
      if (tier < 0 || style.visibility === "hidden") return false;
      const r = rectOf(el);
      if (!(r.width > 0 && r.height > 0)) return false;
      const name = clip(clean(nameOf(el, role)), MAX_NAME);
      const value = valueOf(el, role);
      const record = {
        md,
        role,
        name,
        tag,
        attrs: attrsOf(el, role, name),
        rect: boxOf(r),
        visible: visibleBox(el, r, vw, vh),
        tier,
        value: value === null || value === undefined ? null : clip(String(value), MAX_VALUE),
        checked: checkedOf(el, role),
        disabled: !!(el.matches(":disabled") || attr(el, "aria-disabled") === "true"),
        path: pathOf(el),
      };
      add(elements, record);
      add(nodes, el);
      add(contexts, guard("context", el, () => contextOf(r, ctx), { dialog: ctx.dialog, section: "", scroll: "", scrollInDialog: false, hidden: ctx.hidden }));
      md += 1;
      return true;
    };

    // The context the element's children see: its dialog, the section a heading names, its scroll area, inert or aria-hidden.
    const enter = (el, tag, role, style, ctx) => {
      let next = ctx;
      const copy = () => {
        if (next === ctx) next = { container: ctx.container, dialog: ctx.dialog, scroller: ctx.scroller, hidden: ctx.hidden };
        return next;
      };
      if (!ctx.hidden) {
        const hidden = hasAttr(el, "inert") ? "inert" : attr(el, "aria-hidden") === "true" ? "aria-hidden" : "";
        if (hidden) copy().hidden = hidden;
      }
      if (isDialog(el, role)) {
        const r = safeRect(el);
        const record = {
          el,
          name: guard("name", el, () => clip(clean(nameOf(el, "dialog")), MAX_NAME), ""),
          heading: "",
          modal: isModalDialog(el, role),
          topLayer: el.localName === "dialog" && guard("modal", el, () => el.matches(":modal"), false),
          shown: !!(r && r.width * r.height >= 4 && style.visibility !== "hidden" && style.opacity !== "0"),
          rect: r,
        };
        add(dialogs, record);
        copy().dialog = dialogs.length - 1;
        next.container = { heading: "", parent: ctx.container, boundary: true };
      } else if (isContainer(el, role)) {
        copy().container = { heading: "", parent: ctx.container, boundary: false };
      }
      if (isScroller(el, style)) {
        copy().scroller = { rect: safeRect(el), inDialog: next.dialog !== null };
        // Headings inside a scroll area name its sections, not what follows it (a dialog's buttons below its form).
        if (next.container === ctx.container) next.container = { heading: "", parent: ctx.container, boundary: false };
      }
      return next;
    };
    const noteHeading = (el, tag, role, ctx) => {
      if (role !== "heading" && tag !== "legend") return;
      const name = clip(clean(textOf(el, true)), MAX_SECTION);
      if (!name) return;
      if (ctx.container) ctx.container.heading = name;
      if (ctx.dialog !== null && !dialogs[ctx.dialog].heading) dialogs[ctx.dialog].heading = name;
    };

    const visit = (el, parentCursor, insideMarked, ctx) => {
      if (seen.has(el)) return;
      seen.add(el);
      const tag = tagOf(el);
      const style = styleOf(el);
      if (style.display === "none") return;
      const role = guard("role", el, () => roleOf(el), "generic");
      let marked = false;
      try {
        marked = mark(el, tag, role, style, parentCursor, insideMarked, ctx);
      } catch (e) {
        note("element", el, e);
        skipped += 1;
      }
      if (style.visibility !== "hidden") guard("messages", el, () => noteMessages(el, role), null);
      guard("sections", el, () => {
        if (role === "heading" || (LANDMARKS[tag] && hasAttr(el, "aria-label")) || (role === "region" && hasAttr(el, "aria-label"))) {
          const name = role === "heading" ? clean(textOf(el, true)) : clean(attr(el, "aria-label"));
          const r = safeRect(el);
          if (name && r && r.height > 0) add(sections, [clip(name, MAX_SECTION), r.top, r.bottom]);
        }
      }, null);
      guard("sections", el, () => noteHeading(el, tag, role, ctx), null);
      if (NO_DESCEND[tag]) return;
      const inner = guard("context", el, () => enter(el, tag, role, style, ctx), ctx);
      const children = childElements(el);
      for (let i = 0; i < children.length; i += 1) visit(children[i], style.cursor, insideMarked || marked, inner);
    };
    const top = { container: { heading: "", parent: null, boundary: false }, dialog: null, scroller: null, hidden: "" };
    if (document.documentElement) visit(document.documentElement, "auto", false, top);

    // The open modal dialog on top (a <dialog> shown as modal is in the top layer; else the one a hit test finds on top,
    // else the highest layer, else the last in the page): everything outside it is behind it and is not offered.
    let modal = null;
    guard("modal", null, () => {
      const shown = [];
      for (let i = 0; i < dialogs.length; i += 1) if (dialogs[i].modal && dialogs[i].shown && dialogs[i].el.isConnected) add(shown, dialogs[i]);
      if (!shown.length) return;
      const topLayer = [];
      for (let i = 0; i < shown.length; i += 1) if (shown[i].topLayer) add(topLayer, shown[i]);
      const pool = topLayer.length ? topLayer : shown;
      let onTop = null;
      for (let i = 0; i < pool.length; i += 1) {
        const r = pool[i].rect;
        if (!r) continue;
        const x = Math.min(Math.max((r.left + r.right) / 2, 0), vw - 1);
        const y = Math.min(Math.max((r.top + r.bottom) / 2, 0), vh - 1);
        const hit = guard("hit-test", pool[i].el, () => deepHit(x, y), null);
        if (hit && (hit === pool[i].el || isInside(hit, pool[i].el))) onTop = pool[i];
      }
      if (!onTop) {
        let best = -1;
        for (let i = 0; i < pool.length; i += 1) {
          const z = guard("layer", pool[i].el, () => layerOf(pool[i].el), 0);
          if (z >= best) {
            best = z;
            onTop = pool[i];
          }
        }
      }
      modal = onTop;
    }, null);

    const blockedOf = (node, hidden) => {
      if (modal && !guard("modal", node, () => isInside(node, modal.el), true)) return "behind_modal";
      return hidden || "";
    };
    for (let i = 0; i < elements.length; i += 1) {
      const c = contexts[i];
      const e = elements[i];
      const d = c.dialog !== null && c.dialog !== undefined ? dialogs[c.dialog] : null;
      e.dialog = d ? d.name || d.heading || "" : null;
      e.section = c.section && !(d && (c.section === d.name || c.section === d.heading)) ? c.section : "";
      e.scroll = c.scroll || "";
      e.scroll_in_dialog = !!c.scrollInDialog;
      e.blocked = blockedOf(nodes[i], c.hidden);
      if (d && d.path === undefined) d.path = guard("path", d.el, () => pathOf(d.el), null);
      e.dialog_path = d ? d.path : null;
    }

    // Same-named elements (a "Remove" button per cart row) are told apart by the text around them.
    const counts = new NativeMap();
    for (let i = 0; i < elements.length; i += 1) {
      const e = elements[i];
      if (!e.name) continue;
      const key = e.role + "\u0000" + e.name;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    for (let i = 0; i < elements.length; i += 1) {
      const e = elements[i];
      if (!e.name || counts.get(e.role + "\u0000" + e.name) < 2) continue;
      const near = guard("near", nodes[i], () => surroundingText(nodes[i], e.name), "");
      if (near) e.attrs.near = near;
    }

    const handles = params.frames || [];
    const frames = [];
    for (let i = 0; i < handles.length; i += 1) {
      const f = handles[i];
      add(
        frames,
        guard("frame", f, () => {
          if (!f || !f.isConnected) return null;
          const r = rectOf(f);
          const st = styleOf(f);
          const left = r.left + f.clientLeft + parseFloat(st.paddingLeft || "0");
          const top = r.top + f.clientTop + parseFloat(st.paddingTop || "0");
          const width = f.clientWidth - parseFloat(st.paddingLeft || "0") - parseFloat(st.paddingRight || "0");
          const height = f.clientHeight - parseFloat(st.paddingTop || "0") - parseFloat(st.paddingBottom || "0");
          const label = clean(attr(f, "name") || attr(f, "title") || f.id || attr(f, "aria-label")) || null;
          return { content: [left, top, width, height], visible: visibleBox(f, r, vw, vh), label, path: pathOf(f), blocked: blockedOf(f, hiddenAncestor(f)) };
        }, null),
      );
    }
    return { viewport: [vw, vh], next: md, elements, sections, messages, frames, errors, skipped, modal: modal ? { name: modal.name || modal.heading || "" } : null };
  };

  if (second !== undefined && first && first.nodeType === 1) return describe(first);
  return walk(first || {});
}
