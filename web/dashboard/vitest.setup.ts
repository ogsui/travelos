import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without injected globals here, so Testing Library cannot register
// its own auto-cleanup. Unmount between tests explicitly.
afterEach(cleanup);
