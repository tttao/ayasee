import "./styles.css";
import { mount, refresh, tick } from "./app.ts";

const REFRESH_MS = 5 * 60_000;

void mount(document).then(() => {
  // Keep the relative age honest on a page left open, and pick up new snapshots.
  setInterval(() => tick(document), 60_000);
  setInterval(() => void refresh(document), REFRESH_MS);
});
