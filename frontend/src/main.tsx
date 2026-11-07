import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles/global.css";
import "./styles/chat-first.css";
import "./styles/product-foundation.css";

const root = document.getElementById("root");
if (!root) throw new Error("Application root element is missing");

createRoot(root).render(<StrictMode><BrowserRouter><App /></BrowserRouter></StrictMode>);
