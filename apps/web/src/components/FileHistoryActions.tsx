import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { EyeOff, FileText, MoreHorizontal, Trash2 } from "lucide-react";
import { Icon } from "./Icon";

export function FileHistoryActions({filename, onOpen, onDelete, onIgnore, busy = false}: {
  filename: string; onOpen: () => void; onDelete: () => void; onIgnore?: () => void; busy?: boolean;
}) {
  return <DropdownMenu.Root><DropdownMenu.Trigger asChild>
    <button className="btn ghost xs icon-only" aria-label={`文件操作：${filename}`} title="文件操作" disabled={busy}><Icon icon={MoreHorizontal} size={17} /></button>
  </DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="record-menu" align="end" sideOffset={5}>
    <DropdownMenu.Item onSelect={onOpen}><Icon icon={FileText} size={14} />查看与修订</DropdownMenu.Item>
    {onIgnore && <DropdownMenu.Item onSelect={onIgnore}><Icon icon={EyeOff} size={14} />忽略校验问题</DropdownMenu.Item>}
    <DropdownMenu.Separator />
    <DropdownMenu.Item className="danger" onSelect={onDelete}><Icon icon={Trash2} size={14} />删除整个文件</DropdownMenu.Item>
  </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>;
}
