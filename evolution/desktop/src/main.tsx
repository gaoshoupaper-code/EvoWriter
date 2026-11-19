import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "@/styles/globals.css";
// 同一份可变字体覆盖界面所需字重，减少安装包的重复字形。
import "misans/lib/Normal/MiSansVF.min.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
