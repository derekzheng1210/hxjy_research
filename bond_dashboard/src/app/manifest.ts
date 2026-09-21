import type { MetadataRoute } from "next";
import { BASE_PATH } from "@/lib/base-path";

/** PWA 应用清单（供浏览器安装到桌面 / 开始菜单） */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "债用债一级投资看板",
    short_name: "债券看板",
    description:
      "债券一级市场投资看板：每日发行、发行结果、参与/中标、上市提醒、收益率曲线与利差分析",
    start_url: BASE_PATH + "/",
    scope: BASE_PATH + "/",
    display: "standalone",
    background_color: "#f5f6fa",
    theme_color: "#1e40af",
    lang: "zh-CN",
    icons: [
      { src: BASE_PATH + "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: BASE_PATH + "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: BASE_PATH + "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
