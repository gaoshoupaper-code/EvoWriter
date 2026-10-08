import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "@/styles/globals.css";
// MiSans 界面字体(分片 woff2 按需加载,见 DESIGN-UI.md)
import "misans/lib/Normal/MiSans-Regular.min.css";
import "misans/lib/Normal/MiSans-Medium.min.css";
import "misans/lib/Normal/MiSans-Semibold.min.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
