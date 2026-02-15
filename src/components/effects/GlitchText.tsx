"use client";

interface GlitchTextProps {
  text: string;
  as?: "h1" | "h2" | "h3" | "span" | "p";
  className?: string;
}

export default function GlitchText({ text, as: Tag = "span", className = "" }: GlitchTextProps) {
  return (
    <Tag className={`glitch ${className}`} data-text={text}>
      {text}
    </Tag>
  );
}
