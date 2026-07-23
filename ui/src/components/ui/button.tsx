import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import type * as React from "react"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] text-sm font-semibold transition-all duration-200 outline-none disabled:pointer-events-none disabled:opacity-40 active:translate-y-px [&_svg]:size-4",
  {
    variants: {
      variant: {
        default: "bg-signal text-white shadow-[0_8px_22px_-14px_rgba(66,84,60,0.5)] hover:bg-[#4f624a]",
        secondary: "border border-line-strong bg-panel text-ink hover:border-[#a5aea0] hover:bg-panel-raised",
        ghost: "text-muted hover:bg-black/[0.045] hover:text-ink",
        danger: "border border-red-700/20 bg-red-50 text-red-800 hover:bg-red-100",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 rounded-lg px-3 text-xs",
        icon: "size-9 rounded-lg",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
)

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : "button"
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
