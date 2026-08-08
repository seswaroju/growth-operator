import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import Placeholder from "./components/Placeholder";
import QueueSection from "./components/QueueSection";
import Shell from "./components/Shell";
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
const debugRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/debug",
  component: DebugPage,
});

const routeTree = rootRoute.addChildren([queueRoute, storesRoute, debugRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
