import * as React from "react";
import { cn } from "../../lib/utils";
const Input = React.forwardRef(({
  className,
  type,
  ...props
}, ref) => {
  return <input type={type} className={cn("flex min-h-11 w-full min-w-0 rounded-md border border-input bg-background px-3 py-2.5 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50", className)} ref={ref} {...props} />;
});
Input.displayName = "Input";
export { Input };
