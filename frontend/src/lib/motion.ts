/**
 * "smooth", unless the reader has asked for less motion: then the jump is
 * instant. Every scripted scroll goes through this. CSS animations stop under
 * prefers-reduced-motion in globals.css, but a scrollTo/scrollIntoView with
 * behavior "smooth" is JavaScript and ignored that (WCAG 2.3.3).
 */
export function scrollBehavior(): ScrollBehavior {
  if (typeof window === "undefined" || !window.matchMedia) return "auto";
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
