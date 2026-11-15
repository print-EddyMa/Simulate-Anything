import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type St = "pass" | "fail" | "unverified" | "error" | "pending" | "warn" | "skip";

const LABEL: Record<St, string> = {
  pass: "Passed", fail: "Failed", unverified: "Unverified", error: "Error", pending: "Running", warn: "Warning", skip: "Not applicable",
};

export function StatusIcon({ status, className }: { status: St; className?: string }) {
  const base = "grid shrink-0 place-items-center rounded-full";
  if (status === "pending") {
    return <span className={cn(base, "size-5 text-primary", className)} role="img" aria-label={LABEL.pending}><Loader2 className="size-4 animate-spin" /></span>;
  }
  if (status === "skip") {
    return <span className={cn(base, "size-5 border border-dashed border-muted-foreground/50", className)} role="img" aria-label={LABEL.skip} />;
  }
  const tone = status === "pass" ? "bg-good/15 text-good" : status === "warn" || status === "unverified" ? "bg-warn/15 text-warn" : "bg-bad/15 text-bad";
  const Icon = status === "pass" ? Check : status === "warn" || status === "unverified" ? AlertTriangle : X;
  return (
    <span className={cn(base, "size-5", tone, className)} role="img" aria-label={LABEL[status]}>
      <Icon className="size-3.5" strokeWidth={2.75} />
    </span>
  );
}

export function statusText(s: St) {
  return LABEL[s];
}
