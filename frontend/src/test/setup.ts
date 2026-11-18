import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

if (typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.scrollTo !== "function") {
  HTMLElement.prototype.scrollTo = function scrollTo(optionsOrLeft?: ScrollToOptions | number, top?: number) {
    if (typeof optionsOrLeft === "number") {
      this.scrollLeft = optionsOrLeft;
      if (typeof top === "number") this.scrollTop = top;
    } else if (optionsOrLeft) {
      if (typeof optionsOrLeft.left === "number") this.scrollLeft = optionsOrLeft.left;
      if (typeof optionsOrLeft.top === "number") this.scrollTop = optionsOrLeft.top;
    }
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
