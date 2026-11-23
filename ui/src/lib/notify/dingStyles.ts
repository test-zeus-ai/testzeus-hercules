// ding-react's <Toaster> adds the library's CSS as an inline <style>, which the daemon's CSP (style-src 'self')
// refuses and reports. The build points ding-react's import of the library here (dingCss in vite.config.ts):
// the same library, minus that step, since the CSS is already bundled from virtual:ding.css.
export * from "@yusufceng1z/ding";

export function injectStyles(): void {}
