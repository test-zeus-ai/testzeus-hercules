// Quality probes: measurements of one frame for the accessibility, layout and visual checks.
// Evaluated by Playwright with the perception walker passed in as `walker` (see frames.py):
//   frame.evaluate(src, { op, ... })    -> scan the whole frame (document and open shadow roots)
//   locator.evaluate(src, { op, ... })  -> measure, mark or change one element
// Scans only read the page. The "mark", "unmark" and "attr" ops change it and are always undone by the caller.
(first, second, walker) => {
  const single = second !== undefined && first && first.nodeType === 1;
  const target = single ? first : null;
  const args = (single ? second : first) || {};

  const MAX_TEXT = 80;
  const MAX_TOKENS = 12;
  const INTERACTIVE = "a[href], area[href], button, input:not([type=hidden]), select, textarea, summary, [role], [tabindex], [contenteditable=''], [contenteditable=true], [onclick]";
  const NAMED_ROLES = new Set(["button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton", "listbox", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "treeitem"]);
  const FIELD_ROLES = new Set(["textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton", "listbox"]);
  const COMPOSITE_ROLES = new Set(["tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "treeitem", "gridcell", "row"]);
  const FOCUS_PROPS = ["outline-style", "outline-width", "outline-color", "outline-offset", "box-shadow", "border-top-color", "border-right-color", "border-bottom-color", "border-left-color", "border-top-width", "border-bottom-width", "background-color", "background-image", "color", "text-decoration-line", "text-decoration-color", "transform", "filter", "opacity"];
  const PARENT_FOCUS_PROPS = ["outline-style", "outline-width", "box-shadow", "border-top-color", "border-bottom-color", "background-color"];
  const STYLE_PROPS = ["display", "position", "top", "right", "bottom", "left", "z-index", "float", "box-sizing", "width", "height", "margin-top", "margin-right", "margin-bottom", "margin-left", "padding-top", "padding-right", "padding-bottom", "padding-left", "border-top-width", "border-right-width", "border-bottom-width", "border-left-width", "font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing", "text-align", "text-transform", "color", "background-color", "background-image", "opacity", "visibility", "overflow-x", "overflow-y", "flex-direction", "flex-wrap", "justify-content", "align-items", "gap", "grid-template-columns", "white-space", "transform"];

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const describe = (el) => walker(el, 1);

  // ---------- tree, geometry, identity ----------
  const allElements = () => {
    const out = [];
    const visit = (root) => {
      for (const el of root.querySelectorAll("*")) {
        out.push(el);
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    visit(document);
    return out;
  };
  const composedParent = (n) => {
    if (n.assignedSlot) return n.assignedSlot;
    const p = n.parentNode;
    return p && p.nodeType === 11 && p.host ? p.host : p;
  };
  const parentElementOf = (el) => {
    const p = composedParent(el);
    return p && p.nodeType === 1 ? p : null;
  };
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height];
  };
  const rendered = (el) => {
    if (!el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < 1) return false;
    const st = getComputedStyle(el);
    if (st.visibility !== "visible" || st.display === "none") return false;
    for (let n = el; n; n = parentElementOf(n)) if (getComputedStyle(n).opacity === "0") return false;
    return true;
  };
  const visuallyHidden = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return true;
    const st = getComputedStyle(el);
    return st.clip === "rect(0px, 0px, 0px, 0px)" || st.clipPath === "inset(50%)" || r.right < -500 || r.bottom < -500;
  };
  const disabled = (el) => el.matches(":disabled") || !!el.closest("[aria-disabled=true]") || !!el.closest("[inert]");
  const tokensOf = (el) => {
    const out = [];
    const add = (t) => {
      t = clean(t);
      if (t && t.length <= 60 && !out.includes(t) && out.length < MAX_TOKENS) out.push(t);
    };
    let n = el;
    for (let depth = 0; n && depth < 4; depth += 1, n = parentElementOf(n)) {
      if (["body", "html", "main"].includes(n.localName)) break;
      if (n.localName.includes("-")) add(n.localName);
      if (n.id) add("#" + n.id);
      for (const c of Array.from(n.classList || [])) add("." + c);
      for (const a of ["data-testid", "data-test", "data-component", "data-cy"]) if (n.getAttribute(a)) add(n.getAttribute(a));
      const root = n.getRootNode();
      if (root.nodeType === 11 && root.host && root.host.localName.includes("-")) add(root.host.localName);
    }
    return out;
  };
  const descOf = (el) => {
    const attrs = [];
    if (el.id) attrs.push(`id="${el.id}"`);
    const cls = clean(typeof el.className === "string" ? el.className : "");
    if (cls) attrs.push(`class="${clip(cls, 40)}"`);
    for (const a of ["type", "role", "src", "href", "aria-label"]) if (el.hasAttribute(a)) attrs.push(`${a}="${clip(clean(el.getAttribute(a)), 40)}"`);
    const text = clip(clean(el.textContent), 40);
    return `<${el.localName}${attrs.length ? " " + attrs.join(" ") : ""}>${text}`;
  };
  const base = (el, d) => {
    d = d || describe(el);
    return { path: d.path, role: d.role, name: d.name, rect: rectOf(el), tokens: tokensOf(el), desc: descOf(el) };
  };

  // ---------- colours ----------
  let canvasCtx = null;
  const rgba = (value) => {
    const m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/.exec(clean(value));
    if (m) {
      let a = m[4] === undefined ? 1 : m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
      return [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3]), a];
    }
    if (!value || value === "transparent") return [0, 0, 0, 0];
    if (!canvasCtx) canvasCtx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    canvasCtx.clearRect(0, 0, 1, 1);
    canvasCtx.fillStyle = "#000";
    canvasCtx.fillStyle = value;
    canvasCtx.fillRect(0, 0, 1, 1);
    const d = canvasCtx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const opacityChain = (el) => {
    let o = 1;
    for (let n = el; n; n = parentElementOf(n)) o *= parseFloat(getComputedStyle(n).opacity || "1");
    return o;
  };
  const bgLayers = (el, point) => {
    // Background layers behind el, top first, until one is opaque. Uses the hit-test stack when the point is on screen.
    const layers = [];
    let image = false;
    const push = (n) => {
      const st = getComputedStyle(n);
      if (st.backgroundImage && st.backgroundImage !== "none") image = true;
      const c = rgba(st.backgroundColor);
      if (c[3] > 0) layers.push(c);
      return c[3] >= 0.999;
    };
    let stack = [];
    if (point) {
      const root = el.getRootNode();
      stack = (root.elementsFromPoint ? root.elementsFromPoint(point[0], point[1]) : document.elementsFromPoint(point[0], point[1])) || [];
    }
    const start = stack.findIndex((n) => n === el || n.contains(el) || el.contains(n));
    if (start >= 0) {
      for (const n of stack.slice(start)) {
        if (el.contains(n) && n !== el) continue;
        if (push(n)) return { layers, image };
      }
      return { layers, image };
    }
    for (let n = el; n; n = parentElementOf(n)) if (push(n)) break;
    return { layers, image };
  };
  const ownText = (el) => clean(Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.data).join(" "));
  const textPoint = (el) => {
    const node = Array.from(el.childNodes).find((n) => n.nodeType === 3 && clean(n.data));
    if (!node) return null;
    const range = document.createRange();
    range.selectNodeContents(node);
    const r = range.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    return x >= 0 && y >= 0 && x < innerWidth && y < innerHeight ? [x, y] : null;
  };
  const contrastRecord = (el) => {
    const st = getComputedStyle(el);
    const bg = bgLayers(el, textPoint(el));
    return {
      ...base(el),
      text: clip(ownText(el), MAX_TEXT),
      fg: rgba(st.color),
      opacity: opacityChain(el),
      bg: bg.layers,
      bg_image: bg.image,
      font_size: parseFloat(st.fontSize),
      font_weight: parseFloat(st.fontWeight) || 400,
      disabled: disabled(el),
    };
  };

  // ---------- names ----------
  const byIds = (el, attr) => clean(el.getAttribute(attr)).split(" ").filter(Boolean).map((id) => el.getRootNode().getElementById ? el.getRootNode().getElementById(id) : document.getElementById(id)).filter(Boolean);
  const innerName = (el) => {
    for (const n of el.querySelectorAll("[aria-label], img[alt], svg title")) {
      const v = n.localName === "title" ? n.textContent : n.getAttribute("aria-label") || n.getAttribute("alt");
      if (clean(v) && !n.closest("[aria-hidden=true]")) return clean(v);
    }
    return "";
  };
  const programmaticName = (el, d) => {
    // The walker's name, except a field named only by nearby text (no label tied to it) has no programmatic name.
    const tied = clean(el.getAttribute("aria-label")) || byIds(el, "aria-labelledby").length || (el.labels && el.labels.length) || clean(el.getAttribute("title")) || clean(el.getAttribute("placeholder"));
    if (FIELD_ROLES.has(d.role) && !tied) return { name: "", caption: d.name };
    if (d.name) return { name: d.name, caption: "" };
    return { name: innerName(el), caption: "" };
  };
  const HEADING = "h1, h2, h3, h4, h5, h6, [role=heading], legend, caption";
  const nearText = (el) => {
    // The closest heading before el inside its nearest containers, else the text just before it.
    for (let node = parentElementOf(el), depth = 0; node && depth < 5; node = parentElementOf(node), depth += 1) {
      const before = Array.from(node.querySelectorAll(HEADING)).filter((h) => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      const heading = before.pop();
      if (heading && clean(heading.textContent)) return clip(clean(heading.textContent), MAX_TEXT);
      if (["body", "html"].includes(node.localName)) break;
    }
    let prev = el.previousElementSibling;
    for (let i = 0; prev && i < 3; i += 1, prev = prev.previousElementSibling) {
      const text = clean(prev.innerText || prev.textContent || "");
      if (text) return clip(text, MAX_TEXT);
    }
    return "";
  };
  const iconHints = (el) => {
    const out = [];
    for (const n of [el, ...el.querySelectorAll("*")].slice(0, 8)) {
      for (const a of ["data-icon", "data-lucide", "data-feather", "icon", "name"]) if (n.getAttribute && n.getAttribute(a)) out.push(n.getAttribute(a));
      if (n.id) out.push(n.id);
      const cls = n.getAttribute ? n.getAttribute("class") : "";
      if (cls) out.push(...cls.split(/\s+/));
      if (n.localName === "use" && n.getAttribute("href")) out.push(n.getAttribute("href"));
    }
    return out.filter(Boolean).slice(0, 12);
  };
  const nameRecord = (el) => {
    const d = describe(el);
    const pn = programmaticName(el, d);
    return { ...base(el, d), name: pn.name, caption: pn.caption, near: nearText(el), icon_hints: iconHints(el) };
  };

  // ---------- targets, images, focus ----------
  const isInlineTextLink = (el) => {
    if (el.localName !== "a" || getComputedStyle(el).display !== "inline") return false;
    const parent = parentElementOf(el);
    return !!parent && clean(parent.textContent).length > clean(el.textContent).length + 3;
  };
  const candidates = () =>
    allElements().filter((el) => {
      if (!el.matches(INTERACTIVE) || !rendered(el)) return false;
      const role = describe(el).role;
      if (NAMED_ROLES.has(role)) return true;
      const st = getComputedStyle(el);
      const parent = parentElementOf(el);
      return (el.hasAttribute("onclick") || (st.cursor === "pointer" && (!parent || getComputedStyle(parent).cursor !== "pointer"))) && !!clean(el.textContent);
    });
  const clickableGenerics = () =>
    allElements().filter((el) => {
      if (el.matches(INTERACTIVE) && NAMED_ROLES.has(describe(el).role)) return false;
      if (!rendered(el) || !clean(el.textContent)) return false;
      const st = getComputedStyle(el);
      const parent = parentElementOf(el);
      const pointer = st.cursor === "pointer" && (!parent || getComputedStyle(parent).cursor !== "pointer");
      return (el.hasAttribute("onclick") || pointer) && !el.querySelector("a[href], button, input, select, textarea");
    });
  const styleMap = (el, props, pseudo) => {
    const st = getComputedStyle(el, pseudo || null);
    const out = {};
    for (const p of props) out[p] = st.getPropertyValue(p);
    return out;
  };
  const focusStyles = (el) => {
    const parent = parentElementOf(el);
    return { self: styleMap(el, FOCUS_PROPS), parent: parent ? styleMap(parent, PARENT_FOCUS_PROPS) : {} };
  };
  const tabbable = (el) => el.tabIndex >= 0 && !disabled(el) && rendered(el);
  const focusRecord = (el) => {
    const d = describe(el);
    const radioGroup = d.role === "radio" && el.name ? el.name : null;
    return { ...base(el, d), tab_index: el.tabIndex, tabbable: tabbable(el), composite: COMPOSITE_ROLES.has(d.role), radio_group: radioGroup, focus: focusStyles(el), generic: !NAMED_ROLES.has(d.role), disabled: disabled(el) };
  };
  const deepActive = () => {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    if (!a || a === document.body || a === document.documentElement) return null;
    const d = describe(a);
    return { path: d.path, role: d.role, name: d.name, frame: a.localName === "iframe" || a.localName === "frame", styles: focusStyles(a) };
  };
  const tabOrder = () => allElements().filter(tabbable);
  const imageRecord = (el) => {
    const src = el.currentSrc || el.getAttribute("src") || "";
    return { ...base(el), src: clip(src.startsWith("data:") ? "data:" : src, 200), has_alt: el.hasAttribute("alt"), alt: el.getAttribute("alt"), natural: [el.naturalWidth || 0, el.naturalHeight || 0] };
  };
  const imagesMissingAlt = () =>
    allElements().filter((el) => {
      if (!rendered(el) || el.closest("[aria-hidden=true]")) return false;
      const role = clean(el.getAttribute("role"));
      if (role === "presentation" || role === "none") return false;
      const labelled = clean(el.getAttribute("aria-label")) || byIds(el, "aria-labelledby").length || clean(el.getAttribute("title"));
      if (el.localName === "img") return !el.hasAttribute("alt") && !labelled;
      if (el.localName === "input" && (el.type || "").toLowerCase() === "image") return !clean(el.getAttribute("alt")) && !labelled;
      if (role === "img") return !labelled;
      return false;
    });

  // ---------- layout ----------
  const layoutInfo = (el) => {
    const st = getComputedStyle(el);
    const parent = parentElementOf(el);
    const pst = parent ? getComputedStyle(parent) : null;
    return {
      position: st.position,
      display: st.display,
      width_style: el.style.width || "",
      margins: [st.marginTop, st.marginRight, st.marginBottom, st.marginLeft].map(parseFloat),
      parent: parent ? { path: describe(parent).path, display: pst.display, flex_wrap: pst.flexWrap, rect: rectOf(parent) } : null,
    };
  };
  const ancestorsInfo = (el) => {
    const out = [];
    for (let n = parentElementOf(el), depth = 0; n && depth < 6; n = parentElementOf(n), depth += 1) {
      if (["body", "html"].includes(n.localName)) break;
      const st = getComputedStyle(n);
      out.push({ path: describe(n).path, display: st.display, flex_wrap: st.flexWrap, rect: rectOf(n), scroll_width: n.scrollWidth, client_width: n.clientWidth, width: st.width, desc: descOf(n), tokens: tokensOf(n) });
    }
    return out;
  };
  const clipRecord = (el) => {
    const st = getComputedStyle(el);
    return {
      ...base(el),
      text: clip(clean(el.textContent), MAX_TEXT),
      scroll_width: el.scrollWidth,
      client_width: el.clientWidth,
      scroll_height: el.scrollHeight,
      client_height: el.clientHeight,
      overflow_x: st.overflowX,
      overflow_y: st.overflowY,
      white_space: st.whiteSpace,
      text_overflow: st.textOverflow,
      width: st.width,
      viewport: [innerWidth, innerHeight],
      clipped_x: el.scrollWidth > el.clientWidth + 1 && ["hidden", "clip"].includes(st.overflowX),
      clipped_y: el.scrollHeight > el.clientHeight + 1 && ["hidden", "clip"].includes(st.overflowY),
    };
  };
  const intersect = (a, b) => {
    const x0 = Math.max(a[0], b[0]);
    const y0 = Math.max(a[1], b[1]);
    const x1 = Math.min(a[0] + a[2], b[0] + b[2]);
    const y1 = Math.min(a[1] + a[3], b[1] + b[3]);
    return x1 - x0 >= 1 && y1 - y0 >= 1 ? [x0, y0, x1 - x0, y1 - y0] : null;
  };
  const overlapOf = (a, b) => {
    const box = intersect(rectOf(a), rectOf(b));
    if (!box) return null;
    const root = a.getRootNode();
    const hit = (root.elementFromPoint || document.elementFromPoint).call(root, box[0] + box[2] / 2, box[1] + box[3] / 2);
    const onTop = hit && (hit === a || a.contains(hit)) ? 0 : hit && (hit === b || b.contains(hit)) ? 1 : -1;
    return { box, on_top: onTop };
  };
  const layoutScan = () => {
    const controls = candidates().filter((el) => !visuallyHidden(el));
    const overlaps = [];
    for (let i = 0; i < controls.length; i += 1) {
      for (let j = i + 1; j < controls.length; j += 1) {
        const a = controls[i];
        const b = controls[j];
        if (a.contains(b) || b.contains(a)) continue;
        const o = overlapOf(a, b);
        if (o) overlaps.push({ a: { ...base(a), layout: layoutInfo(a) }, b: { ...base(b), layout: layoutInfo(b) }, ...o });
      }
    }
    const clipped = allElements()
      .filter((el) => rendered(el) && ownText(el) && !visuallyHidden(el))
      .map(clipRecord)
      .filter((r) => r.clipped_x || r.clipped_y);
    const offscreen = controls
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.right > innerWidth + 1 || r.left < -1;
      })
      .map((el) => ({ ...base(el), viewport: [innerWidth, innerHeight], ancestors: ancestorsInfo(el) }));
    return { viewport: [innerWidth, innerHeight], page_width: document.documentElement.scrollWidth, overlaps, clipped, offscreen };
  };

  // ---------- page changes, always undone by the caller ----------
  // Several overrides can sit on one element at once: the attribute holds one token per override.
  const fixTokens = (el) => (el.getAttribute("data-hq-fix") || "").split(" ").filter(Boolean);
  const mark = (el) => {
    el.setAttribute("data-hq-fix", [...fixTokens(el), args.token].join(" "));
    const style = document.createElement("style");
    style.setAttribute("data-hq-fix-style", args.token);
    style.textContent = args.css;
    const root = el.getRootNode();
    (root.nodeType === 11 ? root : document.head || document.documentElement).appendChild(style);
    return true;
  };
  const unmark = (el) => {
    const rest = fixTokens(el).filter((t) => t !== args.token);
    if (rest.length) el.setAttribute("data-hq-fix", rest.join(" "));
    else el.removeAttribute("data-hq-fix");
    const root = el.getRootNode();
    for (const s of (root.nodeType === 11 ? root : document).querySelectorAll(`style[data-hq-fix-style="${args.token}"]`)) s.remove();
    return true;
  };
  const setAttr = (el) => {
    const previous = el.hasAttribute(args.name) ? el.getAttribute(args.name) : null;
    if (args.value === null) el.removeAttribute(args.name);
    else el.setAttribute(args.name, args.value);
    return previous;
  };
  const focusProbe = (el) => {
    const before = document.activeElement;
    el.blur();
    const unfocused = focusStyles(el);
    el.focus({ preventScroll: true });
    const focused = focusStyles(el);
    const bgBehind = bgLayers(parentElementOf(el) || el, null).layers;
    el.blur();
    if (before && before !== el && before.focus) before.focus({ preventScroll: true });
    return { unfocused, focused, bg: bgBehind };
  };
  const focusBefore = (el) => {
    // Focus the tabbable element just before el in tree order (or nothing), so one Tab should land on el.
    const order = tabOrder();
    const all = allElements();
    const index = all.indexOf(el);
    const previous = order.filter((n) => all.indexOf(n) < index).pop();
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (previous) previous.focus({ preventScroll: true });
    return !!previous;
  };
  const focusAfter = (el) => {
    const order = tabOrder();
    const all = allElements();
    const index = all.indexOf(el);
    const next = order.find((n) => all.indexOf(n) > index && !el.contains(n));
    if (!next) return false;
    next.focus({ preventScroll: true });
    return true;
  };
  const isActive = (el) => {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    return a === el;
  };

  if (target) {
    switch (args.op) {
      case "contrast": return contrastRecord(target);
      case "name": return nameRecord(target);
      case "rect": return { rect: rectOf(target), display: getComputedStyle(target).display, viewport: [innerWidth, innerHeight] };
      case "image": return imageRecord(target);
      case "styles": return { rect: rectOf(target), styles: styleMap(target, STYLE_PROPS) };
      case "clip": return clipRecord(target);
      case "overlap": return { box: intersect(rectOf(target), rectOf(args.other)) };
      case "describe": return { ...base(target), layout: layoutInfo(target) };
      case "mark": return mark(target);
      case "unmark": return unmark(target);
      case "attr": return setAttr(target);
      case "focus_probe": return focusProbe(target);
      case "focus_before": return focusBefore(target);
      case "focus_after": return focusAfter(target);
      case "is_active": return isActive(target);
      default: throw new Error("unknown element probe " + args.op);
    }
  }
  switch (args.op) {
    case "contrast":
      return allElements().filter((el) => rendered(el) && ownText(el) && !visuallyHidden(el)).map(contrastRecord);
    case "names":
      return candidates().map(nameRecord);
    case "targets":
      return candidates()
        .filter((el) => !disabled(el) && !visuallyHidden(el))
        .map((el) => ({ ...base(el), inline_text_link: isInlineTextLink(el), display: getComputedStyle(el).display }));
    case "images":
      return imagesMissingAlt().map(imageRecord);
    case "focusables":
      return { controls: candidates().map(focusRecord), generics: clickableGenerics().map(focusRecord) };
    case "active":
      return deepActive();
    case "layout":
      return layoutScan();
    case "resolve":
      // Elements named by axe targets: a selector, or a list of selectors one per shadow tree.
      return (args.targets || []).map((t) => {
        const parts = Array.isArray(t) ? t : [t];
        let root = document;
        let el = null;
        for (let i = 0; i < parts.length; i += 1) {
          el = root.querySelector(parts[i]);
          if (!el) return null;
          if (i < parts.length - 1) {
            if (!el.shadowRoot) return null;
            root = el.shadowRoot;
          }
        }
        return el ? base(el) : null;
      });
    case "scroll":
      if (args.to) window.scrollTo(args.to[0], args.to[1]);
      return [scrollX, scrollY];
    default:
      throw new Error("unknown probe " + args.op);
  }
}
