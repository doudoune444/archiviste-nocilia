/**
 * AppSidebar — server component that fetches identity and renders the client
 * sidebar app-shell (#248). Rendered once in the root layout, on every page.
 *
 * Identity comes from GET /v1/me forwarded by the bff-proxy (cookie is the
 * source of truth, never client-supplied — A01) and degrades to anonymous on
 * any failure.
 */

import { fetchIdentity } from "./fetch-identity";
import { SidebarShell } from "./SidebarShell";

export default async function AppSidebar() {
  const identity = await fetchIdentity();
  return <SidebarShell identity={identity} />;
}
