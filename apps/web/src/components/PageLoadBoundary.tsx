import { Component, type ReactNode } from "react";

// Keep the navigation usable if a page module cannot be loaded. A fresh document
// also clears the browser's cached rejected module request on explicit retry.
export class PageLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <div className="view">
        <div className="callout danger" role="alert">
          <p>页面暂时无法打开。可以先切换到其他页面，或重新载入知意。</p>
          <button type="button" className="btn secondary" onClick={() => window.location.reload()}>重新载入知意</button>
        </div>
      </div>;
    }
    return this.props.children;
  }
}
