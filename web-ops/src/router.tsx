import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import AnalyticsSection from "./components/AnalyticsSection";
import CustomerSuccessSection from "./components/CustomerSuccessSection";
import FinancialSection from "./components/FinancialSection";
import OperationalSection from "./components/OperationalSection";
import Placeholder from "./components/Placeholder";
import QueueSection from "./components/QueueSection";
import Shell from "./components/Shell";
import StoreReportsSection from "./components/StoreReportsSection";
import StoresSection from "./components/StoresSection";

function DebugPage() {
  return (
    <Placeholder
      title="Debug"
      note="Run replay, agent internals, feature flags — dev-only, a later phase."
    />
  );
}

const rootRoute = createRootRoute({ component: Shell });

const queueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: QueueSection,
});
const storesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/stores",
  component: StoresSection,
});
const storeReportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/stores/$orgId",
  component: StoreReportsSection,
});
const opsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops",
  component: OperationalSection,
});
const analyticsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analytics",
  component: AnalyticsSection,
});
const healthRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/health",
  component: CustomerSuccessSection,
});
const financialRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/financial",
  component: FinancialSection,
});
const debugRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/debug",
  component: DebugPage,
});

const routeTree = rootRoute.addChildren([
  queueRoute, storesRoute, storeReportsRoute, opsRoute, analyticsRoute, healthRoute, financialRoute,
  debugRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
