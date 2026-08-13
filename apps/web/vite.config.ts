import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // 开发模式下把 /api 转发到本地 FastAPI（8010），避免浏览器直接拿到 SPA 的 index.html
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        // http-proxy 会把被代理的长连接（SSE）的 Connection 头追加 close，
        // 浏览器 EventSource 读到 Connection: close 立即断开，前端被迫退化为轮询。
        // 这里对 text/event-stream 强制覆盖为 keep-alive，保持实时推送。
        configure: (proxy) => {
          (proxy as unknown as {
            on: (
              event: "proxyRes",
              listener: (proxyRes: {
                headers: Record<string, string | string[] | undefined>;
              }) => void,
            ) => void;
          }).on("proxyRes", (proxyRes) => {
            const contentType = proxyRes.headers["content-type"];
            if (
              typeof contentType === "string" &&
              contentType.includes("text/event-stream")
            ) {
              proxyRes.headers["connection"] = "keep-alive";
            }
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
