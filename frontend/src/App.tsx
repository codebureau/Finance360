import { RouterProvider } from "@tanstack/react-router";
import { useState } from "react";

import { createAppRouter, type AppRouter } from "@/routes";

/** The routed app. `AuthGate` wraps this (outside the router), so sign-in happens at the
 * requested URL and the user is still on it afterwards. */
export default function App({ router }: { router?: AppRouter }) {
  const [current] = useState(() => router ?? createAppRouter());
  return <RouterProvider router={current} />;
}
