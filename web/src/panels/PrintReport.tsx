import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useStore } from "@/store";
import { fmtMass } from "@/three/format";
import { fmt, prettyUnit } from "./Charts";

/** A plain, print-only rendering of the result (window.print()). */
export function PrintReport() {
  const s = useStore();
  if (!s.design) return null;
  const tests = s.testOrder.map((id) => s.tests[id]?.result).filter(Boolean);
  return (
    <article className="print-only p-8 text-[11pt] leading-snug text-black">
      <h1 className="text-2xl font-bold">{s.design.name}</h1>
      {s.design.description && <p className="mt-1 text-neutral-700">{s.design.description}</p>}
      <p className="mt-1 text-xs text-neutral-500">Margin report · {new Date().toLocaleString()}</p>
      {s.answer && <div className="mt-4 [&_li]:ml-5 [&_li]:list-disc"><Markdown remarkPlugins={[remarkGfm]}>{s.answer}</Markdown></div>}
      <h2 className="mt-6 text-lg font-semibold">Real-world tests</h2>
      <table className="mt-2 w-full border-collapse text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">Test</th><th>Result</th><th>Status</th><th>Verification</th></tr></thead>
        <tbody>
          {tests.map((t) => t && (
            <tr key={t.id} className="border-b align-top">
              <td className="py-1 pr-2 font-medium">{t.title}</td>
              <td className="pr-2">{t.headline}{t.metrics.map((m) => ` · ${m.label} ${fmt(m.value)} ${prettyUnit(m.unit)} (${m.kind === "min" ? "≥" : "≤"} ${fmt(m.limit)})`).join("")}</td>
              <td className="pr-2 uppercase">{t.status}</td>
              <td>{t.sim?.verification.summary ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 className="mt-6 text-lg font-semibold">Bill of materials</h2>
      <table className="mt-2 w-full border-collapse text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">Part</th><th>Material</th><th>Qty</th><th className="text-right">Mass</th><th className="text-right">Cost</th></tr></thead>
        <tbody>
          {s.summary?.parts.map((r) => (
            <tr key={r.id} className="border-b"><td className="py-1">{r.name}</td><td>{r.material}</td><td>{r.quantity}</td><td className="text-right">{fmtMass(r.mass_g)}</td><td className="text-right">${r.cost.toFixed(2)}</td></tr>
          ))}
        </tbody>
      </table>
      {s.design.assembly.length > 0 && (
        <>
          <h2 className="mt-6 text-lg font-semibold">Assembly</h2>
          <ol className="mt-2 list-decimal pl-6">{s.design.assembly.map((a, i) => <li key={i}>{a.action}</li>)}</ol>
        </>
      )}
    </article>
  );
}
