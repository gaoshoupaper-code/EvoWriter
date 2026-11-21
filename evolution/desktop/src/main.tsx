import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "@/styles/globals.css";
// 同一份可变字体覆盖界面所需字重，减少安装包的重复字形。
import "misans/lib/Normal/MiSansVF.min.css";
import "@/styles/workspace-readability.css";
import "@/styles/run-readability.css";
import "@/styles/experiment-readability.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
