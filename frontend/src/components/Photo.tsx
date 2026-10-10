"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * A photo from the site's own `/photo/…` route (lib/photos.ts), or
 * `fallback` when there is none to show: the route answers 404 for a
 * member bioguide has no portrait for, and a broken-image icon is not a
 * picture of anyone.
 */
export default function Photo({
  src,
  alt,
  className,
  fallback = null,
  lazy = false,
}: {
  src: string;
  alt: string;
  className?: string;
  fallback?: ReactNode;
  lazy?: boolean;
}) {
  const ref = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);
  // An image that failed before hydration fired its error event before
  // React was listening.
  useEffect(() => {
    const img = ref.current;
    if (img?.complete && img.naturalWidth === 0) setFailed(true);
  }, []);
  if (failed) return <>{fallback}</>;
  return (
    // Not next/image: the /photo route is already cached by nginx, and the
    // optimizer would add a second, uncached copy. onError is a load event,
    // not an interaction.
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/no-noninteractive-element-interactions
    <img
      ref={ref}
      src={src}
      alt={alt}
      className={className}
      loading={lazy ? "lazy" : undefined}
      onError={() => setFailed(true)}
    />
  );
}
