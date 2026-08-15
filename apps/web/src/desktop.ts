declare global {
  interface Window {
    pywebview?: {
      api?: {
        get_export_directory(): Promise<string>;
        choose_export_directory(): Promise<string | null>;
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
