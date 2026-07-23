import type * as React from "react"

import { cn } from "@/lib/utils"

export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-[10px] border border-line-strong bg-panel px-3 text-sm text-ink outline-none transition placeholder:text-dim focus:border-signal/55 focus:ring-2 focus:ring-signal/10",
        className,
      )}
      {...props}
    />
  )
}

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-28 w-full resize-y rounded-[12px] border border-line-strong bg-panel px-3.5 py-3 text-sm leading-6 text-ink outline-none transition placeholder:text-dim focus:border-signal/55 focus:ring-2 focus:ring-signal/10",
        className,
      )}
      {...props}
    />
  )
}
