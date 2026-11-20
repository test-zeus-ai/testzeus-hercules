import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Redirect, Route, Switch, useLocation, useSearch } from "wouter";
import { BrandLockup, Wordmark } from "./components/Brand";
import { ProPromo } from "./components/ProPromo";
import { TESTZEUS_SITE, withUtm } from "./lib/links";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Icon, type IconName } from "./components/Icon";
import { Notifications } from "./components/Notifications";
import { api, UNAUTHORIZED_EVENT } from "./lib/api";
import { dissolvePage } from "./lib/pageDissolve";
import { useFont } from "./lib/font";
import { useTheme } from "./lib/theme";
import type { Health } from "./lib/types";
import { CasesScreen } from "./screens/Cases";
import { DashboardScreen } from "./screens/Dashboard";
import { EnvironmentsScreen } from "./screens/Environments";
import { ExploreScreen } from "./screens/Explore";
import { ExploreSessionScreen } from "./screens/ExploreSession";
import { IntegrationsScreen } from "./screens/Integrations";
import { SettingsScreen } from "./screens/Settings";
import { ModelsScreen } from "./screens/Models";
import { RunDetailScreen } from "./screens/RunDetail";
import { RunsScreen } from "./screens/Runs";

const NAV: Array<{ href: string; label: string; icon: IconName; match: (path: string) => boolean; beta?: boolean }> = [
  { href: "/", label: "Dashboard", icon: "dashboard", match: (path) => path === "/" },
  { href: "/cases", label: "Cases", icon: "cases", match: (path) => path.startsWith("/cases") },
  { href: "/runs", label: "Runs", icon: "runs", match: (path) => path.startsWith("/runs") },
  { href: "/explore", label: "Explore", icon: "compass", match: (path) => path.startsWith("/explore"), beta: true },
  { href: "/environments", label: "Environments", icon: "envs", match: (path) => path.startsWith("/environments") },
  { href: "/models", label: "Models", icon: "models", match: (path) => path.startsWith("/models") },
  { href: "/integrations", label: "Integrations", icon: "cloud", match: (path) => path.startsWith("/integrations") },
];

const SIDEBAR_KEY = "hercules.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(SIDEBAR_KEY, "1");
    else window.localStorage.removeItem(SIDEBAR_KEY);
  } catch {
    return;
  }
}


/** The dashboard; a link to the old runs list (`/?status=failed`, `/?batch=…`) goes to `/runs` with its filters. */
function Home() {
  const search = useSearch();
  return search ? <Redirect to={`/runs?${search}`} replace /> : <DashboardScreen />;
}

function SignedOut() {
  return (
    <div className="signed-out" role="alertdialog" aria-modal="true" aria-labelledby="signed-out-title">
      <div className="signed-out-card">
        <Wordmark className="signed-out-brand" aria-hidden="true" />
        <h2 id="signed-out-title">Signed out of Hercules</h2>
        <p>The daemon no longer knows this browser (it was restarted, or the session ended).</p>
        <p>
          Run <code>hercules ui</code> in a terminal to get a new one-time sign-in link.
        </p>
        <p className="signed-out-foot">
          Hercules is an open-source QA engine by{" "}
          <a href={withUtm(TESTZEUS_SITE, "signed-out")} target="_blank" rel="noopener noreferrer">
            TestZeus
          </a>
          .
        </p>
      </div>
    </div>
  );
}

export function App() {
  const [location] = useLocation();
  useTheme();
  useFont();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [animating, setAnimating] = useState(false);
  useEffect(() => {
    if (!animating) return undefined;
    const timer = window.setTimeout(() => setAnimating(false), 320);
    return () => window.clearTimeout(timer);
  }, [animating, collapsed]);
  const toggleSidebar = () => {
    setAnimating(true);
    setCollapsed((value) => {
      writeCollapsed(!value);
      return !value;
    });
  };
  const [signedOut, setSignedOut] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health"), refetchInterval: 15000 });

  useEffect(() => {
    const onUnauthorized = () => setSignedOut(true);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const queue = health.data?.queue ?? {};
  const active = (queue.running ?? 0) + (queue.queued ?? 0);

  return (
    <div className={`shell${collapsed ? " shell-collapsed" : ""}${animating ? " shell-animating" : ""}`}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <button type="button" className="sidebar-toggle" onClick={toggleSidebar} aria-expanded={!collapsed} aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"} title={collapsed ? "Expand" : "Collapse"}>
          <Icon name="chevron" size={14} />
        </button>
        <Link href="/" className="brand" aria-label="Hercules, a QA engine by TestZeus: dashboard">
          <BrandLockup />
        </Link>
        <nav aria-label="Main">
          <ul className="nav">
            {NAV.map((item) => {
              const current = item.match(location);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={`nav-link${current ? " nav-current" : ""}`}
                    aria-current={current ? "page" : undefined}
                    title={collapsed ? item.label : undefined}
                    onClick={(event) => {
                      // A plain click to another page dissolves the current one from the click (keyboard: from the tab).
                      if (current || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                      const tab = event.currentTarget.getBoundingClientRect();
                      dissolvePage(document.getElementById("main"), event.detail > 0 ? { x: event.clientX, y: event.clientY } : { x: tab.right, y: tab.top + tab.height / 2 });
                    }}
                  >
                    <Icon name={item.icon} size={17} />
                    <span className="nav-label">{item.label}</span>
                    {item.beta ? <span className="nav-beta">Beta</span> : null}
                    {item.href === "/runs" && active > 0 ? <span className="nav-badge" aria-label={`${active} active`}>{active}</span> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="sidebar-foot">
          <ProPromo />
          <Link
            href="/settings"
            className={`nav-link settings-link${location.startsWith("/settings") ? " nav-current" : ""}`}
            aria-current={location.startsWith("/settings") ? "page" : undefined}
            title={collapsed ? "Settings" : undefined}
          >
            <Icon name="settings" size={16} />
            <span className="nav-label">Settings</span>
          </Link>
          {health.data ? (
            <p className="daemon-info" title={health.data.project_dir}>
              <span className="daemon-dot" aria-hidden="true" /> v{health.data.version}
              <span className="daemon-project">{health.data.project_dir.split("/").pop()}</span>
            </p>
          ) : health.isError ? (
            <p className="daemon-info daemon-down">Daemon unreachable</p>
          ) : null}
          <a className="made-by" href={withUtm(TESTZEUS_SITE, "made-by")} target="_blank" rel="noopener noreferrer" title="testzeus.com (new tab)">
            <span className="nav-label">Made by</span> <Wordmark className="made-by-wordmark" aria-hidden="true" />
            <span className="visually-hidden">TestZeus (testzeus.com, new tab)</span>
          </a>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <ErrorBoundary resetKey={location}>
          <Switch>
            <Route path="/" component={Home} />
            <Route path="/runs" component={RunsScreen} />
            <Route path="/runs/:id">{(params) => <RunDetailScreen key={params.id} runId={params.id} />}</Route>
            <Route path="/explore" component={ExploreScreen} />
            <Route path="/explore/:id">{(params) => <ExploreSessionScreen key={params.id} sessionId={params.id} />}</Route>
            <Route path="/cases">{() => <CasesScreen />}</Route>
            <Route path="/cases/*">{(params) => <CasesScreen selected={params["*"]} />}</Route>
            <Route path="/environments" component={EnvironmentsScreen} />
            <Route path="/models" component={ModelsScreen} />
            <Route path="/integrations" component={IntegrationsScreen} />
            <Route path="/settings" component={SettingsScreen} />
            <Route>
              <section className="page">
                <h1>Not found</h1>
                <p>
                  <Link href="/">Back to the dashboard</Link>
                </p>
              </section>
            </Route>
          </Switch>
        </ErrorBoundary>
      </main>
      <Notifications />
      {signedOut ? <SignedOut /> : null}
    </div>
  );
}
