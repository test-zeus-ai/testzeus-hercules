/* Marks of the clouds Hercules runs tests on, taken from each company's own website (November 2026) and shown only
   to identify the integration. The names and logos are trademarks of their owners. Inline SVG (and a bundled PNG)
   fetch nothing at run time, so the daemon's same-origin content security policy is unaffected. */
import sauceLabsMark from "../assets/clouds/saucelabs-mark.png";

/** BrowserStack's hexagon, in its own colours. */
const BROWSERSTACK: ReadonlyArray<readonly [string, string]> = [
  ["M121.248 192.99C104.677 202.263 84.267 202.342 67.6199 193.195L0 156.05L53.2871 126.136L146.717 178.736L121.248 192.99Z", "#44D4F2"],
  ["M28.0742 35.3257C11.3557 44.51 0.187143 61.6114 0 80.2142V156.05L53.2628 185.305V21.2829L28.0742 35.3257Z", "#E86F32"],
  ["M53.2556 185.303L0 156.055L53.2556 126.156V185.303Z", "#02B2D6"],
  ["M171.925 164.673C188.644 155.49 199.813 138.389 200 119.785V43.9489L146.737 14.6932V178.716L171.925 164.673Z", "#6DB64C"],
  ["M78.7656 7.01021C95.337 -2.26263 115.746 -2.34118 132.393 6.80452L200 43.9487L146.727 73.863L53.2956 21.2631L78.7656 7.01021Z", "#F5BB60"],
  ["M200 43.9465L146.744 73.8293V14.698L200 43.9465Z", "#E28C23"],
];

/** TestMu AI's pixel mark: one colour, drawn in `currentColor` so it follows the theme. */
const TESTMU = [
  "M18.0137 0H12.6097V13.0595H18.0137V0Z",
  "M5.4043 0H0.000360489V7.43041L5.4043 13.0595V0Z",
  "M5.4043 13.0595H0.000360489V20.2648H5.4043V13.0595Z",
  "M12.6104 13.0595H5.4051L9.68322 18.4634H12.6104V13.0595Z",
];

/** The cloud's mark, or null for a cloud without one. Decorative: the cloud's name is always shown beside it. */
export function CloudLogo({ cloud }: { cloud: string }) {
  if (cloud === "browserstack") {
    return (
      <svg className="cloud-logo" viewBox="0 0 200 200" aria-hidden="true" focusable="false">
        {BROWSERSTACK.map(([d, fill]) => (
          <path key={d.slice(0, 24)} d={d} fill={fill} />
        ))}
      </svg>
    );
  }
  if (cloud === "testmu") {
    return (
      <svg className="cloud-logo cloud-logo-mono" viewBox="-1.12 0 20.26 20.26" aria-hidden="true" focusable="false">
        {TESTMU.map((d) => (
          <path key={d.slice(0, 24)} d={d} fill="currentColor" />
        ))}
      </svg>
    );
  }
  if (cloud === "saucelabs") {
    return <img className="cloud-logo" src={sauceLabsMark} alt="" width={96} height={96} />;
  }
  return null;
}
