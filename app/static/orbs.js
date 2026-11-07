// Thinking Orbs (MIT, libraries.dev/orbs), the same React canvas orbs as the Natural Voice site, loaded without a build step.
// Slots keep their text fallback if this can't load. Pages call window.HOrbs.mount(el, state, size) once "orbs-ready" fires.
import React from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client?deps=react@18.3.1";
import { ThinkingOrb } from "https://esm.sh/thinking-orbs@0.3.2?deps=react@18.3.1";

function mount(el, state, size) {
  el.textContent = "";
  const root = createRoot(el);
  let current = null;
  const draw = (s) => { if (s !== current) { current = s; root.render(React.createElement(ThinkingOrb, { state: s, size, theme: "dark" })); } };
  draw(state);
  return { set: draw, unmount: () => root.unmount() };
}
window.HOrbs = { mount };
window.dispatchEvent(new Event("orbs-ready"));
