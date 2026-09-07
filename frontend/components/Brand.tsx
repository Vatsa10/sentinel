import { cn } from "cn";

/** नेत्र (Netra) mark + wordmark, used in the console top bar and sign-in surfaces. */
export function Brand({ size = "sm" }: { size?: "sm" | "lg" }) {
  const big = size === "lg";
  return (
    <div className={cn("flex items-center gap-2", big ? "gap-3" : "gap-2")}>
      <span
        className={cn(
          "font-sans font-bold text-accent",
          big ? "text-3xl" : "text-lg"
        )}
        lang="hi"
      >
        नेत्र
      </span>
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
