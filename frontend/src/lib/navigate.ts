/** Opens `url` the way the app's links do (components/Link.tsx): through
 *  the router, or, in a browser automation drives (navigator.webdriver), as
 *  a page load. A router navigation fetches the next page's data from the
 *  running page, which the visit counter reads as a browser running the page
 *  (lib/visitSignal.ts); a page load is held and never confirmed. */
export function navigate(router: { push: (url: string) => void }, url: string): void {
  if (typeof navigator !== "undefined" && navigator.webdriver) {
    window.location.assign(url);
    return;
  }
  router.push(url);
}
