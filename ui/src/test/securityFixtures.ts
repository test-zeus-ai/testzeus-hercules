import type { SecurityFinding, SecurityScan, SecuritySection } from "../lib/types";

export const RUN = "r_20261105_170329_8d91c1";

export function secFinding(overrides: Partial<SecurityFinding> = {}): SecurityFinding {
  return {
    template_id: "http-missing-security-headers",
    name: "HTTP Missing Security Headers",
    severity: "info",
    matched_at: "http://127.0.0.1:8000",
    matcher: "x-frame-options",
    tags: ["misconfig", "headers"],
    extracted: [],
    description: null,
    ...overrides,
  };
}

export function securityScan(overrides: Partial<SecurityScan> = {}): SecurityScan {
  const findings = overrides.findings ?? [
    secFinding(),
    secFinding({ matcher: "content-security-policy" }),
    secFinding({ matched_at: "http://127.0.0.1:8000/v1/pets", matcher: "x-frame-options" }),
    secFinding({ template_id: "exposed-env-file", name: "Exposed .env file", severity: "high", matcher: null, matched_at: "http://127.0.0.1:8000/.env", extracted: ["DB_HOST"] }),
  ];
  const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0, findings: findings.length };
  for (const item of findings) counts[item.severity] += 1;
  return {
    step: 1,
    target: "http://127.0.0.1:8000",
    input: "url",
    tags: ["misconfig", "exposure"],
    exclude_tags: [],
    fail_on: "medium",
    scanner: "nuclei 3.11.1",
    duration_ms: 12_300,
    error: null,
    evidence: [`hercules://runs/${RUN}/steps/1/summary.txt`, `hercules://runs/${RUN}/steps/1/findings.jsonl`],
    ...overrides,
    findings,
    counts,
  };
}

export function securitySection(scans: SecurityScan[] = [securityScan()]): SecuritySection {
  const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0, findings: 0 };
  for (const scan of scans) {
    for (const item of scan.findings) {
      counts[item.severity] += 1;
      counts.findings += 1;
    }
  }
  return { scans, counts };
}
