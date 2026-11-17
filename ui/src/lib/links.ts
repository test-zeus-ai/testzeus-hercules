/* Links out of the tester UI to TestZeus. Each carries UTM parameters so TestZeus can tell visits that came from
   Hercules apart; nothing else (no project, run or user detail) is added to them. */

const UTM = { utm_source: "hercules", utm_medium: "tester-ui" } as const;

/** ``url`` with the Hercules UTM parameters and the campaign that names the place in the UI. */
export function withUtm(url: string, campaign: string): string {
  const target = new URL(url);
  for (const [key, value] of Object.entries({ ...UTM, utm_campaign: campaign })) {
    target.searchParams.set(key, value);
  }
  return target.toString();
}

export const TESTZEUS_SITE = "https://testzeus.com/";
/** Where testzeus.com's own "Free Trial" button goes. */
export const TESTZEUS_FREE_TRIAL = "https://prod.testzeus.app/sign-up";
export const TESTZEUS_PRICING = "https://testzeus.com/pricing";

/** The user guide on GitHub (the project's documentation link); ``page`` is a file in docs/guide. */
export const DOCS_BASE = "https://github.com/test-zeus-ai/testzeus-hercules/blob/main/docs/guide/";
export const docsUrl = (page: string): string => `${DOCS_BASE}${page}`;
