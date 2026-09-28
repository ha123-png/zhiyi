import { useEffect, useState } from "react";
import { ArrowRight, FileText, CheckCheck, Library } from "lucide-react";
import type { NavigationKey } from "../types";
import { getGuideIndex, getGuideTopic, type GuideTopic, type GuideContent } from "../api";
import { Icon } from "./Icon";
import { Disclosure } from "./Disclosure";

function Topic({ topic, onNavigate }: { topic: GuideTopic; onNavigate: (key: NavigationKey) => void }) {
  const [content, setContent] = useState<GuideContent | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load() {
    if (content || loading) return;
    setLoading(true); setError("");
    try { setContent(await getGuideTopic(topic.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "手册暂时无法读取"); }
    finally { setLoading(false); }
  }
  return <Disclosure title={topic.title} onOpen={() => void load()}>
    {loading && <p className="support" role="status">正在读取…</p>}
    {error && <div role="alert"><p>{error}</p><button className="btn secondary sm" onClick={() => void load()}>重新读取</button></div>}
    {content?.sections.map(section => <section key={section.title}>
      <h4>{section.title}</h4>{section.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
    </section>)}
    <button className="btn ghost sm" onClick={() => onNavigate(topic.target)}>{topic.action}<Icon icon={ArrowRight} size={13} /></button>
  </Disclosure>;
}

export function GuidePage({ onNavigate }: { onNavigate: (key: NavigationKey) => void }) {
  const [topics, setTopics] = useState<GuideTopic[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError("");
    getGuideIndex().then(value => { if (!cancelled) setTopics(value.topics); })
      .catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : "手册暂时无法读取"); });
    return () => { cancelled = true; };
  }, [reload]);
  return <div className="view guide-page">
    <div className="page-header"><div className="eyebrow">帮助</div><h1>使用说明</h1><p className="support">一份原件，一份当前数据，一条修订历史。需要时按主题查看。</p></div>
    <div className="guide-start">
      {[
        { icon: FileText, number: "01", title: "告诉知意要什么", text: "选择或创建模板，也可用自主提取处理尚未固定字段的文件。", action: "打开文件提取", target: "extract" as NavigationKey },
        { icon: CheckCheck, number: "02", title: "核对与修订", text: "对照原件检查当前数据，处理校验或归档中需要决定的事项。", action: "打开状态监控", target: "workspace" as NavigationKey },
        { icon: Library, number: "03", title: "积累与复用", text: "在仓库阅读、修订、汇总和导出，同一数据也可交给问知意分析。", action: "打开数据仓库", target: "tables" as NavigationKey },
      ].map(step => <section className="card guide-step" key={step.number}><div className="guide-step-marker"><Icon icon={step.icon} size={18} /><span>{step.number}</span></div><h2>{step.title}</h2><p>{step.text}</p><button className="btn ghost sm" onClick={() => onNavigate(step.target)}>{step.action}<Icon icon={ArrowRight} size={13} /></button></section>)}
    </div>
    <div className="card guide-questions"><div className="panel-title"><div><h3>知意使用手册</h3><p className="support">这里与问知意查阅的是同一份说明。</p></div></div>
      {error ? <div role="alert"><p>{error}</p><button className="btn secondary sm" onClick={() => setReload(value => value + 1)}>重新读取</button></div>
        : topics.length ? topics.map(topic => <Topic topic={topic} onNavigate={onNavigate} key={topic.id} />) : <p role="status">正在读取目录…</p>}
    </div>
    <p className="guide-footnote">模型结果需要核对。原件、当前数据和修订记录帮助你判断与修正。</p>
  </div>;
}
