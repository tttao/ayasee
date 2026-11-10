import "./styles.css";
import { mount, renderAge } from "./app.ts";

void mount(document).then((s) => {
  if (!s) return;
  // Keep the relative age honest on a page left open.
  setInterval(() => renderAge(document, s, new Date()), 60_000);
});
