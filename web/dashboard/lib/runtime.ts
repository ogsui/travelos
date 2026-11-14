/** Server-only runtime labels. */

export function modeLabel(): string {
  const mode = (process.env.MODE ?? "test").trim().toLowerCase();
  return mode === "live" || mode === "shadow" || mode === "test" ? mode : "test";
}

export function modeDescription(mode: string): string {
  switch (mode) {
    case "live":
      return "Live mode. Actions follow the approval matrix and the enabled kill switches.";
    case "shadow":
      return "Shadow mode. The full pipeline runs, but nothing is sent to customers or suppliers.";
    default:
      return "Test mode. Synthetic data only; nothing is sent to customers or suppliers.";
  }
}
