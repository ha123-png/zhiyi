export function deletionNotice(result: { pending_cleanup?: number }, completed: string): string {
  return result.pending_cleanup
    ? `记录与关联数据已删除；${result.pending_cleanup} 份内部原件尚未清理，知意会自动重试。请关闭占用文件的其他程序。`
    : completed;
}
