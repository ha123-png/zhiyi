import { useCallback, useState } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal, Trash2 } from "lucide-react";
import { Icon } from "./Icon";

export function DetailRowAction({index, disabled, onDelete}: {index: number; disabled?: boolean; onDelete: () => void}) {
  const [container, setContainer] = useState<HTMLElement | undefined>();
  const locate = useCallback((node: HTMLButtonElement | null) => setContainer(node?.closest("dialog") ?? undefined), []);
  return <DropdownMenu.Root><DropdownMenu.Trigger asChild>
    <button ref={locate} className="detail-row-action" disabled={disabled}
      aria-label={`明细 ${index} 操作`} title={`明细 ${index} 操作`}>
      <span>{index}</span><Icon icon={MoreHorizontal} size={13} />
    </button>
  </DropdownMenu.Trigger><DropdownMenu.Portal container={container}>
    <DropdownMenu.Content className="record-menu" align="start" sideOffset={4}>
      <DropdownMenu.Item className="danger" onSelect={onDelete}><Icon icon={Trash2} size={14} />删除此明细</DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu.Portal></DropdownMenu.Root>;
}
