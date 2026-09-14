import { ArrowRight, FileText, CheckCheck, Library } from "lucide-react";
import type { NavigationKey } from "../types";
import { Icon } from "./Icon";
import { Disclosure } from "./Disclosure";

const questions: { title: string; text: string; target: NavigationKey; action: string }[] = [
  { title: "表有很多数据，问知意会全部读取吗？", text: "不会把整张表塞进对话。默认提供最多三条明细或分组预览，长字段控制长度；需要时再按字段、筛选和排序读取。统计覆盖完整授权范围，图表使用完整分析快照。处理过程与较长文字说明默认收起。对话历史分页加载，可搜索更早的对话；停止、短暂断线或重启后，已保存内容仍可继续查看。", target: "assistant", action: "打开问知意" },
  { title: "问知意能帮我做什么？会把数据发到哪里？", text: "可以直接提问，也可以从仓库、任务或模板页面打开侧栏，附加当前范围，让它查询、分析、画图，或解释模板的字段与规则。问知意单独选择模型，不沿用提取方案。使用远程模型前需授权发送问题、历史及所选资料；同一对话、方案与范围内无需逐句确认，可在对话设置中撤销。默认先预览修改，确认后执行；明确选择“本次委托”才会在下一次发送中自动执行授权内的部分修改。模板的创建、修改和版本恢复统一在模板页完成。图表保留生成时的数值与来源，可按最新数据重新分析。", target: "assistant", action: "打开问知意" },
  { title: "第一次用，先配置什么？", text: "先确定希望从文件中得到哪些信息，选择现有模板或创建自己的模板。再到设置中选择本地或云端模型方案；文本可用文本模型，图片和扫描件需要视觉能力，本地服务还需启动并加载模型。用一份文件检查提取结果、规则提示与原件，再继续处理同类文件。远程模型会接收本次读取范围内的内容。", target: "settings", action: "设置模型" },
  { title: "模板怎么越用越顺手？", text: "用模板定义要提取的字段。一份字段顺序同时决定表格和卡片，首字段作卡片标题；可拖动，也可聚焦手柄后按上下方向键。补充要求写在额外提示词，必填、范围和计算关系放在程序校验中。内置模板需先复制。新建和复制的模板默认不参与智能匹配，准备好后在提取页勾选匹配范围。点击版本号可查看历史，恢复会将选定版本设为当前，不重复创建版本，也不改旧任务。", target: "templates", action: "查看模板" },
  { title: "数据进了仓库，为什么还提示待核对？", text: "提取结果会先进入仓库；有规则提示或提取结果需要核对时，来源会标为待核对，导出时也会保留提示。请到状态监控找到待处理事项，核对并确认。直接编辑表格不会自动完成来源核对；导入或合并的独立副本保留当时的状态。仍在处理或等待模板的文件可能还没有数据。", target: "workspace", action: "打开状态监控" },
  { title: "长文件会全部交给 AI 吗？", text: "默认提供全部可读取内容；开启发送限制后，从文件开头连续读取到你设置的边界，结果可能缺少后续信息。内部原件始终完整保留，实际范围在来源与处理详情中查看。模型可能拒绝过大的请求，完整发送也不保证提取完整。", target: "settings", action: "查看读取设置" },
  { title: "原件、内容名称和导出副本是什么关系？", text: "内部原件由知意保存，用于预览和追溯。开启“文件命名”后，选择 AI 根据内容建议，或固定的“导入日期_模板名_序号”。旧模板保持 AI 方式，可在额外要求中表达命名偏好；固定方式的同日同模板序号递增，重试沿用名称。名称自动生效，仍可编辑，上传原名保留。外部副本也是可选项，复制完整文件到指定位置；不会覆盖同名文件，也不会自动改名、移动或删除已经导出的副本。完整原件在文件历史下载。", target: "history", action: "查看文件历史" },
  { title: "表格和卡片会不会各存一份数据？", text: "两种展示使用同一份数据。一条明细对应一张卡片，整份文件字段由同文件的明细共享。仓库后加的列只用于人工补充，不让模型重复提取；分组也不会复制数据。可以直接编辑、筛选并导出 Excel。", target: "tables", action: "打开数据仓库" },
  { title: "仪表盘与问知意中的图表有什么区别？", text: "问知意保留当时的分析快照，便于回看依据。数据仪表盘按保存的统计配置读取当前数据；你可以选择一张表，查看记录数、合计或平均值，并按类别或日期分组。最多保存四张自定义统计。问知意中符合这些简单条件的分析，可以通过“数据与来源”里的“添加到仪表盘”预览并保存。删除统计卡不会删除原表数据。", target: "dashboard", action: "打开数据仪表盘" },
  { title: "换模型或调整设置，会影响旧任务吗？", text: "任务保留当时的模型方案版本和读取设置。方案高级设置中的上下文预算可单独调整，云端不继承本地加载容量；本地预算应与实际加载容量一致。普通重试沿用旧设置，明确选择按当前读取设置重试才改变读取范围。旧密钥不可用时会报错，不会悄悄用另一个模型继续处理。", target: "settings", action: "管理模型方案" },
  { title: "升级、换电脑前，需要保留什么？", text: "先导出业务备份，并妥善保存自己导出的文件。备份恢复可保留业务记录和内部原件；外部副本由你独立管理，恢复后需核对本机目录绑定。清除全部本地数据会删除业务记录、内部原件、配置和保存密钥，但不删除外部副本、备份或程序。", target: "backups", action: "管理备份" },
  { title: "如何让其他工具或 AI 使用知意？", text: "接口页提供当前安装位置对应的配置。按需启用查询、事实修改、任务控制或文件访问，文件操作还受允许目录约束。只查询时无需开放写能力。", target: "connections", action: "查看接口与权限" },
];

export function GuidePage({ onNavigate }: { onNavigate: (key: NavigationKey) => void }) {
  return <div className="view guide-page">
    <div className="page-header"><div className="eyebrow">帮助</div><h1>使用说明</h1><p className="support">从一份文件开始，让模板、数据与原件一起积累。</p></div>
    <div className="guide-start">
      {[
        { icon: FileText, number: "01", title: "告诉知意要什么", text: "选择或创建模板，定义字段与理解要求，再放入文件。", action: "打开文件提取", target: "extract" as NavigationKey },
        { icon: CheckCheck, number: "02", title: "核对，再确认", text: "对照原件检查结果，处理需要你决定的事项。", action: "打开状态监控", target: "workspace" as NavigationKey },
        { icon: Library, number: "03", title: "积累，也能复用", text: "在仓库阅读、汇总和导出，下一批继续用同一模板。", action: "打开数据仓库", target: "tables" as NavigationKey },
      ].map(step => <section className="card guide-step" key={step.number}><div className="guide-step-marker"><Icon icon={step.icon} size={18} /><span>{step.number}</span></div><h2>{step.title}</h2><p>{step.text}</p><button className="btn ghost sm" onClick={() => onNavigate(step.target)}>{step.action}<Icon icon={ArrowRight} size={13} /></button></section>)}
    </div>
    <div className="card guide-questions"><div className="panel-title"><div><h3>常见问题</h3><p className="support">需要时展开查看，或直接前往对应页面。</p></div></div>{questions.map(q => <Disclosure key={q.title} title={q.title}><p>{q.text}</p><button className="btn ghost sm" onClick={() => onNavigate(q.target)}>{q.action}<Icon icon={ArrowRight} size={13} /></button></Disclosure>)}</div>
    <p className="guide-footnote">模型结果需要核对。知意保留原件与处理记录，帮助你判断和修正。</p>
  </div>;
}
