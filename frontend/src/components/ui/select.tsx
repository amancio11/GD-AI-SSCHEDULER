import * as React from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

// Lightweight Select built on native <select> — fully Tailwind v3 compatible.
// Collects <SelectItem> children as <option> elements inside a styled wrapper.

interface SelectProps {
  value?: string;
  onValueChange?: (value: string | null) => void;
  children?: React.ReactNode;
  className?: string;
}

// Extract all SelectItem props from the React tree
function collectOptions(children: React.ReactNode): { value: string; label: React.ReactNode }[] {
  const opts: { value: string; label: React.ReactNode }[] = [];
  React.Children.forEach(children, (child) => {
    if (!React.isValidElement(child)) return;
    const el = child as React.ReactElement<{ value?: string; children?: React.ReactNode }>;
    if ((el.type as { displayName?: string }).displayName === "SelectItem" || (el.type as Function).name === "SelectItem") {
      opts.push({ value: el.props.value ?? "", label: el.props.children });
    } else if (el.props.children) {
      opts.push(...collectOptions(el.props.children));
    }
  });
  return opts;
}

function Select({ value = "", onValueChange, children, className }: SelectProps) {
  const opts = collectOptions(children);
  return (
    <div className={cn("relative", className)}>
      <select
        value={value}
        onChange={(e) => onValueChange?.(e.target.value)}
        className="flex h-9 w-full items-center rounded-md border border-input bg-transparent pl-3 pr-8 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring appearance-none"
      >
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {typeof o.label === "string" ? o.label : o.value}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 opacity-50" />
    </div>
  );
}

// These are "phantom" components — their props are consumed by Select above
// but they render nothing themselves (the parent reads them as data).

interface SelectTriggerProps extends React.HTMLAttributes<HTMLDivElement> {
  size?: "sm" | "default";
}
function SelectTrigger({ children }: SelectTriggerProps) { return null; }
SelectTrigger.displayName = "SelectTrigger";

function SelectValue({ placeholder }: { placeholder?: string }) { return null; }
SelectValue.displayName = "SelectValue";

function SelectContent({ children }: { children?: React.ReactNode }) { return <>{children}</>; }
SelectContent.displayName = "SelectContent";

interface SelectItemProps { value: string; children?: React.ReactNode; }
function SelectItem({ children }: SelectItemProps) { return null; }
SelectItem.displayName = "SelectItem";

function SelectGroup({ children }: { children?: React.ReactNode }) { return <>{children}</>; }
SelectGroup.displayName = "SelectGroup";

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem, SelectGroup };
