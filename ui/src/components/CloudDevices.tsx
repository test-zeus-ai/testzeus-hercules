import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, post } from "../lib/api";
import {
  EMPTY_DESKTOP_FILTER,
  EMPTY_MOBILE_FILTER,
  browserIcon,
  browserLabel,
  desktopOses,
  devicesSummary,
  filterDesktop,
  filterMobile,
  mixesRealAndVirtual,
  osIcon,
  platformLabel,
  versionsSummary,
  type DesktopFilter,
  type MobileFilter,
} from "../lib/cloudDevices";
import { formatDateTime } from "../lib/format";
import { isCloudDevices } from "../lib/guards";
import type { Cloud, CloudDevices } from "../lib/types";
import { Field } from "./Field";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

export const devicesKey = (cloud: Cloud) => ["clouds", cloud.id, "devices"] as const;

/** The cloud's saved device list (null before the first fetch) and the mutation that fetches it again. */
export function useCloudDevices(cloud: Cloud) {
  const client = useQueryClient();
  const saved = useQuery({
    queryKey: devicesKey(cloud),
    queryFn: async () => {
      try {
        return await api<CloudDevices>(`/api/clouds/${cloud.id}/devices`, { guard: isCloudDevices });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: cloud.connected,
    staleTime: Infinity,
  });
  const refresh = useMutation({
    mutationFn: () => post<CloudDevices>(`/api/clouds/${cloud.id}/devices`, undefined, isCloudDevices),
    onSuccess: (catalog) => client.setQueryData(devicesKey(cloud), catalog),
  });
  return { catalog: saved.data ?? null, refresh };
}

/** One line on the card: how many devices the saved list has and when it was fetched. */
export function CloudDevicesSummary({ catalog }: { catalog: CloudDevices }) {
  return (
    <div className="cloud-devices-summary small">
      <Icon name="phone" size={13} />
      <span>
        {devicesSummary(catalog)}
        <span className="cloud-devices-fetched muted">
          Fetched {formatDateTime(catalog.fetched_at)}
          {catalog.stale ? <span className="tag tag-check-warn">older settings</span> : null}
        </span>
      </span>
    </div>
  );
}

type Tab = "mobile" | "desktop";

/** The saved list of a cloud's mobile devices and desktop browsers, filterable, with a refresh. */
export function CloudDevicesDialog({ cloud, catalog, refresh, onClose }: { cloud: Cloud; catalog: CloudDevices; refresh: ReturnType<typeof useCloudDevices>["refresh"]; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>(catalog.mobile.length || !catalog.desktop.length ? "mobile" : "desktop");
  const [mobileFilter, setMobileFilter] = useState<MobileFilter>(EMPTY_MOBILE_FILTER);
  const [desktopFilter, setDesktopFilter] = useState<DesktopFilter>(EMPTY_DESKTOP_FILTER);
  const mobile = filterMobile(catalog.mobile, mobileFilter);
  const desktop = filterDesktop(catalog.desktop, desktopFilter);
  const oses = desktopOses(catalog.desktop);
  const updateMobile = (change: Partial<MobileFilter>) => setMobileFilter({ ...mobileFilter, ...change });
  const updateDesktop = (change: Partial<DesktopFilter>) => setDesktopFilter({ ...desktopFilter, ...change });
  const tabs: Array<[Tab, string, number]> = [
    ["mobile", "Mobile", catalog.mobile.length],
    ["desktop", "Desktop", catalog.desktop.length],
  ];

  return (
    <Modal title={`${cloud.name} devices`} onClose={onClose} wide>
      <div className="cloud-devices">
        <div className="cloud-devices-head">
          <p className="small muted">
            {catalog.username}
            {catalog.region ? ` · region ${catalog.region}` : ""} · fetched {formatDateTime(catalog.fetched_at)}
          </p>
          <button type="button" className="button button-small" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <Icon name="rerun" size={13} /> {refresh.isPending ? "Fetching…" : "Refresh"}
          </button>
        </div>
        {refresh.error ? (
          <p className="callout callout-fail" role="alert">
            <Icon name="cross" size={13} /> {(refresh.error as Error).message}
          </p>
        ) : null}
        {catalog.stale ? (
          <p className="callout callout-note">
            <Icon name="alert" size={13} /> This list was fetched with another user name or region. Refresh to see the devices for the current settings.
          </p>
        ) : null}
        {catalog.notes.map((note) => (
          <p key={note} className="callout callout-note small">
            <Icon name="alert" size={13} /> {note}
          </p>
        ))}

        <div className="segmented" role="tablist" aria-label="Device kind">
          {tabs.map(([id, label, count]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={`segment${tab === id ? " segment-on" : ""}`} onClick={() => setTab(id)}>
              {label} <span className="segment-count">{count}</span>
            </button>
          ))}
        </div>

        {tab === "mobile" ? (
          <>
            <div className="cloud-devices-filters">
              <Field id={`devices-${cloud.id}-search`} label="Search">
                <input type="search" value={mobileFilter.query} onChange={(event) => updateMobile({ query: event.target.value })} placeholder="Pixel, iPhone 16, 17.4…" />
              </Field>
              <Field id={`devices-${cloud.id}-platform`} label="Platform">
                <select value={mobileFilter.platform} onChange={(event) => updateMobile({ platform: event.target.value as MobileFilter["platform"] })}>
                  <option value="">All</option>
                  <option value="android">Android</option>
                  <option value="ios">iOS</option>
                </select>
              </Field>
              <Field id={`devices-${cloud.id}-kind`} label="Type">
                <select value={mobileFilter.kind} onChange={(event) => updateMobile({ kind: event.target.value as MobileFilter["kind"] })}>
                  <option value="">All</option>
                  <option value="phone">Phones</option>
                  <option value="tablet">Tablets</option>
                </select>
              </Field>
              {mixesRealAndVirtual(catalog.mobile) ? (
                <Field id={`devices-${cloud.id}-real`} label="Device">
                  <select value={mobileFilter.real} onChange={(event) => updateMobile({ real: event.target.value as MobileFilter["real"] })}>
                    <option value="">All</option>
                    <option value="real">Real</option>
                    <option value="virtual">Emulators and simulators</option>
                  </select>
                </Field>
              ) : null}
            </div>
            {mobile.length ? (
              <div className="table-wrap cloud-devices-table">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Device</th>
                      <th scope="col">OS</th>
                      <th scope="col">Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mobile.map((device) => (
                      <tr key={`${device.name}|${device.platform}|${device.os_version}|${device.real}`}>
                        <td>
                          <span className="cloud-device-name">
                            <span className="env-target" aria-hidden="true">
                              <Icon name={device.platform === "ios" ? "apple" : "android"} size={14} />
                            </span>
                            {device.name}
                          </span>
                        </td>
                        <td className="mono">
                          {platformLabel(device.platform)} {device.os_version}
                        </td>
                        <td>
                          <span className="tag">{device.real ? "Real" : device.platform === "ios" ? "Simulator" : "Emulator"}</span>
                          <span className="tag">{device.kind === "tablet" ? "Tablet" : "Phone"}</span>
                          {device.tier ? <span className="tag">{device.tier}</span> : null}
                          {device.status ? <span className={`tag${device.status.toLowerCase() === "available" ? " tag-check-ok" : ""}`}>{device.status}</span> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted cloud-devices-empty">{catalog.mobile.length ? "No device matches these filters." : `${cloud.name} listed no mobile devices for this account.`}</p>
            )}
          </>
        ) : (
          <>
            <div className="cloud-devices-filters">
              <Field id={`devices-${cloud.id}-desktop-search`} label="Search">
                <input type="search" value={desktopFilter.query} onChange={(event) => updateDesktop({ query: event.target.value })} placeholder="Chrome, Sonoma, 130…" />
              </Field>
              <Field id={`devices-${cloud.id}-os`} label="Operating system">
                <select value={desktopFilter.os} onChange={(event) => updateDesktop({ os: event.target.value })}>
                  <option value="">All</option>
                  {oses.map((os) => (
                    <option key={os} value={os}>
                      {os}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {desktop.length ? (
              <div className="table-wrap cloud-devices-table">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Operating system</th>
                      <th scope="col">Browser</th>
                      <th scope="col">Versions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {desktop.map((item) => (
                      <tr key={`${item.os}|${item.os_version}|${item.browser}`}>
                        <td>
                          <span className="cloud-device-name">
                            <span className="env-target" aria-hidden="true">
                              <Icon name={osIcon(item.os)} size={14} />
                            </span>
                            {item.os} {item.os_version}
                          </span>
                        </td>
                        <td>
                          <span className="cloud-device-name">
                            <Icon name={browserIcon(item.browser)} size={14} /> {browserLabel(item.browser)}
                          </span>
                        </td>
                        <td className="mono" title={item.versions.join(", ")}>
                          {versionsSummary(item.versions)}
                          {item.versions.length > 4 ? <span className="muted"> ({item.versions.length})</span> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted cloud-devices-empty">{catalog.desktop.length ? "No browser matches these filters." : `${cloud.name} listed no desktop browsers for this account.`}</p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
