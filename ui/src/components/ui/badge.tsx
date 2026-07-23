import { cva, type VariantProps } from "class-variance-authority"
import type * as React from "react"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-1 text-[10px] font-bold uppercase leading-none tracking-[0.11em]",
  {
    variants: {
      variant: {
        default: "border-signal/25 bg-signal-soft text-signal",
        neutral: "border-line-strong bg-black/[0.035] text-muted",
        success: "border-emerald-700/20 bg-emerald-50 text-emerald-800",
        warning: "border-amber-700/20 bg-amber-50 text-amber-800",
        danger: "border-red-700/20 bg-red-50 text-red-800",
        blue: "border-blue-700/20 bg-blue-50 text-blue-800",
        violet: "border-violet-700/20 bg-violet-50 text-violet-800",
        teal: "border-teal-700/20 bg-teal-50 text-teal-800",
      },
    },
    defaultVariants: { variant: "default" },
  },
)

export function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
