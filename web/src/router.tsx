import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import Shell from "./components/Shell";
import HomeSection from "./components/HomeSection";
import ApprovalsSection from "./components/ApprovalsSection";
import ConversationsSection from "./components/ConversationsSection";
import CatalogSection from "./components/CatalogSection";
import CustomersSection from "./components/CustomersSection";
import InsightsSection from "./components/InsightsSection";
import SupportSection from "./components/SupportSection";
import TeamSection from "./components/TeamSection";
import SettingsSection from "./components/SettingsSection";

const rootRoute = createRootRoute({ component: Shell });

// Explicit createRoute calls (literal `path`s) so TanStack infers the typed route tree that
// <Link to="…"> checks against.
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: HomeSection });
const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/approvals", component: ApprovalsSection });
const conversationsRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/conversations", component: ConversationsSection });
const catalogRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/catalog", component: CatalogSection });
const customersRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/customers", component: CustomersSection });
const insightsRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/insights", component: InsightsSection });
const supportRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/support", component: SupportSection });
const teamRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/team", component: TeamSection });
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute, path: "/settings", component: SettingsSection });

const routeTree = rootRoute.addChildren([
  homeRoute, approvalsRoute, conversationsRoute, catalogRoute,
  customersRoute, insightsRoute, supportRoute, teamRoute, settingsRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
