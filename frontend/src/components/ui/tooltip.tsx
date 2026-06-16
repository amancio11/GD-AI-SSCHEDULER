import * as React from "react";
import { cn } from "@/lib/utils";

// Lightweight tooltip built on CSS :hover — no Radix dependency.

function TooltipProvider({ children }: { children: React.ReactNode; delay?: number }) {
  return <>{children}</>;
}

function Tooltip({ children }: { children: React.ReactNode }) {
  return <div className="relative inline-flex group">{children}</div>;
}

function TooltipTrigger({ children, asChild }: { children: React.ReactNode; asChild?: boolean }) {
  return <span className="inline-flex">{children}</span>;
}

function TooltipContent({
  children,
  className,
  side = "top",
  sideOffset,
  align,
  alignOffset,
  ...props
}: {
  children: React.ReactNode;
  className?: string;
  side?: string;
  sideOffset?: number;
  align?: string;
  alignOffset?: number;
} & React.HTMLAttributes<HTMLDivElement>) {
  const posClass =
    side === "bottom"
      ? "top-full mt-1.5"
      : side === "left"
      ? "right-full mr-1.5"
      : side === "right"
      ? "left-full ml-1.5"
      : "bottom-full mb-1.5";

  return (
    <div
      className={cn(
        "pointer-events-none absolute z-50 hidden group-hover:block rounded-md bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95",
        posClass,
        "left-1/2 -translate-x-1/2",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
