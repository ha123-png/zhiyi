import { ArrowRight, Bot, Braces, Database, FileCheck2, FileInput, FileText, History, Layers3, Plug, Settings2, ShieldCheck, TableProperties, Upload } from "lucide-react";
import type { NavigationKey } from "../types";
import { Icon } from "./Icon";

const core: Array<{ icon: typeof Upload; title: string; text: string; target: NavigationKey; action: string }> = [
  { icon: Bot, title: "1. 启用视觉模型", text: "本地使用可连接 LM Studio 或 Ollama，并在设置中启动服务、加载支持图片的模型；也可以配置兼容的云端视觉模型。", target: "settings", action: "配置视觉模型" },
  { icon: FileText, title: "2. 准备字段模板", text: "模板定义最终要得到哪些表头字段和明细列。发票、送货单可直接使用内置模板；其他业务文件可手工创建，也可让 AI 根据样例生成草稿。", target: "templates", action: "管理模板" },
  { icon: Upload, title: "3. 上传并提取", text: "明确知道用途时直接指定模板；混合文件可使用智能匹配。系统会按任务顺序连续处理，不需要手工守着模型。", target: "extract", action: "开始提取" },
  { icon: FileCheck2, title: "4. 核对并确认", text: "结果先经过字段类型、必填项和业务规则校验。有问题时对照原文件修改；只有确认后的数据才进入仓库。", target: "history", action: "查看待确认" },
  { icon: Database, title: "5. 汇总与交付", text: "在数据仓库合并、筛选、补充备注列，并按当前表头与合并关系导出 XLSX；桌面版可固定默认导出文件夹。", target: "tables", action: "打开数据仓库" },
];

const features: Array<{ icon: typeof Upload; title: string; text: string; target: NavigationKey; action: string }> = [
  { icon: Layers3, title: "智能匹配预选池", text: "只让模型在用途明确、互不混淆的模板中选择。无法确定时任务停在待选模板，不会悄悄写入错误的数据表。", target: "settings", action: "配置预选池" },
  { icon: TableProperties, title: "模板与附加列", text: "模板字段参与模型提取；数据表中后加的列只用于备注或业务补充，可选择按表头合并或按明细逐行填写，不污染原始提取记录。", target: "templates", action: "查看模板" },
  { icon: History, title: "文件历史与追溯", text: "查看每份文件的状态、耗时、所用模型、原始结果、人工修改和确认记录。失败任务可重试，重试会重新计时。", target: "history", action: "打开历史" },
  { icon: FileInput, title: "导入、合并与导出", text: "导入表格时按字段取并集，不同字段不会静默丢失；重复表头可以保留。导出 XLSX 会保留列色、列宽和表头合并关系。", target: "tables", action: "管理数据" },
  { icon: ShieldCheck, title: "备份与数据边界", text: "本地方案中文件不离开电脑；激活云端方案前必须确认数据传输。重要业务数据可导出备份并在新环境恢复。", target: "backups", action: "查看备份" },
  { icon: Plug, title: "模型方案", text: "LM Studio、Ollama 适合本地隐私处理；云端模型通常更快或更强。不同方案可独立保存，每个任务会记录实际使用的方案版本。", target: "settings", action: "管理方案" },
  { icon: Braces, title: "MCP 与外部接口", text: "需要让其他工具调用知意时，到接口页复制按当前安装位置动态生成的配置，并按需开启读取、任务控制、写入或文件访问权限。", target: "connections", action: "查看接口" },
  { icon: Settings2, title: "文件格式与桌面设置", text: "支持 PDF、常见图片及开启转换后的 Word、Excel和文本。桌面版可设置导出目录、主题和启动引导；浏览器版沿用浏览器下载规则。", target: "settings", action: "打开设置" },
];

export function GuidePage({ onNavigate }: { onNavigate: (key: NavigationKey) => void }) {
  const navigate = (target: NavigationKey, title: string) => {
    if (title === "智能匹配预选池") sessionStorage.setItem("zhiyi-settings-anchor", "smart-pool-settings");
    onNavigate(target);
  };
  return <div className="view guide-page">
    <div className="page-header"><div className="eyebrow">使用说明</div><h1>先走通核心链路，再按需使用高级功能</h1><div className="support">所有功能都围绕“文件 → 结构化结果 → 人工确认 → 数据仓库”展开。</div></div>
    <section className="guide-section"><div className="section-heading"><div><div className="eyebrow">核心链路</div><h2>第一次使用，按这五步完成</h2></div></div>
      <div className="guide-flow">{core.map((step) => <article className="card guide-step" key={step.title}><div className="guide-step-icon"><Icon icon={step.icon} size={20} /></div><div><h3>{step.title}</h3><p>{step.text}</p><button className="btn secondary sm" type="button" onClick={() => onNavigate(step.target)}>{step.action}<Icon icon={ArrowRight} size={13} /></button></div></article>)}</div>
    </section>
    <section className="guide-section"><div className="section-heading"><div><div className="eyebrow">重要功能</div><h2>知道这些，才算完整使用知意</h2></div></div>
      <div className="guide-feature-grid">{features.map((feature) => <article className="card guide-feature" key={feature.title}><div className="guide-step-icon"><Icon icon={feature.icon} size={19} /></div><div><h3>{feature.title}</h3><p>{feature.text}</p><button className="btn ghost sm" type="button" onClick={() => navigate(feature.target, feature.title)}>{feature.action}<Icon icon={ArrowRight} size={13} /></button></div></article>)}</div>
    </section>
  </div>;
}
