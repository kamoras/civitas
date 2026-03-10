import React from "react";

export default function TerminalTitlebar({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="terminal-titlebar" aria-hidden="true">
      <span className="terminal-dot red" />
      <span className="terminal-dot yellow" />
      <span className="terminal-dot green" />
      <span className="ml-3 text-white/40 text-xs font-terminal">{title}</span>
      {children}
    </div>
  );
}
