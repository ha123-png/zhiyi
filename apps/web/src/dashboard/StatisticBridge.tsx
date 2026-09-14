import { lazy, Suspense, useEffect, useState } from "react";
import type { Analysis, ChartSpec } from "../assistant/types";
import { statisticFromAnalysis } from "./pinAnalysis";
import type { StatisticInput } from "./types";
import "../components/dashboard.css";
const StatisticDialog = lazy(() => import("./StatisticDialog").then(module => ({default:module.StatisticDialog})));

export function StatisticBridge({ onSaved }: { onSaved: () => void }) {
  const [draft, setDraft] = useState<StatisticInput | null>(null);
  useEffect(() => {
    const pin = (event: Event) => {
      const { analysis, spec } = (event as CustomEvent<{ analysis: Analysis; spec?: ChartSpec }>).detail;
      setDraft(statisticFromAnalysis(analysis, spec));
    };
    window.addEventListener("zhiyi:pin-analysis", pin);
    return () => window.removeEventListener("zhiyi:pin-analysis", pin);
  }, []);
  return draft && <Suspense fallback={<div className="app-toast" role="status">正在打开统计设置…</div>}><StatisticDialog initial={draft} onClose={() => setDraft(null)} onSaved={() => { setDraft(null); onSaved(); }} /></Suspense>;
}
