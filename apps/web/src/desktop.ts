export interface NativeImportFile { token: string; name: string; size: number; type: string }

declare global {
  interface Window {
    pywebview?: {
      api?: {
        get_export_directory(): Promise<string>;
        choose_export_directory(): Promise<string | null>;
        choose_import_files?(): Promise<NativeImportFile[]>;
        choose_folder?(): Promise<string | null>;
        open_export_folder?(taskId: string): Promise<string>;
        export_table(url: string, filename: string): Promise<string>;
        export_template(filename: string, content: string): Promise<string>;
        download_task_file(url: string, filename: string): Promise<string>;
      };
    };
  }
}

export function desktopApi() {
  return window.pywebview?.api ?? null;
}

export function isDesktopApp(): boolean {
  return desktopApi() !== null;
}
