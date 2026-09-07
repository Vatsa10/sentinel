import Image from "next/image";
import { cn } from "cn";

/** नेत्र (Netra) mark + wordmark, used in the console top bar, sign-in surfaces and landing page. */
export function Brand({ size = "sm" }: { size?: "sm" | "lg" }) {
  const big = size === "lg";
  return (
    <div className={cn("flex items-center gap-2", big ? "gap-3" : "gap-2")}>
      <Image
        src="/netra-mark.svg"
        alt=""
        aria-hidden="true"
        width={big ? 40 : 28}
        height={big ? 40 : 28}
        priority
      />
      <span
        className={cn(
          "font-sans font-semibold tracking-[0.2em] text-text",
          big ? "text-2xl" : "text-sm"
        )}
      >
        NETRA
      </span>
    </div>
  );
}
