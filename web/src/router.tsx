import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import Shell from "./components/Shell";
import SupportSection from "./components/SupportSection";
import TeamSection from "./components/TeamSection";

const rootRoute = createRootRoute({ component: Shell });

const supportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: SupportSection,
});

const teamRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/team",
  component: TeamSection,
});

const routeTree = rootRoute.addChildren([supportRoute, teamRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
