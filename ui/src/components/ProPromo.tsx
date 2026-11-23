import { useState } from "react";
import { TESTZEUS_FREE_TRIAL, TESTZEUS_PRICING, withUtm } from "../lib/links";
import { Icon } from "./Icon";

const HIDDEN_KEY = "hercules.pro-promo.hidden";

function readHidden(): boolean {
  try {
    return window.localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHidden(hidden: boolean): void {
  try {
    if (hidden) window.localStorage.setItem(HIDDEN_KEY, "1");
    else window.localStorage.removeItem(HIDDEN_KEY);
  } catch {
    // Private windows and blocked storage: the card simply shows again next time.
  }
}

/** TestZeus Pro, the TestZeus cloud platform: a card in the sidebar with a free trial link. Hidden, it
    shrinks to one link, so it can always be found again. */
export function ProPromo() {
  const [hidden, setHidden] = useState(readHidden);
  const trial = withUtm(TESTZEUS_FREE_TRIAL, "try-pro");
  const hide = (value: boolean) => {
    writeHidden(value);
    setHidden(value);
  };

  const link = (
    <a className={`pro-link${hidden ? "" : " pro-link-narrow"}`} href={trial} target="_blank" rel="noopener noreferrer" title="Try TestZeus Pro free (new tab)">
      <Icon name="shield" size={16} />
      <span className="nav-label">Try TestZeus Pro</span>
    </a>
  );
  if (hidden) return link;
  return (
    <>
      {link}
      <aside className="pro-promo" aria-labelledby="pro-promo-title">
      <div className="pro-promo-head">
        <p id="pro-promo-title" className="pro-promo-title">
          Go Pro with TestZeus
        </p>
        <button type="button" className="icon-button pro-promo-hide" onClick={() => hide(true)} aria-label="Hide the TestZeus Pro card" title="Hide">
          <Icon name="cross" size={12} />
        </button>
      </div>
      <p className="pro-promo-text">End to end test management, creation, execution and reporting in the cloud.</p>
      <a className="button button-small button-primary pro-promo-cta" href={trial} target="_blank" rel="noopener noreferrer">
        Start free trial <Icon name="link" size={12} />
      </a>
      <a className="pro-promo-more" href={withUtm(TESTZEUS_PRICING, "try-pro")} target="_blank" rel="noopener noreferrer">
        See plans and pricing
      </a>
      </aside>
    </>
  );
}
