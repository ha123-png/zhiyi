import { ArrowRight, FileText, CheckCheck, Library } from "lucide-react";
import type { NavigationKey } from "../types";
import { Icon } from "./Icon";
import { Disclosure } from "./Disclosure";

const questions: { title: string; text: string; target: NavigationKey; action: string }[] = [
  { title: "第一次用，先配置什么？", text: "先在设置中启用一个模型方案。处理图片或扫描 PDF 需要支持图片的模型；本地模型服务需要启动并加载模型。云端方案会把本次处理的内容发送给对应服务，激活前请确认。随后选择现有模板，或根据自己的文件创建模板。", target: "settings", action: "设置模型" },
  { title: "模板怎么越用越顺手？", text: "用模板定义要提取的字段。一份字段顺序同时决定表格和卡片，首字段作卡片标题；可拖动，也可聚焦手柄后按上下方向键。补充要求写在额外提示词，必填、范围和计算关系放在程序校验中。内置模板需先复制。点击版本号可查看历史，恢复会将选定版本设为当前，不重复创建版本，也不改旧任务。", target: "templates", action: "查看模板" },
  { title: "数据进了仓库，为什么还提示待核对？", text: "提取结果会先进入仓库；有规则提示或名称需要确认时，来源会标为待核对，导出时也会保留提示。请回到待处理事项核对并确认。直接编辑表格不会自动完成来源核对；导入或合并的独立副本保留当时的状态。仍在处理或等待模板的文件可能还没有数据。", target: "workspace", action: "查看待处理" },
  { title: "长文件会全部交给 AI 吗？", text: "默认提供全部可读取内容；开启发送限制后，从文件开头连续读取到你设置的边界，结果可能缺少后续信息。内部原件始终完整保留，实际范围在来源与处理详情中查看。模型可能拒绝过大的请求，完整发送也不保证提取完整。", target: "settings", action: "查看读取设置" },
  { title: "原件、建议名称和导出副本是什么关系？", text: "内部原件由知意保存，用于预览和追溯。名称建议在同一次提取中生成，默认关闭，有意义的原名会保留。外部副本也是可选项，复制完整文件到指定位置；不会覆盖同名文件，也不会自动改名、移动或删除已经导出的副本。完整原件在文件历史下载。", target: "history", action: "查看文件历史" },
  { title: "表格和卡片会不会各存一份数据？", text: "两种展示使用同一份数据。一条明细对应一张卡片，整份文件字段由同文件的明细共享。仓库后加的列只用于人工补充，不让模型重复提取；分组也不会复制数据。可以直接编辑、筛选并导出 Excel。", target: "tables", action: "打开数据仓库" },
  { title: "换模型或调整设置，会影响旧任务吗？", text: "任务保留当时的模型方案版本和读取设置。普通重试沿用旧设置，明确选择按当前读取设置重试才改变读取范围。旧密钥不可用时会报错，不会悄悄用另一个模型继续处理。", target: "settings", action: "管理模型方案" },
  { title: "升级、换电脑前，需要保留什么？", text: "先导出业务备份，并妥善保存自己导出的文件。备份恢复可保留业务记录和内部原件；外部副本由你独立管理，恢复后需核对本机目录绑定。清除全部本地数据会删除业务记录、内部原件、配置和保存密钥，但不删除外部副本、备份或程序。", target: "backups", action: "管理备份" },
  { title: "如何让其他工具或 AI 使用知意？", text: "接口页提供当前安装位置对应的配置。按需启用查询、事实修改、任务控制或文件访问，文件操作还受允许目录约束。只查询时无需开放写能力。", target: "connections", action: "查看接口与权限" },
];

export function GuidePage({ onNavigate }: { onNavigate: (key: NavigationKey) => void }) {
  return <div className="view guide-page">
    <div className="page-header"><div className="eyebrow">帮助</div><h1>使用说明</h1><p className="support">先完成一份文件，其他能力在需要时再了解。</p></div>
    <div className="guide-start">
      {[
        { icon: FileText, number: "01", title: "告诉知意要什么", text: "选择模板，放入文件。", action: "开始提取", target: "extract" as NavigationKey },
        { icon: CheckCheck, number: "02", title: "核对，再确认", text: "对照原件检查结果，处理需要你决定的事项。", action: "查看待处理", target: "workspace" as NavigationKey },
        { icon: Library, number: "03", title: "积累，也能复用", text: "在仓库阅读、汇总和导出，下一批继续用同一模板。", action: "打开数据仓库", target: "tables" as NavigationKey },
      ].map(step => <section className="card guide-step" key={step.number}><div className="guide-step-marker"><Icon icon={step.icon} size={18} /><span>{step.number}</span></div><h2>{step.title}</h2><p>{step.text}</p><button className="btn ghost sm" onClick={() => onNavigate(step.target)}>{step.action}<Icon icon={ArrowRight} size={13} /></button></section>)}
    </div>
    <div className="card guide-questions"><div className="panel-title"><div><h3>常见问题</h3><p className="support">需要时展开查看，或直接前往对应页面。</p></div></div>{questions.map(q => <Disclosure key={q.title} title={q.title}><p>{q.text}</p><button className="btn ghost sm" onClick={() => onNavigate(q.target)}>{q.action}<Icon icon={ArrowRight} size={13} /></button></Disclosure>)}</div>
    <p className="guide-footnote">模型结果需要核对。知意保留原件与处理记录，帮助你判断和修正。</p>
  </div>;
}
